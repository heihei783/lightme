"""
Planner-Executor runtime helpers for LightMe.

This module keeps plan validation, budget checks, session isolation and
structured trace persistence outside the LangGraph node definitions.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from utils.config_handler import config_ai
from utils.path_tool import get_abs_path


TERMINAL_STATUSES = {"completed", "failed", "skipped"}
ACTIVE_STATUSES = {"pending", "retry", "adjust"}
EXECUTION_STATUSES = TERMINAL_STATUSES | ACTIVE_STATUSES | {"running"}
TASK_TYPES = {"research", "read", "write", "execute", "browse", "analyze", "create", "verify", "general"}
RISK_LEVELS = {"low", "medium", "high"}
TOOL_POLICY_BY_TASK_TYPE = {
    "research": {"knowledge_search", "web_search", "search_files", "read_file_content", "get_file_info", "firecrawl_search", "firecrawl_scrape"},
    "read": {"knowledge_search", "read_file_content", "list_directory", "search_files", "get_file_info"},
    "write": {"write_file_content", "read_file_content", "list_directory", "search_files", "get_file_info", "copy_file", "make_directory"},
    "execute": {"execute_python_code", "execute_shell_command", "read_file_content", "write_file_content", "list_directory", "search_files", "get_file_info"},
    "browse": {"open_url", "web_search", "firecrawl_search", "firecrawl_scrape", "midscene_act", "midscene_flow", "midscene_query"},
    "analyze": {"knowledge_search", "read_file_content", "list_directory", "search_files", "get_file_info", "execute_python_code", "web_search"},
    "create": {"write_file_content", "read_file_content", "make_directory", "copy_file", "execute_python_code"},
    "verify": {"read_file_content", "list_directory", "search_files", "get_file_info", "execute_python_code", "execute_shell_command"},
    "general": set(),
}
ALWAYS_AVAILABLE_TOOLS = {"get_system_info", "get_disk_usage"}
HIGH_RISK_TOOLS = {"execute_shell_command", "start_app", "open_path", "move_file"}


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(frozen=True)
class RuntimeBudget:
    max_steps: int
    max_runtime_seconds: int
    max_tokens: int
    planner_enabled: bool
    planner_parallelism: int
    trace_enabled: bool
    eval_mode: bool

    @classmethod
    def from_config(cls) -> "RuntimeBudget":
        return cls(
            max_steps=max(1, _safe_int(config_ai.get("agent_max_steps"), 40)),
            max_runtime_seconds=max(5, _safe_int(config_ai.get("agent_max_runtime_seconds"), 180)),
            max_tokens=max(1000, _safe_int(config_ai.get("agent_max_tokens"), 8000)),
            planner_enabled=_safe_bool(config_ai.get("planner_enabled"), True),
            planner_parallelism=max(1, _safe_int(config_ai.get("planner_parallelism"), 2)),
            trace_enabled=_safe_bool(config_ai.get("trace_enabled"), True),
            eval_mode=_safe_bool(config_ai.get("eval_mode"), False),
        )


def extract_json_object(text: str) -> Dict[str, Any]:
    """Parse a model JSON response, tolerating fenced output and surrounding text."""
    candidate = (text or "").strip()
    if "```json" in candidate:
        candidate = candidate.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in candidate:
        candidate = candidate.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and start < end:
        parsed = json.loads(candidate[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("model output does not contain a JSON object")


def _normalize_dep_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    deps: List[str] = []
    for item in raw:
        dep = str(item).strip()
        if dep:
            deps.append(dep)
    return deps


def _normalize_string_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    values: List[str] = []
    for item in raw:
        value = str(item).strip()
        if value:
            values.append(value)
    return values


def infer_task_type(text: str, skill: Any = None) -> str:
    """Infer a coarse task type used by the executor tool policy."""
    haystack = f"{text or ''} {skill or ''}".lower()
    keyword_map = [
        ("research", ["搜索", "查询", "检索", "查找", "联网", "资料", "知识库", "文档库", "最新", "search"]),
        ("read", ["读取", "打开文件", "查看文件", "列出", "目录", "read", "list"]),
        ("write", ["写入", "保存", "创建文件", "修改文件", "生成文件", "write", "save"]),
        ("execute", ["执行", "运行", "命令", "脚本", "python", "shell", "测试", "编译", "execute", "run"]),
        ("browse", ["浏览器", "网页交互", "点击", "输入", "midscene", "browser"]),
        ("verify", ["验证", "检查", "验收", "测试结果", "确认", "verify", "check"]),
        ("create", ["创建", "生成", "撰写", "输出", "报告", "create", "generate"]),
        ("analyze", ["分析", "总结", "归纳", "对比", "评估", "analyze", "summary"]),
    ]
    for task_type, keywords in keyword_map:
        if any(keyword in haystack for keyword in keywords):
            return task_type
    return "general"


def is_continuation_request(text: str) -> bool:
    """Detect explicit cross-turn references that should stay on the Agent route."""
    normalized = "".join(str(text or "").lower().split())
    if not normalized or len(normalized) > 160:
        return False
    markers = (
        "继续", "接着", "然后呢", "下一步", "上一步", "刚才", "上次",
        "按照上面", "按上面", "按刚才", "沿用", "基于之前", "在此基础上",
        "继续优化", "继续执行", "重试", "再试一次", "重新执行", "用上一个",
        "那个文件", "这个文件", "上个结果", "刚才的结果", "前面的结果",
    )
    return any(marker in normalized for marker in markers)


_REASONING_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|token|password|passwd|secret|authorization)\b"
        r"\s*[:=]\s*([^\s,;]+)"
    ),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+\-/]+=*"),
    re.compile(r"\bsk-[a-zA-Z0-9_-]{12,}\b"),
)


def sanitize_reasoning_text(value: Any, max_chars: int = 500) -> str:
    """Create a bounded, single-line, secret-redacted public work summary."""
    text = " ".join(str(value or "").split())
    for pattern in _REASONING_SECRET_PATTERNS:
        if "bearer" in pattern.pattern.lower():
            text = pattern.sub("Bearer ***", text)
        elif "sk-" in pattern.pattern.lower():
            text = pattern.sub("sk-***", text)
        else:
            text = pattern.sub(lambda match: f"{match.group(1)}=***", text)
    return text[: max(40, int(max_chars))]


def build_reasoning_update(
    phase: str,
    title: str,
    summary: str,
    *,
    next_action: str = "",
    subtask_id: Any = None,
    status: str = "running",
) -> Dict[str, Any]:
    """Build the public reasoning-summary protocol used by Trace and the frontend."""
    allowed_phases = {"understand", "recall", "plan", "decide", "observe", "verify", "next", "summarize"}
    normalized_phase = str(phase or "understand").lower()
    if normalized_phase not in allowed_phases:
        normalized_phase = "understand"
    payload: Dict[str, Any] = {
        "schema_version": 1,
        "visibility": "public_summary",
        "phase": normalized_phase,
        "title": sanitize_reasoning_text(title, 120),
        "summary": sanitize_reasoning_text(summary, 700),
        "next_action": sanitize_reasoning_text(next_action, 300),
        "status": str(status or "running")[:24],
    }
    if subtask_id is not None:
        payload["subtask_id"] = sanitize_reasoning_text(subtask_id, 80)
    return payload


def infer_risk_level(task_type: str, text: str, skill: Any = None) -> str:
    """Classify subtask risk so traces can explain why tools were constrained."""
    haystack = f"{text or ''} {skill or ''}".lower()
    if task_type == "execute" or any(token in haystack for token in ["shell", "命令", "删除", "移动", "覆盖", "启动", "subprocess"]):
        return "high"
    if task_type in {"write", "browse"} or any(token in haystack for token in ["写入", "创建", "修改", "上传", "下载"]):
        return "medium"
    return "low"


def default_acceptance_checks(subtask: Dict[str, Any]) -> List[str]:
    expected = str(subtask.get("expected_result") or "").strip()
    task_type = str(subtask.get("task_type") or "general")
    checks = []
    if expected:
        checks.append(f"result_mentions_expected:{expected[:120]}")
    type_checks = {
        "research": "result_contains_source_or_summary",
        "read": "result_contains_file_observation",
        "write": "result_confirms_artifact_written",
        "execute": "result_contains_exit_or_execution_status",
        "browse": "result_contains_page_observation",
        "verify": "result_contains_pass_fail_judgement",
    }
    if task_type in type_checks:
        checks.append(type_checks[task_type])
    return checks or ["result_is_non_empty"]


def allowed_tools_for_subtask(subtask: Dict[str, Any], all_tool_names: Sequence[str]) -> List[str]:
    """Return tool names the executor may expose for this subtask."""
    available = set(all_tool_names)
    explicit = {name for name in _normalize_string_list(subtask.get("allowed_tools")) if name in available}
    if "allowed_tools" in subtask and not subtask.get("allowed_tools") and subtask.get("task_type") == "general":
        return []
    if explicit:
        return sorted(explicit)

    task_type = str(subtask.get("task_type") or "general")
    policy = set(TOOL_POLICY_BY_TASK_TYPE.get(task_type, set())) | ALWAYS_AVAILABLE_TOOLS
    skill_name = str(subtask.get("skill") or "")
    if skill_name:
        policy.update(name for name in available if skill_name.split("_")[0] in name)
    allowed = sorted(available & policy)
    return allowed or sorted(available - HIGH_RISK_TOOLS)


def detect_tool_policy_violations(tool_calls: Sequence[Dict[str, Any]], allowed_tool_names: Sequence[str]) -> List[Dict[str, Any]]:
    allowed = set(allowed_tool_names)
    violations = []
    for call in tool_calls or []:
        name = str(call.get("name") or "")
        if name and name not in allowed:
            violations.append({"tool": name, "reason": "tool not allowed for current subtask"})
    return violations


def normalize_plan(
    raw_plan: Dict[str, Any],
    goal: str,
    complexity: str,
    previous_plan: Optional[Dict[str, Any]] = None,
    replan_from: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert loose model output into the project plan schema."""
    previous_version = _safe_int((previous_plan or {}).get("version"), 0)
    plan_id = (previous_plan or {}).get("plan_id") or f"plan_{uuid.uuid4().hex[:10]}"
    raw_subtasks = raw_plan.get("subtasks") if isinstance(raw_plan, dict) else None
    if not isinstance(raw_subtasks, list) or not raw_subtasks:
        raw_subtasks = [{"id": 1, "desc": goal, "skill": None, "depends_on": []}]

    subtasks: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(raw_subtasks, 1):
        item = item if isinstance(item, dict) else {"desc": str(item)}
        raw_id = item.get("id") or item.get("task_id") or idx
        task_id = str(raw_id).strip() or str(idx)
        if task_id in seen_ids:
            task_id = f"{task_id}_{idx}"
        seen_ids.add(task_id)
        subtasks.append(
            # Keep planner output loose, then attach deterministic execution metadata below.
            {
                "id": task_id,
                "desc": str(item.get("desc") or item.get("goal") or goal).strip(),
                "expected_result": str(item.get("expected_result") or item.get("expect") or "").strip(),
                "skill": item.get("skill"),
                "depends_on": _normalize_dep_list(item.get("depends_on") or item.get("dependencies")),
                "task_type": str(item.get("task_type") or item.get("type") or "").strip().lower(),
                "risk_level": str(item.get("risk_level") or item.get("risk") or "").strip().lower(),
                "acceptance_checks": _normalize_string_list(item.get("acceptance_checks") or item.get("checks")),
                "allowed_tools": _normalize_string_list(item.get("allowed_tools") or item.get("tools")),
                "max_tool_calls": _safe_int(item.get("max_tool_calls"), 0),
                "status": item.get("status") if item.get("status") in TERMINAL_STATUSES else "pending",
                "result": item.get("result", ""),
                "error": item.get("error", ""),
                "retry_count": _safe_int(item.get("retry_count"), 0),
            }
        )

    for subtask in subtasks:
        if subtask["task_type"] not in TASK_TYPES:
            subtask["task_type"] = infer_task_type(subtask.get("desc", ""), subtask.get("skill"))
        if subtask["risk_level"] not in RISK_LEVELS:
            subtask["risk_level"] = infer_risk_level(subtask["task_type"], subtask.get("desc", ""), subtask.get("skill"))
        if not subtask["acceptance_checks"]:
            subtask["acceptance_checks"] = default_acceptance_checks(subtask)
        if subtask["max_tool_calls"] <= 0:
            subtask["max_tool_calls"] = 1 if subtask["risk_level"] == "low" else 2 if subtask["risk_level"] == "medium" else 3

    plan = {
        "plan_id": plan_id,
        "version": previous_version + 1,
        "goal": str(raw_plan.get("goal") or goal)[:500],
        "decision_summary": sanitize_reasoning_text(
            raw_plan.get("decision_summary")
            or f"根据任务复杂度、依赖关系和可用能力，将目标组织为 {len(subtasks)} 个可验收子任务。",
            500,
        ),
        "complexity": complexity,
        "subtasks": subtasks,
        "current_subtask": 1,
        "estimated_steps": _safe_int(raw_plan.get("estimated_steps"), len(subtasks)),
        "created_at": now_iso(),
        "replan_from": replan_from,
    }
    budget = RuntimeBudget.from_config()
    plan["budget"] = {
        "max_subtasks": min(12, max(1, budget.max_steps // 3)),
        "max_steps": budget.max_steps,
        "max_runtime_seconds": budget.max_runtime_seconds,
        "max_tokens": budget.max_tokens,
    }
    plan = enforce_plan_budget(plan)
    errors = validate_plan(plan)
    if errors:
        plan["validation_errors"] = errors
        plan["subtasks"] = repair_plan_dependencies(plan["subtasks"])
    score = score_plan_quality(plan)
    plan["quality"] = score
    plan["ready_subtasks"] = get_ready_subtasks(plan)
    plan["current_subtask"] = get_next_subtask_index(plan) or len(plan["subtasks"]) + 1
    plan["state_graph"] = build_plan_state_graph(plan)
    return plan


def enforce_plan_budget(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Limit over-decomposed plans before they enter the executor."""
    plan = dict(plan)
    budget = plan.get("budget") or {}
    max_subtasks = max(1, _safe_int(budget.get("max_subtasks"), 12))
    subtasks = list(plan.get("subtasks") or [])
    if len(subtasks) <= max_subtasks:
        return plan

    kept = subtasks[:max_subtasks]
    kept_ids = {str(st.get("id")) for st in kept}
    for st in kept:
        clone_deps = [dep for dep in _normalize_dep_list(st.get("depends_on")) if dep in kept_ids]
        st["depends_on"] = clone_deps
    plan["subtasks"] = kept
    plan["budget_warnings"] = [f"subtasks truncated from {len(subtasks)} to {max_subtasks}"]
    plan["estimated_steps"] = min(_safe_int(plan.get("estimated_steps"), len(kept)), max_subtasks)
    return plan


def score_plan_quality(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic verifier for planner output quality."""
    subtasks = plan.get("subtasks") or []
    errors = validate_plan(plan)
    warnings: List[str] = []
    score = 100

    if errors:
        score -= min(40, 8 * len(errors))
    if not subtasks:
        return {"score": 0, "level": "invalid", "warnings": ["plan has no subtasks"], "errors": errors}

    if len(subtasks) == 1 and plan.get("complexity") == "complex":
        warnings.append("complex task has only one subtask")
        score -= 12
    if len(subtasks) > _safe_int((plan.get("budget") or {}).get("max_subtasks"), 12):
        warnings.append("plan exceeds subtask budget")
        score -= 15

    seen_desc: set[str] = set()
    known_statuses = EXECUTION_STATUSES
    for st in subtasks:
        desc = str(st.get("desc") or "").strip()
        expected = str(st.get("expected_result") or "").strip()
        status = str(st.get("status") or "")
        task_type = str(st.get("task_type") or "")
        risk_level = str(st.get("risk_level") or "")
        acceptance_checks = _normalize_string_list(st.get("acceptance_checks"))
        if len(desc) < 8:
            warnings.append(f"subtask {st.get('id')} description is too short")
            score -= 5
        normalized_desc = desc.lower()
        if normalized_desc in seen_desc:
            warnings.append(f"subtask {st.get('id')} duplicates another description")
            score -= 8
        seen_desc.add(normalized_desc)
        if not expected:
            warnings.append(f"subtask {st.get('id')} has no expected_result")
            score -= 4
        if task_type not in TASK_TYPES:
            warnings.append(f"subtask {st.get('id')} has invalid task_type")
            score -= 6
        if risk_level not in RISK_LEVELS:
            warnings.append(f"subtask {st.get('id')} has invalid risk_level")
            score -= 6
        if not acceptance_checks:
            warnings.append(f"subtask {st.get('id')} has no acceptance_checks")
            score -= 5
        if status not in known_statuses:
            warnings.append(f"subtask {st.get('id')} has unknown status {status}")
            score -= 4

    level = "good" if score >= 82 else "usable" if score >= 60 else "weak"
    return {
        "score": max(0, min(100, score)),
        "level": level,
        "warnings": sorted(set(warnings)),
        "errors": errors,
    }


def repair_plan_for_capabilities(
    plan: Dict[str, Any],
    available_skills: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Repair skill names in a plan using registered skill metadata."""
    skill_items = list(available_skills or [])
    skill_names = {str(item.get("name")) for item in skill_items if item.get("name")}
    keyword_index: List[tuple[str, str]] = []
    for item in skill_items:
        name = str(item.get("name") or "")
        haystack = " ".join(
            [str(item.get("description") or ""), str(item.get("category") or ""), *[str(k) for k in item.get("keywords", [])]]
        ).lower()
        for token in set(haystack.replace("，", " ").replace(",", " ").split()):
            if token:
                keyword_index.append((token, name))

    repaired = dict(plan)
    repaired["subtasks"] = [dict(st) for st in plan.get("subtasks", [])]
    repairs: List[Dict[str, Any]] = []
    for st in repaired["subtasks"]:
        current_skill = st.get("skill")
        if current_skill in (None, "", "null"):
            inferred = infer_skill_for_text(str(st.get("desc") or ""), keyword_index)
            if inferred:
                st["skill"] = inferred
                repairs.append({"subtask_id": st.get("id"), "action": "skill_inferred", "skill": inferred})
            continue
        if str(current_skill) not in skill_names:
            inferred = infer_skill_for_text(f"{current_skill} {st.get('desc', '')}", keyword_index)
            repairs.append(
                {
                    "subtask_id": st.get("id"),
                    "action": "skill_replaced" if inferred else "skill_removed",
                    "from": current_skill,
                    "to": inferred,
                }
            )
            st["skill"] = inferred

    if repairs:
        repaired["capability_repairs"] = repairs
    repaired["quality"] = score_plan_quality(repaired)
    repaired["ready_subtasks"] = get_ready_subtasks(repaired)
    repaired["current_subtask"] = get_next_subtask_index(repaired) or len(repaired.get("subtasks", [])) + 1
    repaired["state_graph"] = build_plan_state_graph(repaired)
    return repaired


def infer_skill_for_text(text: str, keyword_index: Iterable[tuple[str, str]]) -> Optional[str]:
    text_l = (text or "").lower()
    scores: Dict[str, int] = {}
    for token, name in keyword_index:
        if token and token in text_l:
            scores[name] = scores.get(name, 0) + 1
    if not scores:
        return None
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]


def repair_plan_dependencies(subtasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    known = {str(st.get("id")) for st in subtasks}
    repaired: List[Dict[str, Any]] = []
    for st in subtasks:
        clone = dict(st)
        clone["depends_on"] = [dep for dep in _normalize_dep_list(clone.get("depends_on")) if dep in known and dep != str(clone.get("id"))]
        repaired.append(clone)
    return repaired


def validate_plan(plan: Dict[str, Any]) -> List[str]:
    subtasks = plan.get("subtasks", [])
    if not isinstance(subtasks, list) or not subtasks:
        return ["plan has no subtasks"]
    ids = [str(st.get("id", "")) for st in subtasks]
    errors: List[str] = []
    if any(not task_id for task_id in ids):
        errors.append("subtask id cannot be empty")
    if len(set(ids)) != len(ids):
        errors.append("subtask ids must be unique")
    known = set(ids)
    graph = {str(st.get("id")): _normalize_dep_list(st.get("depends_on")) for st in subtasks}
    for task_id, deps in graph.items():
        for dep in deps:
            if dep not in known:
                errors.append(f"subtask {task_id} depends on missing {dep}")
            if dep == task_id:
                errors.append(f"subtask {task_id} depends on itself")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            errors.append(f"cycle detected at {node}")
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            if dep in known:
                visit(dep)
        visiting.remove(node)
        visited.add(node)

    for task_id in ids:
        visit(task_id)
    return sorted(set(errors))


def get_ready_subtasks(plan: Dict[str, Any]) -> List[str]:
    subtasks = plan.get("subtasks", [])
    done = {str(st.get("id")) for st in subtasks if st.get("status") == "completed"}
    ready: List[str] = []
    for st in subtasks:
        task_id = str(st.get("id"))
        if st.get("status", "pending") not in ACTIVE_STATUSES:
            continue
        deps = _normalize_dep_list(st.get("depends_on"))
        if all(dep in done for dep in deps):
            ready.append(task_id)
    return ready


def get_next_subtask_index(plan: Dict[str, Any]) -> Optional[int]:
    ready = set(plan.get("ready_subtasks") or get_ready_subtasks(plan))
    for idx, st in enumerate(plan.get("subtasks", []), 1):
        if str(st.get("id")) in ready:
            return idx
    return None


def build_plan_state_graph(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Build a dynamic DAG/state snapshot for scheduling and visualization."""
    subtasks = list(plan.get("subtasks") or [])
    by_id = {str(st.get("id")): st for st in subtasks}
    completed = {task_id for task_id, st in by_id.items() if st.get("status") == "completed"}
    failed = {task_id for task_id, st in by_id.items() if st.get("status") == "failed"}
    ready = set(get_ready_subtasks(plan))
    terminal = set()
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    for task_id, st in by_id.items():
        deps = _normalize_dep_list(st.get("depends_on"))
        status = st.get("status") or "pending"
        if status == "completed":
            state = "done"
            terminal.add(task_id)
        elif status == "failed":
            state = "failed"
            terminal.add(task_id)
        elif status == "skipped":
            state = "skipped"
            terminal.add(task_id)
        elif status == "adjust":
            state = "needs_replan"
        elif status == "running":
            state = "running"
        elif task_id in ready:
            state = "ready"
        elif any(dep in failed for dep in deps):
            state = "blocked"
        elif status == "retry":
            state = "ready" if task_id in ready else "waiting"
        else:
            state = "waiting"

        nodes.append(
            {
                "id": task_id,
                "state": state,
                "status": status,
                "desc": str(st.get("desc") or "")[:240],
                "skill": st.get("skill"),
                "task_type": st.get("task_type"),
                "risk_level": st.get("risk_level"),
                "acceptance_checks": st.get("acceptance_checks", []),
                "allowed_tools": st.get("allowed_tools", []),
                "depends_on": deps,
                "retry_count": _safe_int(st.get("retry_count"), 0),
            }
        )
        for dep in deps:
            edge_state = "satisfied" if dep in completed else "failed" if dep in failed else "waiting"
            edges.append({"from": dep, "to": task_id, "state": edge_state})

    blocked = [node["id"] for node in nodes if node["state"] == "blocked"]
    running = [node["id"] for node in nodes if node["state"] == "running"]
    waiting = [node["id"] for node in nodes if node["state"] == "waiting"]
    needs_replan = [node["id"] for node in nodes if node["state"] == "needs_replan"]
    return {
        "kind": "dynamic_dag",
        "version": plan.get("version", 1),
        "updated_at": now_iso(),
        "nodes": nodes,
        "edges": edges,
        "frontier": sorted(ready),
        "running": running,
        "blocked": blocked,
        "waiting": waiting,
        "terminal": sorted(terminal),
        "needs_replan": needs_replan,
        "summary": {
            "total": len(nodes),
            "ready": len(ready),
            "running": len(running),
            "waiting": len(waiting),
            "blocked": len(blocked),
            "completed": len(completed),
            "failed": len(failed),
            "needs_replan": len(needs_replan),
        },
    }


def update_plan_after_subtask(plan: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(plan)
    plan["ready_subtasks"] = get_ready_subtasks(plan)
    next_idx = get_next_subtask_index(plan)
    plan["current_subtask"] = next_idx or len(plan.get("subtasks", [])) + 1
    plan["state_graph"] = build_plan_state_graph(plan)
    return plan


def verify_subtask_result(
    subtask: Dict[str, Any],
    result_text: str,
    stop_reason: str = "",
    has_tool_evidence: bool = False,
) -> Dict[str, Any]:
    """Deterministic verifier before the LLM critic makes a softer judgement."""
    text = (result_text or "").strip()
    expected = str(subtask.get("expected_result") or "").strip()
    task_type = str(subtask.get("task_type") or "general")
    checks = _normalize_string_list(subtask.get("acceptance_checks"))
    issues: List[str] = []
    passed_checks: List[str] = []
    status = "unknown"

    if stop_reason:
        issues.append(f"runtime stopped: {stop_reason}")
        status = "adjust" if "budget" in stop_reason or "repeated" in stop_reason else "retry"
    if not text:
        issues.append("empty execution result")
        status = "retry"
    error_markers = ["traceback", "exception", "error:", "执行出错", "工具调用已达上限", "[loopguard]", "[budgetstop]"]
    if any(marker in text.lower() for marker in error_markers):
        issues.append("execution result contains error marker")
        status = "retry" if status == "unknown" else status
    if expected and len(expected) >= 6:
        expected_terms = [term for term in expected.replace("，", " ").replace(",", " ").split() if len(term) >= 2]
        if expected_terms:
            matched = sum(1 for term in expected_terms[:8] if term.lower() in text.lower())
            if matched == 0 and len(text) < 120 and not has_tool_evidence:
                issues.append("result is too thin for expected_result")
                status = "retry" if status == "unknown" else status
            elif matched > 0:
                passed_checks.append("expected_result_terms")

    lowered = text.lower()
    if task_type == "execute":
        if has_tool_evidence or any(marker in lowered for marker in ["exit_code", "执行成功", "stdout", "stderr", "执行结果", "result"]):
            passed_checks.append("execution_status")
        else:
            issues.append("execute task lacks execution status")
            status = "retry" if status == "unknown" else status
    elif task_type == "write":
        if any(marker in lowered for marker in ["写入成功", "保存", "created", "written", "文件"]):
            passed_checks.append("artifact_written")
        else:
            issues.append("write task lacks artifact confirmation")
            status = "retry" if status == "unknown" else status
    elif task_type == "research":
        if any(marker in lowered for marker in ["标题", "来源", "http", "搜索结果", "总结", "资料"]):
            passed_checks.append("research_evidence")
        else:
            issues.append("research task lacks evidence or summary")
            status = "retry" if status == "unknown" else status
    elif task_type == "verify":
        if any(marker in lowered for marker in ["通过", "失败", "pass", "fail", "ok", "error"]):
            passed_checks.append("verification_judgement")
        else:
            issues.append("verify task lacks pass/fail judgement")
            status = "retry" if status == "unknown" else status

    for check in checks:
        if check == "result_is_non_empty" and text:
            passed_checks.append(check)

    if status == "unknown":
        status = "completed"
    return {
        "status": status,
        "issues": issues,
        "expected_result": expected,
        "task_type": task_type,
        "acceptance_checks": checks,
        "passed_checks": sorted(set(passed_checks)),
    }


def should_replan(plan: Dict[str, Any], subtask: Dict[str, Any], verifier: Dict[str, Any], retry_count: int, max_retries: int) -> bool:
    """Decide whether the planner should do local repair instead of another blind retry."""
    if subtask.get("status") == "adjust":
        return True
    if retry_count >= max_retries:
        return True
    if verifier.get("status") == "adjust":
        return True
    if plan.get("quality", {}).get("score", 100) < 60:
        return True
    return False


def budget_exceeded(state: Dict[str, Any], budget: RuntimeBudget) -> Optional[str]:
    if _safe_int(state.get("step_count"), 0) >= budget.max_steps:
        return f"step budget exceeded ({budget.max_steps})"
    started_at = float(state.get("started_at") or time.time())
    if time.time() - started_at >= budget.max_runtime_seconds:
        return f"runtime budget exceeded ({budget.max_runtime_seconds}s)"
    if _safe_int(state.get("token_count"), 0) >= budget.max_tokens:
        return f"token budget exceeded ({budget.max_tokens})"
    return None


def estimate_text_tokens(text: Any) -> int:
    """Small dependency-free token estimate for providers without usage metadata."""
    if text is None:
        return 0
    return max(1, len(str(text)) // 4)


def message_token_usage(message: Any) -> int:
    """Return model-reported token usage when available, otherwise estimate output tokens."""
    usage = getattr(message, "usage_metadata", None) or {}
    if isinstance(usage, dict):
        total = usage.get("total_tokens") or usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        try:
            if total:
                return int(total)
        except (TypeError, ValueError):
            pass
    return estimate_text_tokens(getattr(message, "content", ""))


class TraceStore:
    """SQLite + JSONL trace store for runs, plan versions and events."""

    def __init__(self, db_path: Optional[str] = None, jsonl_dir: Optional[str] = None):
        self.db_path = db_path or get_abs_path("data/agent_trace.db")
        self.jsonl_dir = jsonl_dir or get_abs_path("data/agent_traces")
        self._write_lock = threading.RLock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.jsonl_dir, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=30)

    def _init_db(self) -> None:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal TEXT,
                status TEXT,
                started_at TEXT,
                finished_at TEXT,
                final_output TEXT,
                metrics_json TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_plan_versions (
                run_id TEXT,
                plan_id TEXT,
                version INTEGER,
                plan_json TEXT,
                created_at TEXT,
                PRIMARY KEY (run_id, plan_id, version)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                node TEXT,
                payload_json TEXT,
                created_at TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_session_handoffs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal TEXT,
                status TEXT,
                handoff_json TEXT,
                created_at TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_handoffs_session_created
            ON agent_session_handoffs (session_id, created_at DESC)
            """
        )
        conn.commit()
        conn.close()

    def start_run(self, run_id: str, session_id: str, goal: str) -> None:
        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO agent_runs
            (run_id, session_id, goal, status, started_at, finished_at, final_output, metrics_json)
            VALUES (?, ?, ?, 'running', ?, NULL, '', '{}')
            """,
            (run_id, session_id, goal[:500], now_iso()),
        )
        conn.commit()
        conn.close()

    def finish_run(self, run_id: str, status: str, final_output: str, metrics: Optional[Dict[str, Any]] = None) -> None:
        conn = self._connect()
        conn.execute(
            """
            UPDATE agent_runs
            SET status = ?, finished_at = ?, final_output = ?, metrics_json = ?
            WHERE run_id = ?
            """,
            (status, now_iso(), final_output[:4000], json.dumps(metrics or {}, ensure_ascii=False), run_id),
        )
        conn.commit()
        conn.close()

    def record_plan(self, run_id: str, plan: Dict[str, Any]) -> None:
        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO agent_plan_versions
            (run_id, plan_id, version, plan_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                plan.get("plan_id", ""),
                _safe_int(plan.get("version"), 1),
                json.dumps(plan, ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.commit()
        conn.close()

    def save_handoff(self, run_id: str, session_id: str, handoff: Dict[str, Any]) -> None:
        """Persist a compact, auditable execution capsule for the next turn."""
        payload = json.dumps(handoff, ensure_ascii=False, default=str)
        with self._write_lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_session_handoffs
                (run_id, session_id, goal, status, handoff_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    session_id,
                    str(handoff.get("goal") or "")[:500],
                    str(handoff.get("status") or "unknown")[:32],
                    payload,
                    now_iso(),
                ),
            )
            conn.commit()
            conn.close()

    def get_session_handoffs(
        self,
        session_id: str,
        *,
        limit: int = 2,
        exclude_run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Load recent execution capsules from one session only."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        params: List[Any] = [session_id]
        where = "session_id = ?"
        if exclude_run_id:
            where += " AND run_id != ?"
            params.append(exclude_run_id)
        params.append(max(1, min(int(limit), 5)))
        rows = conn.execute(
            f"""
            SELECT run_id, handoff_json, created_at
            FROM agent_session_handoffs
            WHERE {where}
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        conn.close()
        result: List[Dict[str, Any]] = []
        for row in rows:
            try:
                item = json.loads(row["handoff_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                item.setdefault("run_id", row["run_id"])
                item.setdefault("created_at", row["created_at"])
                result.append(item)
        return result

    def delete_session_data(self, session_id: str) -> None:
        """Delete runs, plans, events, handoffs and JSONL traces for one session."""
        with self._write_lock:
            conn = self._connect()
            run_rows = conn.execute(
                "SELECT run_id FROM agent_runs WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            run_ids = [str(row[0]) for row in run_rows]
            conn.execute(
                "DELETE FROM agent_plan_versions WHERE run_id IN (SELECT run_id FROM agent_runs WHERE session_id = ?)",
                (session_id,),
            )
            conn.execute("DELETE FROM agent_trace_events WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM agent_session_handoffs WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM agent_runs WHERE session_id = ?", (session_id,))
            conn.commit()
            conn.close()
            for run_id in run_ids:
                jsonl_path = os.path.join(self.jsonl_dir, f"{run_id}.jsonl")
                if os.path.isfile(jsonl_path):
                    os.remove(jsonl_path)

    def event(self, run_id: str, session_id: str, event_type: str, node: str, payload: Dict[str, Any]) -> None:
        created_at = now_iso()
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        with self._write_lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO agent_trace_events
                (run_id, session_id, event_type, node, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, session_id, event_type, node, payload_json, created_at),
            )
            conn.commit()
            conn.close()

            jsonl_path = os.path.join(self.jsonl_dir, f"{run_id}.jsonl")
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "session_id": session_id,
                            "event_type": event_type,
                            "node": node,
                            "payload": payload,
                            "created_at": created_at,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        conn.close()
        if not row:
            return None
        data = dict(row)
        data["metrics"] = json.loads(data.pop("metrics_json") or "{}")
        return data

    def list_runs(self, session_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        if session_id:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE session_id = ? ORDER BY started_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        result = []
        for row in rows:
            item = dict(row)
            item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
            result.append(item)
        return result

    def get_trace(self, run_id: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        plan_rows = conn.execute(
            "SELECT plan_json FROM agent_plan_versions WHERE run_id = ? ORDER BY version ASC",
            (run_id,),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT event_type, node, payload_json, created_at FROM agent_trace_events WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
        conn.close()
        return {
            "run": run,
            "plans": [json.loads(row["plan_json"]) for row in plan_rows],
            "events": [
                {
                    "event_type": row["event_type"],
                    "node": row["node"],
                    "payload": json.loads(row["payload_json"] or "{}"),
                    "created_at": row["created_at"],
                }
                for row in event_rows
            ],
        }


trace_store = TraceStore()


def build_execution_handoff(
    *,
    run_id: str,
    goal: str,
    status: str,
    plan: Dict[str, Any],
    final_output: str,
    artifacts: Sequence[Dict[str, Any]],
    stop_reason: str = "",
    scheduler_epochs: int = 0,
    replan_count: int = 0,
) -> Dict[str, Any]:
    """Create bounded cross-turn state without storing private chain-of-thought."""
    tasks: List[Dict[str, Any]] = []
    for subtask in (plan.get("subtasks") or [])[:12]:
        evidence = [
            {
                "kind": str(item.get("kind") or "observation"),
                "source": str(item.get("source") or "unknown"),
                "summary": str(item.get("summary") or "")[:500],
            }
            for item in subtask.get("evidence") or []
            if isinstance(item, dict)
        ][:6]
        evidence_sources = [
            str(item.get("source"))
            for item in subtask.get("evidence") or []
            if isinstance(item, dict) and item.get("source")
        ]
        task_artifacts = [
            {
                "kind": str(item.get("kind") or "artifact"),
                "uri": str(item.get("uri") or "")[:500],
            }
            for item in subtask.get("artifacts") or []
            if isinstance(item, dict)
        ]
        tool_calls = [
            {
                "tool": str(item.get("tool") or "unknown"),
                "ok": bool(item.get("ok")),
                "args": item.get("args") if isinstance(item.get("args"), dict) else {},
            }
            for item in subtask.get("tool_calls") or []
            if isinstance(item, dict)
        ][:8]
        verifier = subtask.get("verifier") if isinstance(subtask.get("verifier"), dict) else {}
        tasks.append(
            {
                "id": str(subtask.get("id") or ""),
                "desc": str(subtask.get("desc") or "")[:500],
                "status": str(subtask.get("status") or "pending"),
                "worker": str(subtask.get("worker") or ""),
                "result": str(subtask.get("result") or "")[:1200],
                "error": str(subtask.get("error") or "")[:500],
                "evidence": evidence,
                "evidence_sources": evidence_sources[:8],
                "artifacts": task_artifacts[:8],
                "tool_calls": tool_calls,
                "verification": {
                    "status": str(verifier.get("status") or "unknown"),
                    "issues": [str(item)[:300] for item in verifier.get("issues") or []][:6],
                    "passed_checks": [str(item)[:200] for item in verifier.get("passed_checks") or []][:8],
                },
                "depends_on": [str(item) for item in subtask.get("depends_on") or []],
            }
        )
    compact_artifacts = [
        {
            "subtask_id": str(item.get("subtask_id") or ""),
            "kind": str(item.get("kind") or "artifact"),
            "uri": str(item.get("uri") or "")[:500],
            "description": str(item.get("description") or "")[:300],
        }
        for item in list(artifacts)[:16]
        if isinstance(item, dict)
    ]
    open_items = [
        {
            "id": item["id"],
            "status": item["status"],
            "desc": item["desc"],
            "error": item["error"],
        }
        for item in tasks
        if item["status"] not in TERMINAL_STATUSES or item["status"] in {"failed", "skipped"}
    ]
    return {
        "schema_version": 1,
        "run_id": run_id,
        "goal": str(goal or plan.get("goal") or "")[:1000],
        "status": status,
        "plan_id": str(plan.get("plan_id") or ""),
        "plan_version": _safe_int(plan.get("version"), 1),
        "complexity": str(plan.get("complexity") or "unknown"),
        "plan_summary": sanitize_reasoning_text(plan.get("decision_summary") or "", 500),
        "final_summary": str(final_output or "")[:1600],
        "stop_reason": str(stop_reason or "")[:500],
        "scheduler_epochs": max(0, int(scheduler_epochs)),
        "replan_count": max(0, int(replan_count)),
        "subtasks": tasks,
        "artifacts": compact_artifacts,
        "open_items": open_items,
        "created_at": now_iso(),
    }


def format_handoff_context(handoffs: Sequence[Dict[str, Any]], max_chars: int = 6000) -> str:
    """Render execution capsules as a bounded Planner/Worker handoff context."""
    sections: List[str] = []
    for handoff in list(handoffs)[:3]:
        lines = [
            f"[运行 {handoff.get('run_id', '-')}; 状态 {handoff.get('status', 'unknown')}; 时间 {handoff.get('created_at', '-')}]",
            f"目标: {str(handoff.get('goal') or '')[:800]}",
            f"最终摘要: {str(handoff.get('final_summary') or '')[:1000]}",
        ]
        if handoff.get("plan_summary"):
            lines.append(f"规划依据: {str(handoff.get('plan_summary'))[:500]}")
        if handoff.get("stop_reason"):
            lines.append(f"停止原因: {str(handoff.get('stop_reason'))[:500]}")
        for task in (handoff.get("subtasks") or [])[:10]:
            if not isinstance(task, dict):
                continue
            sources = ", ".join(str(item) for item in task.get("evidence_sources") or [])
            task_line = (
                f"- 子任务 {task.get('id', '-')}: [{task.get('status', 'unknown')}] "
                f"{str(task.get('desc') or '')[:300]}"
            )
            if task.get("result"):
                task_line += f" => {str(task.get('result'))[:700]}"
            if task.get("error"):
                task_line += f" | 错误: {str(task.get('error'))[:300]}"
            if sources:
                task_line += f" | 证据: {sources}"
            lines.append(task_line)
            tool_names = [
                str(item.get("tool"))
                for item in task.get("tool_calls") or []
                if isinstance(item, dict) and item.get("tool")
            ]
            if tool_names:
                lines.append(f"  工具调用: {', '.join(tool_names[:8])}")
            verification = task.get("verification") if isinstance(task.get("verification"), dict) else {}
            issues = [str(item) for item in verification.get("issues") or []]
            if verification:
                verify_line = f"  验收: {verification.get('status', 'unknown')}"
                if issues:
                    verify_line += f"；问题: {'; '.join(issues[:4])}"
                lines.append(verify_line)
            for evidence in (task.get("evidence") or [])[:3]:
                if isinstance(evidence, dict) and evidence.get("summary"):
                    lines.append(
                        f"  观察[{evidence.get('source', 'unknown')}]: "
                        f"{str(evidence.get('summary'))[:350]}"
                    )
        artifact_uris = [
            str(item.get("uri"))
            for item in handoff.get("artifacts") or []
            if isinstance(item, dict) and item.get("uri")
        ]
        if artifact_uris:
            lines.append(f"产物: {', '.join(artifact_uris[:8])}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)[: max(500, int(max_chars))]


def trace_enabled(state: Dict[str, Any]) -> bool:
    return bool(state.get("trace_enabled", True))


def record_trace(state: Dict[str, Any], event_type: str, node: str, payload: Dict[str, Any]) -> None:
    if not trace_enabled(state):
        return
    run_id = state.get("run_id")
    session_id = state.get("session_id") or "default"
    if run_id:
        trace_store.event(run_id, session_id, event_type, node, payload)

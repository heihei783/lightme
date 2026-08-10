"""Structured workers and deterministic DAG scheduling for LightMe Agent."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from app.agent.runtime import (
    allowed_tools_for_subtask,
    build_reasoning_update,
    detect_tool_policy_violations,
    get_ready_subtasks,
    message_token_usage,
    verify_subtask_result,
)
from app.agent.skill_loader import get_skill_tools
from app.agent.skills import skill_registry
from app.agent.tools import DEFAULT_TOOLS
from app.llm.chat_model import chat_model
from utils.console_emitter import console


WorkerStatus = Literal["completed", "retryable", "replan_required", "failed"]
EventSink = Callable[[str, str, Dict[str, Any]], None]


class WorkerTask(BaseModel):
    run_id: str
    session_id: str
    subtask_id: str
    objective: str
    expected_result: str = ""
    task_type: str = "general"
    risk_level: str = "low"
    acceptance_checks: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    runtime_allowed_tools: Optional[List[str]] = None
    max_tool_calls: int = 1
    skill: Optional[str] = None
    dependency_results: Dict[str, str] = Field(default_factory=dict)
    memory_context: str = ""
    deadline_seconds: int = 120

    @classmethod
    def from_subtask(
        cls,
        subtask: Dict[str, Any],
        *,
        run_id: str,
        session_id: str,
        dependency_results: Optional[Dict[str, str]] = None,
        memory_context: str = "",
        deadline_seconds: int = 120,
        runtime_allowed_tools: Optional[Sequence[str]] = None,
    ) -> "WorkerTask":
        return cls(
            run_id=run_id,
            session_id=session_id,
            subtask_id=str(subtask.get("id") or ""),
            objective=str(subtask.get("desc") or ""),
            expected_result=str(subtask.get("expected_result") or ""),
            task_type=str(subtask.get("task_type") or "general"),
            risk_level=str(subtask.get("risk_level") or "low"),
            acceptance_checks=[str(item) for item in subtask.get("acceptance_checks") or []],
            allowed_tools=[str(item) for item in subtask.get("allowed_tools") or []],
            runtime_allowed_tools=[str(item) for item in runtime_allowed_tools] if runtime_allowed_tools is not None else None,
            max_tool_calls=max(1, int(subtask.get("max_tool_calls") or 1)),
            skill=str(subtask.get("skill")) if subtask.get("skill") else None,
            dependency_results=dict(dependency_results or {}),
            memory_context=memory_context[:7000],
            deadline_seconds=max(5, int(deadline_seconds)),
        )


class WorkerEvidence(BaseModel):
    kind: str
    source: str
    summary: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkerArtifact(BaseModel):
    kind: str
    uri: str
    description: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkerResult(BaseModel):
    run_id: str
    subtask_id: str
    worker: str
    status: WorkerStatus
    summary: str = ""
    evidence: List[WorkerEvidence] = Field(default_factory=list)
    artifacts: List[WorkerArtifact] = Field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    verification: Dict[str, Any] = Field(default_factory=dict)
    token_count: int = 0
    step_count: int = 0
    elapsed_seconds: float = 0
    error: Optional[str] = None


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    description: str
    task_types: frozenset[str]
    tool_names: frozenset[str] = field(default_factory=frozenset)
    system_prompt: str = ""
    priority: int = 100


class WorkerRegistry:
    """Capability registry. A worker is defined by contracts and tools, not a role label."""

    def __init__(self) -> None:
        self._workers: Dict[str, WorkerSpec] = {}

    def register(self, worker: WorkerSpec) -> None:
        self._workers[worker.name] = worker

    def get(self, name: str) -> Optional[WorkerSpec]:
        return self._workers.get(name)

    def list_all(self) -> List[WorkerSpec]:
        return sorted(self._workers.values(), key=lambda item: (item.priority, item.name))

    def select(self, task: WorkerTask) -> WorkerSpec:
        skill_name = str(task.skill or "").lower()
        preferred = None
        if any(token in skill_name for token in ("midscene", "browser", "web_interaction")):
            preferred = "browser_worker"
        elif any(token in skill_name for token in ("search", "reader", "firecrawl")):
            preferred = "research_worker"
        elif any(token in skill_name for token in ("system", "shell", "python", "writer")):
            preferred = "execution_worker"
        if preferred and preferred in self._workers:
            return self._workers[preferred]

        matches = [worker for worker in self.list_all() if task.task_type in worker.task_types]
        if matches:
            return matches[0]
        fallback = self._workers.get("general_worker")
        if fallback:
            return fallback
        raise LookupError(f"no worker registered for task type: {task.task_type}")


RESEARCH_TOOLS = frozenset(
    {
        "knowledge_search",
        "web_search",
        "firecrawl_search",
        "firecrawl_scrape",
        "firecrawl_crawl",
        "firecrawl_map",
        "firecrawl_extract",
        "read_file_content",
        "list_directory",
        "search_files",
        "get_file_info",
        "get_system_info",
        "get_disk_usage",
    }
)
EXECUTION_TOOLS = frozenset(
    {
        "execute_python_code",
        "execute_shell_command",
        "read_file_content",
        "write_file_content",
        "list_directory",
        "search_files",
        "get_file_info",
        "copy_file",
        "move_file",
        "make_directory",
        "open_path",
        "list_processes",
        "start_app",
        "get_system_info",
        "get_disk_usage",
    }
)
BROWSER_TOOLS = frozenset(
    {
        "open_url",
        "web_search",
        "firecrawl_search",
        "firecrawl_scrape",
        "midscene_act",
        "midscene_flow",
        "midscene_screenshot",
    }
)
VERIFICATION_TOOLS = frozenset(
    {
        "read_file_content",
        "list_directory",
        "search_files",
        "get_file_info",
        "execute_python_code",
        "execute_shell_command",
        "get_system_info",
        "get_disk_usage",
    }
)


def build_default_worker_registry() -> WorkerRegistry:
    registry = WorkerRegistry()
    registry.register(
        WorkerSpec(
            name="research_worker",
            description="Collects and analyzes external, file and system information.",
            task_types=frozenset({"research", "read", "analyze"}),
            tool_names=RESEARCH_TOOLS,
            system_prompt="优先获取一手证据；区分事实、推断和未知信息；结果必须标注证据来源。",
            priority=10,
        )
    )
    registry.register(
        WorkerSpec(
            name="browser_worker",
            description="Performs browser and web interaction tasks.",
            task_types=frozenset({"browse"}),
            tool_names=BROWSER_TOOLS,
            system_prompt="只执行当前网页子任务，记录访问目标和可验证的页面结果。",
            priority=20,
        )
    )
    registry.register(
        WorkerSpec(
            name="execution_worker",
            description="Executes code, shell, file and system operations.",
            task_types=frozenset({"execute", "write", "create"}),
            tool_names=EXECUTION_TOOLS,
            system_prompt="先检查风险和输入，再执行最少必要操作；工具成功后立即整理证据，不重复执行。",
            priority=30,
        )
    )
    registry.register(
        WorkerSpec(
            name="verification_worker",
            description="Verifies artifacts and execution outcomes.",
            task_types=frozenset({"verify"}),
            tool_names=VERIFICATION_TOOLS,
            system_prompt="只根据实际证据判定通过或失败，不用猜测补全缺失结果。",
            priority=40,
        )
    )
    registry.register(
        WorkerSpec(
            name="general_worker",
            description="Handles direct, tool-free reasoning and synthesis.",
            task_types=frozenset({"general"}),
            tool_names=frozenset(),
            system_prompt="直接完成当前目标，输出简洁、可交付的结果。",
            priority=100,
        )
    )
    return registry


worker_registry = build_default_worker_registry()


def _safe_tool_args(args: Any) -> Dict[str, Any]:
    if not isinstance(args, dict):
        return {}
    sensitive = {"password", "token", "api_key", "secret", "authorization"}
    result: Dict[str, Any] = {}
    for key, value in list(args.items())[:12]:
        result[str(key)] = "***" if str(key).lower() in sensitive else str(value)[:300]
    return result


def _artifact_from_tool(tool_name: str, args: Dict[str, Any], output: str) -> Optional[WorkerArtifact]:
    if tool_name == "write_file_content" and args.get("file_path"):
        return WorkerArtifact(kind="file", uri=str(args["file_path"]), description="Worker wrote a file")
    if tool_name in {"copy_file", "move_file"} and args.get("dst"):
        return WorkerArtifact(kind="file", uri=str(args["dst"]), description=f"Worker completed {tool_name}")
    if tool_name == "make_directory" and args.get("path"):
        return WorkerArtifact(kind="directory", uri=str(args["path"]), description="Worker created a directory")
    if tool_name in {"midscene_screenshot"}:
        return WorkerArtifact(kind="image", uri=output[:500], description="Browser screenshot artifact")
    return None


class WorkerAgent:
    """Runs one subtask with an isolated message list and a strict tool contract."""

    def __init__(
        self,
        spec: WorkerSpec,
        *,
        model: Any = None,
        base_tools: Optional[Sequence[Any]] = None,
    ) -> None:
        self.spec = spec
        self.model = model or chat_model
        self.base_tools = list(base_tools) if base_tools is not None else list(DEFAULT_TOOLS)

    def _resolve_tools(self, task: WorkerTask) -> List[Any]:
        skill = skill_registry.get_by_name(task.skill) if task.skill else None
        skill_tools = get_skill_tools(skill) if skill else []
        candidates = list(self.base_tools) + list(skill_tools)
        by_name = {tool.name: tool for tool in candidates}
        policy_task = task.model_dump()
        allowed = set(allowed_tools_for_subtask(policy_task, list(by_name)))
        worker_allowed = set(self.spec.tool_names) | {tool.name for tool in skill_tools}
        runtime_allowed = set(task.runtime_allowed_tools) if task.runtime_allowed_tools is not None else set(by_name)
        return [
            by_name[name]
            for name in sorted(allowed & worker_allowed & runtime_allowed)
            if name in by_name
        ]

    def _system_prompt(self, task: WorkerTask, tool_names: Sequence[str]) -> str:
        dependencies = json.dumps(task.dependency_results, ensure_ascii=False)[:5000]
        return (
            f"你是 {self.spec.name}。你不是协调者，只负责一个结构化子任务。\n"
            f"职责: {self.spec.description}\n"
            f"执行原则: {self.spec.system_prompt}\n\n"
            f"子任务 ID: {task.subtask_id}\n"
            f"目标: {task.objective}\n"
            f"预期结果: {task.expected_result or '未提供'}\n"
            f"任务类型/风险: {task.task_type}/{task.risk_level}\n"
            f"验收条件: {task.acceptance_checks}\n"
            f"允许工具: {list(tool_names) or ['无']}\n"
            f"最大工具调用数: {task.max_tool_calls}\n"
            f"依赖任务结果: {dependencies or '无'}\n"
            f"相关长期记忆: {task.memory_context or '无'}\n\n"
            "不要输出内部逐步推理。需要工具时直接调用工具；完成后只返回结论、证据和产物。"
        )

    def run(self, task: WorkerTask, event_sink: Optional[EventSink] = None) -> WorkerResult:
        started_at = time.monotonic()
        tools = self._resolve_tools(task)
        tool_by_name = {tool.name: tool for tool in tools}
        tool_names = list(tool_by_name)
        emit = event_sink or (lambda _event, _node, _payload: None)
        emit(
            "worker_started",
            self.spec.name,
            {
                "subtask_id": task.subtask_id,
                "worker": self.spec.name,
                "task_type": task.task_type,
                "risk_level": task.risk_level,
            },
        )
        emit(
            "worker_tool_policy",
            self.spec.name,
            {
                "subtask_id": task.subtask_id,
                "worker": self.spec.name,
                "allowed_tools": tool_names,
                "max_tool_calls": task.max_tool_calls,
            },
        )
        emit(
            "reasoning_update",
            self.spec.name,
            build_reasoning_update(
                "understand",
                f"理解子任务 {task.subtask_id}",
                f"目标是：{task.objective}。当前由 {self.spec.name} 在隔离上下文中处理。",
                next_action=(
                    f"从 {len(tool_names)} 个授权工具中选择必要能力并获取可验证证据。"
                    if tool_names else "无需外部工具，直接生成可验收结果。"
                ),
                subtask_id=task.subtask_id,
            ),
        )

        messages: List[Any] = [
            SystemMessage(content=self._system_prompt(task, tool_names)),
            HumanMessage(content=task.objective),
        ]
        evidence: List[WorkerEvidence] = []
        artifacts: List[WorkerArtifact] = []
        tool_calls: List[Dict[str, Any]] = []
        recent_signatures: List[str] = []
        token_count = 0
        step_count = 0
        summary = ""
        error: Optional[str] = None
        deadline = started_at + task.deadline_seconds

        try:
            for _round in range(max(2, task.max_tool_calls + 2)):
                if time.monotonic() >= deadline:
                    error = f"worker deadline exceeded ({task.deadline_seconds}s)"
                    break

                remaining = max(0, task.max_tool_calls - len(tool_calls))
                available_tools = tools if remaining > 0 else []
                model = self.model.bind_tools(available_tools) if available_tools else self.model
                response = model.invoke(messages)
                step_count += 1
                token_count += message_token_usage(response)
                requested = list(getattr(response, "tool_calls", []) or [])
                accepted = requested[:remaining]
                if len(requested) > len(accepted):
                    emit(
                        "worker_tool_budget_trimmed",
                        self.spec.name,
                        {
                            "subtask_id": task.subtask_id,
                            "requested": len(requested),
                            "accepted": len(accepted),
                        },
                    )
                if requested and not accepted:
                    messages.append(AIMessage(content="工具预算已用尽，请基于已有证据完成结果。"))
                    continue
                if not accepted:
                    summary = str(getattr(response, "content", "") or "").strip()
                    messages.append(response)
                    break

                violations = detect_tool_policy_violations(accepted, tool_names)
                if violations:
                    error = f"tool policy violation: {violations}"
                    emit(
                        "worker_tool_policy_violation",
                        self.spec.name,
                        {"subtask_id": task.subtask_id, "violations": violations},
                    )
                    break

                if len(accepted) != len(requested):
                    response = AIMessage(content=str(getattr(response, "content", "") or ""), tool_calls=accepted)
                messages.append(response)
                for call in accepted:
                    tool_name = str(call.get("name") or "")
                    args = call.get("args") if isinstance(call.get("args"), dict) else {}
                    signature = f"{tool_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"
                    if signature in recent_signatures:
                        error = f"repeated tool call detected: {tool_name}"
                        emit(
                            "worker_loop_detected",
                            self.spec.name,
                            {"subtask_id": task.subtask_id, "tool": tool_name},
                        )
                        break
                    recent_signatures.append(signature)
                    safe_args = _safe_tool_args(args)
                    emit(
                        "reasoning_update",
                        self.spec.name,
                        build_reasoning_update(
                            "decide",
                            f"决定调用 {tool_name}",
                            f"为完成子任务 {task.subtask_id} 并获得可验证依据，使用该工具。参数摘要："
                            f"{json.dumps(safe_args, ensure_ascii=False, default=str)}",
                            next_action="检查工具返回是否满足当前子任务的验收条件。",
                            subtask_id=task.subtask_id,
                        ),
                    )
                    emit(
                        "worker_tool_call_requested",
                        self.spec.name,
                        {"subtask_id": task.subtask_id, "worker": self.spec.name, "tool": tool_name, "args": safe_args},
                    )
                    console.emit_tool(tool_name, str(safe_args)[:180])
                    tool = tool_by_name.get(tool_name)
                    try:
                        output = str(tool.invoke(args)) if tool else f"tool unavailable: {tool_name}"
                    except Exception as exc:
                        output = f"tool execution error: {exc}"
                    tool_calls.append({"tool": tool_name, "args": safe_args, "ok": "error" not in output.lower() and "出错" not in output})
                    evidence.append(
                        WorkerEvidence(
                            kind="tool_result",
                            source=tool_name,
                            summary=output[:1600],
                            metadata={"args": safe_args},
                        )
                    )
                    artifact = _artifact_from_tool(tool_name, args, output)
                    if artifact:
                        artifacts.append(artifact)
                        emit(
                            "artifact_created",
                            self.spec.name,
                            {
                                "subtask_id": task.subtask_id,
                                "kind": artifact.kind,
                                "uri": artifact.uri,
                            },
                        )
                    emit(
                        "worker_tool_result",
                        self.spec.name,
                        {
                            "subtask_id": task.subtask_id,
                            "worker": self.spec.name,
                            "tool": tool_name,
                            "ok": tool_calls[-1]["ok"],
                            "output_length": len(output),
                            "observation_summary": build_reasoning_update(
                                "observe",
                                "工具观察",
                                output[:500],
                            )["summary"],
                        },
                    )
                    emit(
                        "reasoning_update",
                        self.spec.name,
                        build_reasoning_update(
                            "observe",
                            f"读取 {tool_name} 的返回",
                            output[:500],
                            next_action="结合已有证据判断是否还需调用工具，或进入子任务验收。",
                            subtask_id=task.subtask_id,
                            status="success" if tool_calls[-1]["ok"] else "warning",
                        ),
                    )
                    messages.append(
                        ToolMessage(
                            content=output[:5000],
                            tool_call_id=str(call.get("id") or f"call_{len(tool_calls)}"),
                            name=tool_name,
                        )
                    )
                    step_count += 1
                if error:
                    break
        except Exception as exc:
            error = f"worker execution error: {exc}"

        if not summary and evidence and not error:
            try:
                messages.append(HumanMessage(content="请基于已有工具证据给出当前子任务的最终结论，不再调用工具。"))
                response = self.model.invoke(messages)
                summary = str(getattr(response, "content", "") or "").strip()
                token_count += message_token_usage(response)
                step_count += 1
            except Exception as exc:
                error = f"worker synthesis error: {exc}"

        verification_text = "\n".join([summary, *[item.summary for item in evidence]])
        has_successful_tool_evidence = any(bool(item.get("ok")) for item in tool_calls)
        verification = verify_subtask_result(
            task.model_dump(),
            verification_text,
            stop_reason=error or "",
            has_tool_evidence=has_successful_tool_evidence,
        )
        if error:
            status: WorkerStatus = "replan_required" if "policy" in error else "retryable"
        elif verification.get("status") == "completed":
            status = "completed"
        elif verification.get("status") == "adjust":
            status = "replan_required"
        else:
            status = "retryable"

        elapsed = round(time.monotonic() - started_at, 3)
        emit(
            "worker_completed",
            self.spec.name,
            {
                "subtask_id": task.subtask_id,
                "worker": self.spec.name,
                "status": status,
                "tool_calls": len(tool_calls),
                "evidence_count": len(evidence),
                "artifact_count": len(artifacts),
                "elapsed_seconds": elapsed,
                "summary": (summary or error or "未生成结果")[:800],
                "evidence_sources": [item.source for item in evidence[:8]],
                "verification": verification,
            },
        )
        issues = [str(item) for item in verification.get("issues") or []]
        emit(
            "reasoning_update",
            self.spec.name,
            build_reasoning_update(
                "verify",
                f"验收子任务 {task.subtask_id}",
                (
                    f"验收状态为 {verification.get('status', status)}；"
                    f"{'发现问题：' + '；'.join(issues[:3]) if issues else f'已获得 {len(evidence)} 条证据和 {len(artifacts)} 个产物。'}"
                ),
                next_action=(
                    "将结果交回 Scheduler 并推进后续依赖任务。"
                    if status == "completed" else "由 Scheduler 决定重试、局部重规划或停止当前分支。"
                ),
                subtask_id=task.subtask_id,
                status="success" if status == "completed" else "warning" if status != "failed" else "error",
            ),
        )
        return WorkerResult(
            run_id=task.run_id,
            subtask_id=task.subtask_id,
            worker=self.spec.name,
            status=status,
            summary=summary or (error or "未生成可交付结果"),
            evidence=evidence,
            artifacts=artifacts,
            tool_calls=tool_calls,
            verification=verification,
            token_count=token_count,
            step_count=step_count,
            elapsed_seconds=elapsed,
            error=error,
        )


MUTATING_TASK_TYPES = {"write", "create", "execute"}


def select_dispatch_batch(plan: Dict[str, Any], parallelism: int) -> List[Dict[str, Any]]:
    """Select one safe DAG frontier batch while serializing mutating/high-risk tasks."""
    ready_ids = set(get_ready_subtasks(plan))
    ready = [st for st in plan.get("subtasks", []) if str(st.get("id")) in ready_ids]
    if not ready:
        return []

    first = ready[0]
    first_mutates = first.get("task_type") in MUTATING_TASK_TYPES or first.get("risk_level") == "high"
    if first_mutates:
        return [first]

    safe = [
        st
        for st in ready
        if st.get("task_type") not in MUTATING_TASK_TYPES and st.get("risk_level") != "high"
    ]
    return safe[: max(1, int(parallelism))] or [first]


class WorkerOrchestrator:
    def __init__(self, registry: WorkerRegistry) -> None:
        self.registry = registry

    def execute_batch(
        self,
        tasks: Sequence[WorkerTask],
        *,
        parallelism: int,
        event_sink: Optional[EventSink] = None,
    ) -> List[WorkerResult]:
        emit = event_sink or (lambda _event, _node, _payload: None)

        def execute(task: WorkerTask) -> WorkerResult:
            spec = self.registry.select(task)
            emit(
                "worker_dispatched",
                "scheduler",
                {
                    "subtask_id": task.subtask_id,
                    "worker": spec.name,
                    "task_type": task.task_type,
                    "risk_level": task.risk_level,
                },
            )
            return WorkerAgent(spec).run(task, event_sink=emit)

        if len(tasks) <= 1 or parallelism <= 1:
            return [execute(task) for task in tasks]

        results: Dict[str, WorkerResult] = {}
        with ThreadPoolExecutor(max_workers=min(parallelism, len(tasks)), thread_name_prefix="agent-worker") as pool:
            futures = {pool.submit(execute, task): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    results[task.subtask_id] = future.result()
                except Exception as exc:
                    results[task.subtask_id] = WorkerResult(
                        run_id=task.run_id,
                        subtask_id=task.subtask_id,
                        worker="scheduler",
                        status="retryable",
                        summary=f"worker crashed: {exc}",
                        verification={"status": "retry", "issues": [str(exc)]},
                        error=str(exc),
                    )
        return [results[task.subtask_id] for task in tasks]


worker_orchestrator = WorkerOrchestrator(worker_registry)


def dependency_result_map(subtask: Dict[str, Any], task_results: Dict[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for dependency in subtask.get("depends_on") or []:
        item = task_results.get(str(dependency)) or {}
        if isinstance(item, BaseModel):
            item = item.model_dump()
        if isinstance(item, dict):
            result[str(dependency)] = str(item.get("summary") or item.get("result") or "")[:2500]
    return result

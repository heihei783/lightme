import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.agent.runtime import TraceStore, build_plan_state_graph, normalize_plan
from app.agent.agent_graph import route_after_scheduler, scheduler_node
from app.agent.workers import (
    WorkerAgent,
    WorkerSpec,
    WorkerTask,
    WorkerResult,
    dependency_result_map,
    select_dispatch_batch,
    worker_registry,
)


@tool
def echo_tool(value: str) -> str:
    """Return a value as observed tool evidence."""
    return f"echo result: {value}"


class FakeToolModel:
    def __init__(self):
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo_tool",
                        "args": {"value": "ok"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="echo result: ok，执行结果已验证")


class WorkerRuntimeTest(unittest.TestCase):
    def test_registry_selects_worker_by_capability(self):
        research = WorkerTask(
            run_id="run",
            session_id="session",
            subtask_id="r",
            objective="搜索资料",
            task_type="research",
        )
        execute = research.model_copy(update={"subtask_id": "e", "task_type": "execute"})
        self.assertEqual(worker_registry.select(research).name, "research_worker")
        self.assertEqual(worker_registry.select(execute).name, "execution_worker")

    def test_scheduler_parallelizes_only_safe_frontier(self):
        plan = normalize_plan(
            {
                "goal": "demo",
                "subtasks": [
                    {"id": "a", "desc": "读取文件", "task_type": "read"},
                    {"id": "b", "desc": "搜索资料", "task_type": "research"},
                    {"id": "c", "desc": "执行命令", "task_type": "execute", "risk_level": "high"},
                ],
            },
            goal="demo",
            complexity="complex",
        )
        self.assertEqual([item["id"] for item in select_dispatch_batch(plan, 4)], ["a", "b"])

        plan["subtasks"] = [plan["subtasks"][2], plan["subtasks"][0], plan["subtasks"][1]]
        self.assertEqual([item["id"] for item in select_dispatch_batch(plan, 4)], ["c"])

    def test_worker_executes_real_tool_with_isolated_messages(self):
        events = []
        spec = WorkerSpec(
            name="test_worker",
            description="test",
            task_types=frozenset({"analyze"}),
            tool_names=frozenset({"echo_tool"}),
        )
        task = WorkerTask(
            run_id="run",
            session_id="session",
            subtask_id="one",
            objective="调用工具并返回 echo result",
            expected_result="echo result",
            task_type="analyze",
            allowed_tools=["echo_tool"],
            max_tool_calls=1,
        )
        result = WorkerAgent(spec, model=FakeToolModel(), base_tools=[echo_tool]).run(
            task,
            event_sink=lambda event, node, payload: events.append((event, node, payload)),
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_calls[0]["tool"], "echo_tool")
        self.assertEqual(result.evidence[0].source, "echo_tool")
        self.assertIn("worker_completed", [item[0] for item in events])
        reasoning_events = [item for item in events if item[0] == "reasoning_update"]
        self.assertGreaterEqual(len(reasoning_events), 4)
        self.assertEqual(reasoning_events[0][2]["visibility"], "public_summary")
        self.assertIn("decide", [item[2]["phase"] for item in reasoning_events])
        self.assertIn("observe", [item[2]["phase"] for item in reasoning_events])
        self.assertIn("verify", [item[2]["phase"] for item in reasoning_events])

    def test_worker_tools_are_intersected_with_runtime_boundary(self):
        spec = WorkerSpec(
            name="restricted_worker",
            description="test runtime boundary",
            task_types=frozenset({"analyze"}),
            tool_names=frozenset({"echo_tool"}),
        )
        agent = WorkerAgent(spec, model=FakeToolModel(), base_tools=[echo_tool])
        denied_task = WorkerTask(
            run_id="run",
            session_id="session",
            subtask_id="denied",
            objective="echo",
            task_type="analyze",
            allowed_tools=["echo_tool"],
            runtime_allowed_tools=["knowledge_search"],
        )
        allowed_task = denied_task.model_copy(
            update={"subtask_id": "allowed", "runtime_allowed_tools": ["echo_tool"]}
        )

        self.assertEqual(agent._resolve_tools(denied_task), [])
        self.assertEqual([tool.name for tool in agent._resolve_tools(allowed_task)], ["echo_tool"])

    def test_dependency_results_only_include_declared_dependencies(self):
        subtask = {"id": "c", "depends_on": ["a"]}
        results = {
            "a": {"summary": "allowed"},
            "b": {"summary": "must not leak"},
        }
        self.assertEqual(dependency_result_map(subtask, results), {"a": "allowed"})

    def test_running_state_is_not_redispatched(self):
        plan = normalize_plan(
            {"goal": "demo", "subtasks": [{"id": "a", "desc": "read", "task_type": "read"}]},
            goal="demo",
            complexity="simple",
        )
        plan["subtasks"][0]["status"] = "running"
        graph = build_plan_state_graph(plan)
        self.assertEqual(graph["running"], ["a"])
        self.assertEqual(graph["frontier"], [])

    def test_trace_store_accepts_parallel_worker_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TraceStore(db_path=f"{tmp}/trace.db", jsonl_dir=f"{tmp}/jsonl")
            store.start_run("run_parallel", "session", "goal")
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [
                    pool.submit(store.event, "run_parallel", "session", "worker_event", f"worker_{i % 2}", {"i": i})
                    for i in range(20)
                ]
                for future in futures:
                    future.result()
            trace = store.get_trace("run_parallel")
            self.assertEqual(len(trace["events"]), 20)

    def test_scheduler_merges_parallel_worker_results_and_finishes(self):
        plan = normalize_plan(
            {
                "goal": "demo",
                "subtasks": [
                    {"id": "a", "desc": "analyze a", "task_type": "analyze"},
                    {"id": "b", "desc": "analyze b", "task_type": "analyze"},
                ],
            },
            goal="demo",
            complexity="complex",
        )
        state = {
            "plan": plan,
            "run_id": "run_scheduler",
            "session_id": "session",
            "started_at": time.time(),
            "step_count": 1,
            "token_count": 0,
            "trace_enabled": False,
            "stop_reason": "",
            "task_results": {},
            "artifacts": [],
            "scheduler_epoch": 0,
            "replan_count": 0,
            "run_context": {"current_request": "demo"},
            "memory_context": "",
        }
        worker_results = [
            WorkerResult(
                run_id="run_scheduler",
                subtask_id=task_id,
                worker="research_worker",
                status="completed",
                summary=f"result {task_id}",
                verification={"status": "completed", "issues": []},
                step_count=1,
                token_count=10,
            )
            for task_id in ("a", "b")
        ]
        with patch(
            "app.agent.agent_graph.worker_orchestrator.execute_batch",
            return_value=worker_results,
        ), patch("app.agent.agent_graph.agent_memory.save_episodic"):
            result = scheduler_node(state)

        merged_state = {**state, **result}
        self.assertEqual([item["status"] for item in result["plan"]["subtasks"]], ["completed", "completed"])
        self.assertEqual(set(result["task_results"]), {"a", "b"})
        self.assertEqual(result["scheduler_epoch"], 1)
        self.assertEqual(route_after_scheduler(merged_state), "finalize")


if __name__ == "__main__":
    unittest.main()

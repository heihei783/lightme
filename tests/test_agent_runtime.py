import tempfile
import unittest

from app.agent.runtime import (
    TraceStore,
    allowed_tools_for_subtask,
    build_reasoning_update,
    build_execution_handoff,
    build_plan_state_graph,
    detect_tool_policy_violations,
    get_ready_subtasks,
    format_handoff_context,
    is_continuation_request,
    normalize_plan,
    repair_plan_for_capabilities,
    sanitize_reasoning_text,
    score_plan_quality,
    should_replan,
    update_plan_after_subtask,
    validate_plan,
    verify_subtask_result,
)


class PlannerRuntimeTest(unittest.TestCase):
    def test_public_reasoning_update_is_bounded_and_redacted(self):
        payload = build_reasoning_update(
            "decide",
            "选择工具",
            "调用接口，api_key=very-secret-value，Authorization: Bearer abc.def.ghi",
            next_action="读取结果后验证",
            subtask_id="task-1",
        )
        self.assertEqual(payload["visibility"], "public_summary")
        self.assertEqual(payload["phase"], "decide")
        self.assertNotIn("very-secret-value", payload["summary"])
        self.assertNotIn("abc.def.ghi", payload["summary"])
        self.assertIn("***", payload["summary"])
        self.assertEqual(payload["subtask_id"], "task-1")

    def test_reasoning_text_is_single_line(self):
        self.assertEqual(sanitize_reasoning_text("第一行\n  第二行"), "第一行 第二行")

    def test_normalize_plan_adds_version_and_status(self):
        plan = normalize_plan(
            {
                "goal": "demo",
                "decision_summary": "先读取配置，再验证结果。",
                "subtasks": [
                    {"id": 1, "desc": "first"},
                    {"id": 2, "desc": "second", "depends_on": [1]},
                ],
            },
            goal="demo",
            complexity="medium",
        )
        self.assertEqual(plan["version"], 1)
        self.assertEqual(plan["subtasks"][0]["id"], "1")
        self.assertEqual(plan["subtasks"][1]["depends_on"], ["1"])
        self.assertEqual(plan["ready_subtasks"], ["1"])
        self.assertIn(plan["subtasks"][0]["task_type"], {"general", "read", "analyze"})
        self.assertIn(plan["subtasks"][0]["risk_level"], {"low", "medium", "high"})
        self.assertTrue(plan["subtasks"][0]["acceptance_checks"])
        self.assertEqual(plan["decision_summary"], "先读取配置，再验证结果。")

    def test_validate_plan_detects_cycle(self):
        plan = {
            "subtasks": [
                {"id": "a", "depends_on": ["b"]},
                {"id": "b", "depends_on": ["a"]},
            ]
        }
        self.assertTrue(any("cycle" in err for err in validate_plan(plan)))

    def test_update_plan_after_subtask_schedules_next_ready(self):
        plan = normalize_plan(
            {
                "goal": "demo",
                "subtasks": [
                    {"id": "a", "desc": "first"},
                    {"id": "b", "desc": "second", "depends_on": ["a"]},
                ],
            },
            goal="demo",
            complexity="medium",
        )
        plan["subtasks"][0]["status"] = "completed"
        updated = update_plan_after_subtask(plan)
        self.assertEqual(get_ready_subtasks(updated), ["b"])
        self.assertEqual(updated["current_subtask"], 2)
        self.assertEqual(updated["state_graph"]["frontier"], ["b"])

    def test_trace_store_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TraceStore(db_path=f"{tmp}/trace.db", jsonl_dir=f"{tmp}/jsonl")
            store.start_run("run_test", "session_test", "goal")
            plan = normalize_plan({"goal": "goal", "subtasks": [{"id": 1, "desc": "x"}]}, "goal", "simple")
            store.record_plan("run_test", plan)
            store.event("run_test", "session_test", "test_event", "node", {"ok": True})
            store.finish_run("run_test", "completed", "done", {"step_count": 1})
            trace = store.get_trace("run_test")
            self.assertEqual(trace["run"]["status"], "completed")
            self.assertEqual(len(trace["plans"]), 1)
            self.assertEqual(trace["events"][0]["payload"]["ok"], True)

    def test_execution_handoff_preserves_auditable_context(self):
        plan = normalize_plan(
            {"goal": "inspect", "subtasks": [{"id": "a", "desc": "inspect file", "task_type": "read"}]},
            goal="inspect",
            complexity="medium",
        )
        plan["subtasks"][0].update(
            {
                "status": "completed",
                "result": "found config at C:/project/config.json",
                "worker": "research_worker",
                "evidence": [
                    {"kind": "tool_result", "source": "read_file_content", "summary": "config contains planner settings"}
                ],
                "tool_calls": [{"tool": "read_file_content", "args": {"file_path": "C:/project/config.json"}, "ok": True}],
                "verifier": {"status": "completed", "issues": [], "passed_checks": ["result_is_non_empty"]},
            }
        )
        handoff = build_execution_handoff(
            run_id="run_handoff",
            goal="inspect",
            status="completed",
            plan=plan,
            final_output="done",
            artifacts=[],
        )
        context = format_handoff_context([handoff])
        self.assertIn("read_file_content", context)
        self.assertIn("config contains planner settings", context)
        self.assertIn("验收: completed", context)
        self.assertIn("规划依据", context)

    def test_handoffs_are_session_scoped_and_deleted_with_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TraceStore(db_path=f"{tmp}/trace.db", jsonl_dir=f"{tmp}/jsonl")
            store.start_run("run_a", "session_a", "goal")
            store.save_handoff("run_a", "session_a", {"goal": "goal", "status": "completed"})
            self.assertEqual(len(store.get_session_handoffs("session_a")), 1)
            self.assertEqual(store.get_session_handoffs("session_b"), [])
            store.delete_session_data("session_a")
            self.assertEqual(store.get_session_handoffs("session_a"), [])
            self.assertIsNone(store.get_run("run_a"))

    def test_continuation_detection_requires_explicit_reference(self):
        self.assertTrue(is_continuation_request("继续执行刚才的计划"))
        self.assertTrue(is_continuation_request("用上一个文件再试一次"))
        self.assertFalse(is_continuation_request("给我讲一个笑话"))

    def test_plan_quality_scores_missing_expected_result(self):
        plan = normalize_plan(
            {"goal": "demo", "subtasks": [{"id": "a", "desc": "short"}]},
            goal="demo",
            complexity="complex",
        )
        quality = score_plan_quality(plan)
        self.assertLess(quality["score"], 100)
        self.assertTrue(any("expected_result" in item for item in quality["warnings"]))

    def test_repair_plan_replaces_unknown_skill(self):
        plan = normalize_plan(
            {
                "goal": "demo",
                "subtasks": [{"id": "a", "desc": "搜索最新资讯", "skill": "missing_skill"}],
            },
            goal="demo",
            complexity="medium",
        )
        repaired = repair_plan_for_capabilities(
            plan,
            [{"name": "web_searcher", "description": "搜索 查询 资讯", "category": "search", "keywords": ["搜索"]}],
        )
        self.assertEqual(repaired["subtasks"][0]["skill"], "web_searcher")
        self.assertEqual(repaired["capability_repairs"][0]["action"], "skill_replaced")

    def test_verify_subtask_result_detects_error_and_replan(self):
        plan = normalize_plan(
            {"goal": "demo", "subtasks": [{"id": "a", "desc": "执行命令", "expected_result": "返回命令输出"}]},
            goal="demo",
            complexity="medium",
        )
        subtask = plan["subtasks"][0]
        verifier = verify_subtask_result(subtask, "Traceback: boom")
        self.assertEqual(verifier["status"], "retry")
        self.assertTrue(should_replan(plan, subtask, {"status": "adjust"}, 0, 3))

    def test_execute_subtask_requires_execution_evidence(self):
        plan = normalize_plan(
            {
                "goal": "demo",
                "subtasks": [
                    {
                        "id": "run",
                        "desc": "运行单元测试",
                        "expected_result": "返回测试结果",
                        "task_type": "execute",
                    }
                ],
            },
            goal="demo",
            complexity="medium",
        )
        verifier = verify_subtask_result(plan["subtasks"][0], "我认为测试已经完成")
        self.assertEqual(verifier["status"], "retry")
        self.assertTrue(any("execution status" in issue for issue in verifier["issues"]))

    def test_execute_subtask_accepts_observed_tool_result(self):
        plan = normalize_plan(
            {
                "goal": "demo",
                "subtasks": [
                    {
                        "id": "inspect",
                        "desc": "查询系统信息",
                        "expected_result": "返回系统版本和架构",
                        "task_type": "execute",
                    }
                ],
            },
            goal="demo",
            complexity="simple",
        )
        verifier = verify_subtask_result(
            plan["subtasks"][0],
            "system: Windows\nrelease: 11\nmachine: AMD64",
            has_tool_evidence=True,
        )
        self.assertEqual(verifier["status"], "completed")
        self.assertIn("execution_status", verifier["passed_checks"])

    def test_allowed_tools_follow_subtask_policy(self):
        subtask = {
            "desc": "读取 README 并总结",
            "task_type": "read",
            "risk_level": "low",
        }
        tools = allowed_tools_for_subtask(
            subtask,
            ["read_file_content", "write_file_content", "execute_shell_command", "list_directory"],
        )
        self.assertIn("read_file_content", tools)
        self.assertIn("list_directory", tools)
        self.assertNotIn("execute_shell_command", tools)
        self.assertNotIn("write_file_content", tools)

    def test_general_subtask_can_disable_tools(self):
        tools = allowed_tools_for_subtask(
            {"desc": "直接回答", "task_type": "general", "allowed_tools": []},
            ["read_file_content", "execute_shell_command"],
        )
        self.assertEqual(tools, [])

    def test_tool_policy_detects_disallowed_call(self):
        violations = detect_tool_policy_violations(
            [{"name": "execute_shell_command", "args": {"command": "dir"}}],
            ["read_file_content"],
        )
        self.assertEqual(violations[0]["tool"], "execute_shell_command")

    def test_dynamic_state_graph_tracks_frontier_and_blocked_nodes(self):
        plan = normalize_plan(
            {
                "goal": "demo",
                "subtasks": [
                    {"id": "a", "desc": "collect info", "expected_result": "info"},
                    {"id": "b", "desc": "write result", "expected_result": "file", "depends_on": ["a"]},
                    {"id": "c", "desc": "verify result", "expected_result": "ok", "depends_on": ["b"]},
                ],
            },
            goal="demo",
            complexity="complex",
        )
        self.assertEqual(plan["state_graph"]["frontier"], ["a"])
        plan["subtasks"][0]["status"] = "failed"
        graph = build_plan_state_graph(plan)
        self.assertEqual(graph["summary"]["failed"], 1)
        self.assertIn("b", graph["blocked"])

    def test_adjust_status_is_visible_as_needs_replan(self):
        plan = normalize_plan(
            {"goal": "demo", "subtasks": [{"id": "a", "desc": "repair task", "expected_result": "new plan"}]},
            goal="demo",
            complexity="medium",
        )
        plan["subtasks"][0]["status"] = "adjust"
        graph = build_plan_state_graph(plan)
        self.assertIn("a", graph["needs_replan"])
        self.assertEqual(graph["nodes"][0]["state"], "needs_replan")


if __name__ == "__main__":
    unittest.main()

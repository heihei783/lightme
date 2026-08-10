import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.tools import DEFAULT_TOOLS
from app.llm import llm_chain


class UnifiedRuntimeTest(unittest.TestCase):
    def test_explicit_follow_up_reuses_agent_route(self):
        state = {
            "messages": [HumanMessage(content="继续执行刚才的任务")],
            "session_id": "session",
            "execution_context": "目标: 读取项目配置\n子任务 1: 已完成",
        }
        with patch.dict(llm_chain.config_ai, {"agent_open": True, "rag_open": True}):
            result = llm_chain.router_node(state)
        self.assertEqual(result["route"], "agent")
        self.assertIn("knowledge_search", result["runtime_allowed_tools"])
        self.assertIn("read_file_content", result["runtime_allowed_tools"])

    def test_rag_only_mode_routes_through_restricted_agent(self):
        state = {
            "messages": [HumanMessage(content="查询知识库里的项目说明")],
            "session_id": "session",
            "execution_context": "",
        }
        model = Mock()
        model.invoke.return_value = AIMessage(content="agent")
        with patch.dict(llm_chain.config_ai, {"agent_open": False, "rag_open": True}), patch(
            "app.llm.llm_chain.chat_model",
            model,
        ):
            result = llm_chain.router_node(state)
        self.assertEqual(result["route"], "agent")
        self.assertEqual(result["runtime_allowed_tools"], ["knowledge_search"])

    def test_knowledge_search_is_a_first_class_runtime_tool(self):
        tool_names = {tool.name for tool in DEFAULT_TOOLS}
        self.assertIn("knowledge_search", tool_names)

    def test_no_external_capability_uses_direct_route(self):
        state = {
            "messages": [HumanMessage(content="你好")],
            "session_id": "session",
            "execution_context": "",
        }
        with patch.dict(llm_chain.config_ai, {"agent_open": False, "rag_open": False}):
            result = llm_chain.router_node(state)
        self.assertEqual(result["route"], "direct")
        self.assertEqual(result["runtime_allowed_tools"], [])

    def test_model_facing_history_removes_storage_timestamp(self):
        cleaned = llm_chain._clean_history_messages(
            [HumanMessage(content="[2026-07-28 17:59:48] 给我绝对路径")]
        )
        self.assertEqual(cleaned[0].content, "给我绝对路径")


if __name__ == "__main__":
    unittest.main()

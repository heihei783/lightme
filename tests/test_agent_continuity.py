import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from app.llm.llm_chain import _clean_history_messages, router_node
from utils.config_handler import config_ai


class AgentContinuityTest(unittest.TestCase):
    def test_explicit_follow_up_stays_on_agent_route_without_model_call(self):
        state = {
            "messages": [HumanMessage(content="继续执行刚才的计划")],
            "session_id": "session",
            "execution_context": "上一轮使用 read_file_content 找到了配置文件",
            "route": "",
            "context": "",
        }
        with patch.dict(config_ai, {"agent_open": True, "rag_open": False}), patch(
            "app.llm.llm_chain.chat_model"
        ) as model:
            result = router_node(state)
        self.assertEqual(result["route"], "agent")
        model.invoke.assert_not_called()

    def test_model_history_drops_storage_timestamp_prefix(self):
        original = HumanMessage(content="[2026-07-28 20:49:26] 给我绝对路径")
        cleaned = _clean_history_messages([original])
        self.assertEqual(cleaned[0].content, "给我绝对路径")
        self.assertTrue(original.content.startswith("[2026-07-28"))


if __name__ == "__main__":
    unittest.main()

import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from web import web_py


class ChatStreamingApiTest(unittest.TestCase):
    def test_chat_stream_disables_proxy_buffering(self):
        session_id = f"stream_test_{uuid.uuid4().hex}"
        with patch.object(web_py, "chat_loop", return_value=iter(["你", "好"])):
            response = TestClient(web_py.app).post(
                "/chat",
                json={"session_id": session_id, "message": "test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "你好")
        self.assertEqual(response.headers["x-session-id"], session_id)
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertEqual(response.headers["cache-control"], "no-cache, no-transform")


if __name__ == "__main__":
    unittest.main()

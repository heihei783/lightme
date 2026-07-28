import asyncio
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from PIL import Image

from web import web_py


class AvatarApiTest(unittest.TestCase):
    @staticmethod
    def _upload_file():
        buffer = BytesIO()
        Image.new("RGB", (800, 600), (32, 96, 160)).save(buffer, format="PNG")
        buffer.seek(0)
        return UploadFile(file=buffer, filename="portrait.png")

    def test_upload_avatar_returns_metadata_and_updates_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            avatar_dir = Path(tmp) / "avatars"
            config_path = Path(tmp) / "avatar_config.json"
            with (
                patch.object(web_py, "AVATAR_DIR", str(avatar_dir)),
                patch.object(web_py, "AVATAR_CONFIG_PATH", str(config_path)),
            ):
                result = asyncio.run(web_py.upload_avatar(self._upload_file(), type="user"))

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["filename"].endswith(".webp"))
            self.assertEqual((result["avatar"]["width"], result["avatar"]["height"]), (600, 600))
            self.assertTrue((avatar_dir / result["filename"]).is_file())
            with config_path.open("r", encoding="utf-8") as file:
                self.assertEqual(json.load(file)["user_avatar"], result["filename"])


if __name__ == "__main__":
    unittest.main()

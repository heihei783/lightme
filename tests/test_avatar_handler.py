import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from utils.avatar_handler import AvatarProcessingError, inspect_avatar, process_avatar_bytes


class AvatarHandlerTest(unittest.TestCase):
    @staticmethod
    def _image_bytes(size=(1600, 900), mode="RGB", image_format="PNG"):
        image = Image.new(mode, size, (40, 120, 200, 180) if mode == "RGBA" else (40, 120, 200))
        buffer = BytesIO()
        image.save(buffer, format=image_format)
        return buffer.getvalue()

    def test_avatar_is_center_cropped_without_upscaling(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "avatar.webp"
            metadata = process_avatar_bytes(self._image_bytes(), output)

            self.assertEqual((metadata["width"], metadata["height"]), (900, 900))
            self.assertEqual((metadata["source_width"], metadata["source_height"]), (1600, 900))
            self.assertEqual(inspect_avatar(output)["format"], "webp")

    def test_avatar_keeps_transparency(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "avatar.webp"
            process_avatar_bytes(self._image_bytes((600, 600), mode="RGBA"), output)

            with Image.open(output) as image:
                self.assertEqual(image.mode, "RGBA")

    def test_avatar_rejects_low_resolution_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(AvatarProcessingError, "256px"):
                process_avatar_bytes(self._image_bytes((128, 128)), Path(tmp) / "avatar.webp")


if __name__ == "__main__":
    unittest.main()

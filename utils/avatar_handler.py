from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_AVATAR_BYTES = 10 * 1024 * 1024
MAX_AVATAR_PIXELS = 40_000_000
MIN_AVATAR_EDGE = 256
MAX_AVATAR_EDGE = 1024
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


class AvatarProcessingError(ValueError):
    pass


def process_avatar_bytes(
    content: bytes,
    output_path: str | os.PathLike[str],
    *,
    min_edge: int = MIN_AVATAR_EDGE,
    max_edge: int = MAX_AVATAR_EDGE,
) -> dict[str, Any]:
    """Validate and store a centered, high-quality square avatar."""
    if not content:
        raise AvatarProcessingError("头像文件为空")
    if len(content) > MAX_AVATAR_BYTES:
        raise AvatarProcessingError("头像文件不能超过 10MB")

    try:
        with Image.open(BytesIO(content)) as source:
            source_format = str(source.format or "").upper()
            if source_format not in SUPPORTED_FORMATS:
                raise AvatarProcessingError("仅支持 PNG、JPEG、WebP 或 GIF 图片")

            source_width, source_height = source.size
            if source_width * source_height > MAX_AVATAR_PIXELS:
                raise AvatarProcessingError("头像像素过大，请使用 4000 万像素以内的图片")
            if min(source_width, source_height) < min_edge:
                raise AvatarProcessingError(f"头像短边至少需要 {min_edge}px")

            source.seek(0)
            image = ImageOps.exif_transpose(source)
            has_alpha = image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            output_edge = min(max_edge, source_width, source_height)
            avatar = ImageOps.fit(
                image,
                (output_edge, output_edge),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )

            target = Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f"{target.name}.tmp")
            try:
                avatar.save(
                    temporary,
                    format="WEBP",
                    quality=95,
                    method=6,
                    lossless=has_alpha,
                )
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
    except AvatarProcessingError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AvatarProcessingError("无法识别或处理该头像图片") from exc

    return {
        "width": output_edge,
        "height": output_edge,
        "source_width": source_width,
        "source_height": source_height,
        "format": "webp",
        "bytes": Path(output_path).stat().st_size,
    }


def inspect_avatar(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read lightweight dimensions for existing and newly normalized avatars."""
    avatar_path = Path(path)
    with Image.open(avatar_path) as image:
        width, height = image.size
        image_format = str(image.format or avatar_path.suffix.lstrip(".")).lower()
    return {
        "width": width,
        "height": height,
        "format": image_format,
        "bytes": avatar_path.stat().st_size,
    }

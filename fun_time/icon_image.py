from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


def load_icon_image(ico_path: Path, size: int) -> PILImage | None:
    """An ICO as an RGBA PIL Image at *size*, or None without the file or
    Pillow."""
    try:
        from PIL import Image

        img = Image.open(ico_path)
        img = img.resize((size, size), Image.LANCZOS)  # largest, then downsample
        return img.convert("RGBA")
    except (ImportError, OSError):
        return None  # PIL's UnidentifiedImageError is an OSError

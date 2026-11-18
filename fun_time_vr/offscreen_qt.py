from __future__ import annotations

from PIL import Image
from PySide6.QtGui import QImage


def as_rgba_bytes(image: QImage) -> tuple[int, int, bytes]:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return rgba.width(), rgba.height(), bytes(rgba.constBits())


def as_pil(image: QImage) -> Image.Image:
    width, height, pixels = as_rgba_bytes(image)
    return Image.frombytes("RGBA", (width, height), pixels)

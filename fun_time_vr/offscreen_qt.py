from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from shared_ui.application import application


def _windows_fonts_offscreen_qt_lacks() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


@contextmanager
def offscreen_application() -> Iterator[QApplication]:
    os.environ.setdefault("QT_QPA_FONTDIR", str(_windows_fonts_offscreen_qt_lacks()))
    with application([sys.argv[0], "-platform", "offscreen"]) as app:
        yield app


def as_rgba_bytes(image: QImage) -> tuple[int, int, bytes]:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return rgba.width(), rgba.height(), bytes(rgba.constBits())


def as_pil(image: QImage) -> Image.Image:
    width, height, pixels = as_rgba_bytes(image)
    return Image.frombytes("RGBA", (width, height), pixels)

from __future__ import annotations

from PIL import Image
from PySide6.QtGui import QColor, QImage

from fun_time_vr.offscreen_qt import as_pil, as_rgba_bytes


def test_a_qt_image_comes_out_as_the_same_pixels_either_way():
    image = QImage(3, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor(10, 20, 30, 255))

    width, height, pixels = as_rgba_bytes(image)

    assert (width, height) == (3, 2)
    assert pixels[:4] == bytes((10, 20, 30, 255))
    assert as_pil(image).tobytes() == Image.new("RGBA", (3, 2), (10, 20, 30, 255)).tobytes()

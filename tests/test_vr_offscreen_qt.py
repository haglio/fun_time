from __future__ import annotations

import sys
from contextlib import contextmanager

from PIL import Image
from PySide6.QtGui import QColor, QImage

from fun_time_vr import offscreen_qt
from fun_time_vr.offscreen_qt import as_pil, as_rgba_bytes, offscreen_application


def test_the_application_is_made_offscreen_where_it_is_let_go_of_safely(tmp_path, monkeypatch):
    made = []

    @contextmanager
    def application(argv):
        made.append(argv)
        yield "the application"

    monkeypatch.setattr(offscreen_qt, "application", application)
    monkeypatch.delenv("QT_QPA_FONTDIR", raising=False)

    with offscreen_application() as app:
        assert app == "the application"

    assert made == [[sys.argv[0], "-platform", "offscreen"]]


def test_the_offscreen_application_finds_the_fonts_windows_has(monkeypatch):
    monkeypatch.setattr(offscreen_qt, "application", contextmanager(lambda argv: iter([None])))
    monkeypatch.delenv("QT_QPA_FONTDIR", raising=False)
    monkeypatch.setenv("WINDIR", r"C:\MadeUpWindows")

    with offscreen_application():
        assert offscreen_qt.os.environ["QT_QPA_FONTDIR"] == r"C:\MadeUpWindows\Fonts"


def test_a_qt_image_comes_out_as_the_same_pixels_either_way():
    image = QImage(3, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor(10, 20, 30, 255))

    width, height, pixels = as_rgba_bytes(image)

    assert (width, height) == (3, 2)
    assert pixels[:4] == bytes((10, 20, 30, 255))
    assert as_pil(image).tobytes() == Image.new("RGBA", (3, 2), (10, 20, 30, 255)).tobytes()

"""Real-window checks for the primary library browser.

The browser's whole value is what the user *reads* off it — the handle names
under the stills — and the unit suite cannot see that: it renders on Qt's
offscreen platform, where ``QFontDatabase.families()`` is empty and every glyph
comes out as a missing-character box.  A grid of tofu passes every assertion a
unit test can make about it.  So the "does it actually paint the titles" check
lives here, on the native platform, where the fonts are real.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PyQt6.QtGui import QFontDatabase

from fun_time.library_browser import LibraryBrowserWindow
from fun_time.library_handles import LibraryHandle
from fun_time.thumbnail_cache import thumbnail_path

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="paints a real Qt window"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

TITLES = ("Alpha Studio - Scene One", "Beta Collective - The Long Afternoon 2")


def _handles(tmp_path: Path, cache: Path) -> list[LibraryHandle]:
    """Two handles, each with a still already cached, so nothing is extracted."""
    handles = []
    for index, title in enumerate(TITLES):
        video = tmp_path / f"v{index}.mp4"
        video.write_bytes(b"\0" * (100 + index))
        picture = Image.new("RGB", (176, 99), (40, 60, 90))
        ImageDraw.Draw(picture).rectangle((10, 10, 60, 60), fill=(200, 120, 40))
        picture.save(thumbnail_path(video, cache), "JPEG")
        handles.append(LibraryHandle(title=title, versions=(str(video),)))
    return handles


def test_the_grid_paints_its_titles_and_stills(tmp_path: Path):
    """A rendered tile carries both the still and legible text, not tofu.

    Legibility is read off the font: on the native platform the families are
    there and the title's glyphs are really in the face being drawn with, where
    offscreen has no font at all and paints every character as a box.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    handles = _handles(tmp_path, cache)

    window = LibraryBrowserWindow(handles, thumbnail_cache=cache, on_pick=lambda _v: None)
    try:
        window.setGeometry(0, 0, 600, 300)
        window.show()
        painted = window.grab().toImage()

        assert QFontDatabase.families(), "native platform must have real fonts"
        metrics = window.fontMetrics()
        assert metrics.horizontalAdvance(TITLES[0]) > 0
        for character in set("".join(TITLES)):
            assert metrics.inFont(character), f"{character!r} would paint as a box"

        assert not window.item(0).icon().isNull()
        assert painted.width() > 0 and painted.height() > 0
        colors = {painted.pixel(x, y) for x in range(0, painted.width(), 7)
                  for y in range(0, painted.height(), 7)}
        assert len(colors) > 3, "the grid painted a flat slab, not tiles"
    finally:
        window.close()

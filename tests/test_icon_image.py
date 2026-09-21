from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from fun_time.icon_image import load_icon_image

ICON_PATH = Path(__file__).resolve().parent.parent / "icon.ico"


class TestLoadIconImage:
    def test_loads_icon_at_requested_size(self):
        img = load_icon_image(ICON_PATH, 128)
        assert img.size == (128, 128)

    def test_an_icon_file_that_is_not_there_loads_as_no_icon(self):
        result = load_icon_image(Path("nonexistent.ico"), 128)
        assert result is None


class TestLoadingTheIcon:
    """The three ways this can fail, each of which used to look identical to a
    working cover.  Narrowed from `except Exception`, so a failure this does not
    expect now reaches the log instead of being read as "no icon"."""

    def test_a_file_that_is_not_an_image_is_no_icon(self, tmp_path: Path):
        """PIL's UnidentifiedImageError is an OSError."""
        not_an_image = tmp_path / "notes.ico"
        not_an_image.write_text("this is not an icon", encoding="utf-8")

        assert load_icon_image(not_an_image, 128) is None

    def test_a_run_without_pillow_is_no_icon(self):
        """The covers are the one part of the session that needs Pillow, and a
        run without it must still put the scrim up."""
        with patch.dict(sys.modules, {"PIL": None}):
            assert load_icon_image(ICON_PATH, 128) is None

    def test_the_icon_is_resized_to_what_was_asked_for(self):
        assert load_icon_image(ICON_PATH, 64).size == (64, 64)

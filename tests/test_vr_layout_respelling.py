"""The headset's layout file, rewritten once from last session's spelling.

Until 2026-09-13 the file called the main screen "primary" -- a word this room
keeps for a monitor -- and a screen the reader finds nothing for opens in its own
default spot, so a bare rename would have lost where he placed the main screen.
It is rewritten at the startup that first reads it; the reader knows only "main".
"""
from __future__ import annotations

import json
from pathlib import Path

from fun_time_vr.layout import (
    LAYOUT_FILENAME,
    MAIN,
    PORTRAIT,
    migrate_layout,
    read_layout,
    write_layout,
)
from fun_time_vr.scene import Placement


class TestTheHeadsetLayoutFile:
    """The file called the main screen "primary" -- a word this room keeps for
    a monitor -- and a screen the reader finds nothing for opens in its own
    spot, so a bare rename would have lost where he placed the main screen."""

    def test_the_remembered_main_screen_survives_the_rename(self, tmp_path: Path):
        path = tmp_path / LAYOUT_FILENAME
        moved = {"azimuth_deg": 6.5, "elevation_deg": -14.9, "width_deg": 82.1}
        path.write_text(json.dumps({
            "primary": moved,
            "portrait": {"azimuth_deg": 47.6, "elevation_deg": -21.7, "width_deg": 41.6},
        }), encoding="utf-8")

        assert migrate_layout(path) is True

        assert read_layout(path)[MAIN] == Placement(6.5, -14.9, 82.1)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "primary" not in raw
        assert raw["main"] == moved
        assert raw["portrait"]["width_deg"] == 41.6

    def test_a_file_already_in_todays_spelling_is_left_alone(self, tmp_path: Path):
        path = tmp_path / LAYOUT_FILENAME
        write_layout(path, {MAIN: Placement(6.5, -14.9, 82.1),
                            PORTRAIT: Placement(47.6, -21.7, 41.6)})
        before = path.read_text(encoding="utf-8")

        assert migrate_layout(path) is False

        assert path.read_text(encoding="utf-8") == before

    def test_no_file_and_an_unreadable_file_are_left_as_they_are(self, tmp_path: Path):
        path = tmp_path / LAYOUT_FILENAME
        assert migrate_layout(path) is False
        assert not path.exists()

        path.write_text("{not json", encoding="utf-8")
        assert migrate_layout(path) is False
        assert path.read_text(encoding="utf-8") == "{not json"

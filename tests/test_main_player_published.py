"""What the room says, and what the main player keeps when a read comes back torn.

Both files are replaced while this player polls them, so a lost race is the
common case rather than the exceptional one, and blanking the panel for a frame
is a visible flicker on a HUD that redraws at 60fps.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from player_core.drive_readout import DriveHud, drive_text

from main_player.published import Published

GENAU_MODE = "genau"
VIDEO_MODE = "video"


def _console_file(path: Path, mode: str = VIDEO_MODE, **over) -> Path:
    payload = {"mode": mode, "active": True, "osr2": "off"}
    payload.update(over)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _drive_file(path: Path, position: int = 4_000) -> Path:
    """A whole readout, written the way Genau writes one."""
    path.write_text(drive_text(DriveHud(position=position)), encoding="utf-8")
    return path


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "console.json", tmp_path / "drive.txt"


class TestReadingTheConsole:
    def test_what_fun_time_said_is_what_the_panel_shows(self, files):
        console_file, drive_file = files
        _console_file(console_file, GENAU_MODE)
        published = Published(console_file, drive_file)

        published.refresh()

        assert published.console.mode == GENAU_MODE

    def test_a_torn_read_keeps_the_panel_that_was_there(self, files):
        """Fun Time replaces this file while the player polls it, so a lost race
        must not empty the panel for a frame."""
        console_file, drive_file = files
        _console_file(console_file, GENAU_MODE)
        published = Published(console_file, drive_file)
        published.refresh()

        console_file.write_text("{ half a fi", encoding="utf-8")
        published.refresh()

        assert published.console.mode == GENAU_MODE

    def test_a_file_that_vanished_keeps_it_too(self, files):
        console_file, drive_file = files
        _console_file(console_file, GENAU_MODE)
        published = Published(console_file, drive_file)
        published.refresh()

        console_file.unlink()
        published.refresh()

        assert published.console.mode == GENAU_MODE


class TestReadingTheMotion:
    def test_genaus_own_readout_arrives_while_it_is_driving(self, files):
        console_file, drive_file = files
        _console_file(console_file, GENAU_MODE)
        _drive_file(drive_file, position=4_000)
        published = Published(console_file, drive_file)

        published.refresh()

        assert published.drive is not None
        assert published.drive.position == 4_000

    def test_a_torn_read_keeps_the_motion_that_was_there(self, files):
        console_file, drive_file = files
        _console_file(console_file, GENAU_MODE)
        _drive_file(drive_file, position=4_000)
        published = Published(console_file, drive_file)
        published.refresh()

        drive_file.write_text("position=", encoding="utf-8")
        published.refresh()

        assert published.drive.position == 4_000

    def test_the_motion_is_read_in_video_mode_too(self, files):
        """The Robot Hand is driving in both modes — it takes the
        funscript's gaps in video mode — so its readout is drawn there as well."""
        console_file, drive_file = files
        _console_file(console_file, VIDEO_MODE)
        _drive_file(drive_file, position=4_000)
        published = Published(console_file, drive_file)

        published.refresh()

        assert published.drive.position == 4_000

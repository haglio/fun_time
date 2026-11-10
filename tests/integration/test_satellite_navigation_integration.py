from __future__ import annotations

import os
import sys
import time

import pytest
from funestra.funestra_verbs import LOCK_ON, play_file
from funestra.playlist import read_playlist

from fun_time.modes import write_playlist_file
from fun_time.satellite_control import read_satellite_status

from .lone_satellite import a_lone_satellite, library_videos_of

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]


@pytest.fixture
def satellite():
    lone = a_lone_satellite()
    with lone.launched(library_videos_of("portrait", 4), width=800, height=600, hud=True):
        lone.wait_until_playing()
        lone.send(LOCK_ON)
        lone.wait_for(lambda: read_satellite_status(lone.status).locked,
                      desc="the satellite to lock")
        yield lone


def test_next_changes_the_video(satellite):
    start = satellite.video()
    assert start.endswith(".mp4")
    satellite.send("NEXT")
    after = satellite.wait_for_video(other_than=start)
    assert after != start


def test_prev_inverts_next(satellite):
    start = satellite.video()
    satellite.send("NEXT")
    mid = satellite.wait_for_video(other_than=start)
    assert mid != start
    satellite.send("PREV")
    back = satellite.wait_for_video(other_than=mid)
    assert back == start, f"prev should reverse next: expected {start!r}, got {back!r}"


def test_navigation_wraps_around(satellite):
    start = satellite.video()
    current = start
    for _ in range(len(read_playlist(satellite.playlist))):
        satellite.send("NEXT")
        current = satellite.wait_for_video(other_than=current)
    assert current == start, f"stepping the whole list should wrap to {start!r}, got {current!r}"


def test_play_file_switches_to_a_specific_video(satellite):
    start = satellite.video()
    target = next(item for item in read_playlist(satellite.playlist)
                  if str(item.path) != start)

    satellite.send(play_file(target))

    after = satellite.wait_for_video(other_than=start)
    assert after == str(target.path), (
        f"PLAY_FILE should jump to {target.path}, got {after!r}")


def test_trash_advances_off_the_discarded_video(satellite):
    condemned = satellite.video()
    satellite.send("TRASH")
    after = satellite.wait_for_video(other_than=condemned)
    assert after != condemned


def test_reload_playlist_keeps_the_current_video(satellite):
    current = satellite.video()
    videos = [v.strip() for v in satellite.playlist.read_text(encoding="utf-8").splitlines() if v.strip()]
    reshaped = [current] + [v for v in videos if v != current]
    write_playlist_file(satellite.playlist, reshaped)
    satellite.send("RELOAD_PLAYLIST")
    time.sleep(1.0)
    assert satellite.video() == current, "RELOAD_PLAYLIST should keep the current clip"


_CLAIMED_BY_RENAME_AND_READ_TO_THE_END = FileNotFoundError
_WINDOWS_REFUSING_A_READ_MID_RENAME = PermissionError


_THE_UPSCALED_4K_LIBRARY_WHERE_THE_LOAD_IS = "landscape"
_A_LANDSCAPE_SLOTS_SIZE = {"width": 1706, "height": 1410}



"""Integration: a real satellite steps through the clip on screen ten seconds at a
time, as the back and forward 10s buttons on its HUD ask.

Requires: FUN_TIME_RUN_INTEGRATION=1 and a real display (the hidden-desktop runner).
"""
from __future__ import annotations

import os
import sys

import pytest
from funestra.funestra_controls import SEEK_STEP_MS
from funestra.funestra_verbs import SEEK_BACK, SEEK_FWD

from fun_time.runtime_flow import write_flag_file
from fun_time.satellite_control import read_satellite_status

from .integration_support import clip_of_a_test_pattern, published_status
from .lone_satellite import a_lone_satellite

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

SEEK_SLACK_MS = 1_000


def test_seek_steps_a_held_clip_ten_seconds_either_way():
    """A clip made for the test rather than a sample, since stepping ten seconds
    both ways needs one longer than most of the library's."""
    satellite = a_lone_satellite()
    clip = clip_of_a_test_pattern(satellite.sources / "test_pattern.mp4", size="360x640", seconds=40)
    with satellite.launched([str(clip)], width=640, height=360, hud=True):
        satellite.wait_until_playing()
        write_flag_file(satellite.paused, True)
        satellite.wait_for(lambda: read_satellite_status(satellite.status).paused,
                           desc="the satellite to hold still")
        for verb, step in ((SEEK_FWD, SEEK_STEP_MS), (SEEK_BACK, -SEEK_STEP_MS)):
            aim = published_status(read_satellite_status, satellite.status).position_ms + step
            satellite.send(verb)
            satellite.wait_for(
                lambda aim=aim: abs(read_satellite_status(satellite.status).position_ms - aim)
                <= SEEK_SLACK_MS,
                desc=f"{verb} to land the held clip at {aim} ms")

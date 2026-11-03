from __future__ import annotations

import os
import sys

import pytest
from funestra.funestra_controls import SEEK_STEP_MS
from funestra.funestra_verbs import SEEK_BACK, SEEK_FWD

from fun_time.runtime_flow import write_flag_file
from fun_time.satellite_control import read_satellite_status

from .integration_support import published_status, video_of_a_test_pattern
from .lone_satellite import a_lone_satellite

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

SEEK_SLACK_MS = 1_000


def test_seek_steps_a_held_video_ten_seconds_either_way():
    satellite = a_lone_satellite()
    video = video_of_a_test_pattern(satellite.sources / "test_pattern.mp4", size="360x640", seconds=40)
    with satellite.launched([str(video)], width=640, height=360, hud=True):
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

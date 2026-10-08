"""Integration: a real satellite steps through the clip on screen ten seconds at a
time, as the back and forward 10s buttons on its HUD ask.

Requires: FUN_TIME_RUN_INTEGRATION=1 and a real display (the hidden-desktop runner).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from app_support.subprocess_utils import hidden_subprocess_kwargs
from player_core.funestra_controls import SEEK_STEP_MS
from player_core.player_verbs import SEEK_BACK, SEEK_FWD

from fun_time.runtime_flow import write_flag_file
from fun_time.satellite_control import read_satellite_status

from .integration_support import published_status, wait_for
from .test_satellite_navigation_integration import launched

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

SEEK_SLACK_MS = 1_000


def _test_pattern(folder: Path, *, seconds: int) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is what makes this test's clip, and it is not on PATH")
    clip = folder / "test_pattern.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True, timeout=60, **hidden_subprocess_kwargs())
    return str(clip)


def test_seek_steps_a_held_clip_ten_seconds_either_way(tmp_path):
    """A clip made for the test rather than a sample, since stepping ten seconds
    both ways needs one longer than most of the library's."""
    with launched(tmp_path, [_test_pattern(tmp_path, seconds=40)],
                  width=640, height=360) as satellite:
        write_flag_file(satellite.paused, True)
        wait_for(lambda: read_satellite_status(satellite.status).paused,
                 desc="the satellite to hold still")
        for verb, step in ((SEEK_FWD, SEEK_STEP_MS), (SEEK_BACK, -SEEK_STEP_MS)):
            aim = published_status(read_satellite_status, satellite.status).position_ms + step
            satellite.send(verb)
            wait_for(lambda aim=aim: abs(read_satellite_status(satellite.status).position_ms - aim)
                     <= SEEK_SLACK_MS,
                     desc=f"{verb} to land the held clip at {aim} ms")

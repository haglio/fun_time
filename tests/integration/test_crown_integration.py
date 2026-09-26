"""The crown on real windows: a portrait video on the crowned main player trades
places with the portrait player on the secondary monitor, and crowning the
portrait player trades them back -- the windows moved, and what each draws
resized with them.  The clip is a synthetic test pattern made for the run."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest
from app_support.subprocess_utils import hidden_subprocess_kwargs
from player_core.file_channel import append_command
from player_core.player_verbs import play_file
from player_core.playlist import PlaylistItem

from fun_time.crown import Crown
from fun_time.win32 import wait_for_window_by_title, window_rect
from fun_time.win32_loader import Win32Rect
from fun_time.window_layout import MonitorRect, WindowRect, secondary_monitor_rects
from fun_time.windows_bridge_startup import SATELLITE_PORTRAIT_TITLE

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]

SECONDARY_MONITOR = MonitorRect(1280, 0, 720, 1440)
FAKE_MONITORS = "0,0,1280,720;1280,0,720,1440"


def _tall_test_pattern(folder: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is what makes this test's portrait clip, and it is not on PATH")
    clip = folder / "tall_test_pattern.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30",
         "-t", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True, timeout=60, **hidden_subprocess_kwargs())
    return clip


def _client_size(hwnd: int) -> tuple[int, int]:
    rect = Win32Rect()
    ctypes.windll.user32.GetClientRect(wt.HWND(hwnd), ctypes.byref(rect))
    return rect.right - rect.left, rect.lower - rect.top


def _seated(hwnd: int, rect: WindowRect) -> bool:
    return (window_rect(hwnd) == (rect.x, rect.y, rect.width, rect.height)
            and _client_size(hwnd) == (rect.width, rect.height))


def test_a_portrait_video_on_the_crowned_main_player_trades_places_with_the_portrait_player():
    temp_root = build_integration_temp_root()
    session = FunTimeIntegrationSession(build_integration_config(temp_root))
    tall_clip = _tall_test_pattern(temp_root)
    try:
        session.start(env_overrides={"FUN_TIME_FAKE_MONITORS": FAKE_MONITORS})
        portrait = wait_for_window_by_title(SATELLITE_PORTRAIT_TITLE, timeout_s=10, exact=True)
        main = wait_for_window_by_title("Main Player", timeout_s=10, exact=True)
        rects = partial(secondary_monitor_rects, SECONDARY_MONITOR, session.config.layout)

        append_command(session.config.main_player_cmd_file, play_file(PlaylistItem(tall_clip)))
        crowned = rects(majority=Crown.MAIN)
        session.wait_until(
            lambda: _seated(portrait, crowned.portrait) and _seated(main, crowned.main),
            timeout=20,
            description=f"the main player on {crowned.main} and the portrait player on "
                        f"{crowned.portrait} (now {window_rect(main)} and {window_rect(portrait)})")

        session.write_dashboard_command(Crown.PORTRAIT.command)
        usual = rects(majority=Crown.PORTRAIT)
        session.wait_until(
            lambda: _seated(portrait, usual.portrait) and _seated(main, usual.main),
            timeout=20,
            description=f"the portrait player back on {usual.portrait} "
                        f"(now {window_rect(portrait)})")
    finally:
        session.stop()

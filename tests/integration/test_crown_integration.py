"""The crown on real windows: a portrait video or Genau clip on the crowned main
player trades places with the portrait player on the secondary monitor, and
crowning the portrait player trades them back -- the windows moved, and what
each draws resized with them.  The clips are synthetic test patterns made for
the run."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
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
from fun_time.window_roles import GENAU_TITLE
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


def _tall_test_pattern(folder: Path, *, seconds: int) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is what makes this test's portrait clip, and it is not on PATH")
    clip = folder / "tall_test_pattern.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True, timeout=60, **hidden_subprocess_kwargs())
    return clip


def _config_whose_genau_plays(temp_root: Path, clip_folder: Path) -> Path:
    config_path = build_integration_config(temp_root)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["paths"]["clips_dir"] = str(clip_folder)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def _client_size(hwnd: int) -> tuple[int, int]:
    rect = Win32Rect()
    ctypes.windll.user32.GetClientRect(wt.HWND(hwnd), ctypes.byref(rect))
    return rect.right - rect.left, rect.lower - rect.top


def _seated(hwnd: int, rect: WindowRect) -> bool:
    return (window_rect(hwnd) == (rect.x, rect.y, rect.width, rect.height)
            and _client_size(hwnd) == (rect.width, rect.height))


def _wait_until_seated(session: FunTimeIntegrationSession, *, timeout: float,
                       **seats: tuple[int, WindowRect]) -> None:
    session.wait_until(
        lambda: all(_seated(hwnd, rect) for hwnd, rect in seats.values()),
        timeout=timeout,
        description=lambda: "; ".join(
            f"{name} on {rect}, which is on {window_rect(hwnd)} drawing {_client_size(hwnd)}"
            for name, (hwnd, rect) in seats.items()))


def test_a_portrait_video_on_the_crowned_main_player_trades_places_with_the_portrait_player():
    temp_root = build_integration_temp_root()
    session = FunTimeIntegrationSession(build_integration_config(temp_root))
    tall_clip = _tall_test_pattern(temp_root, seconds=30)
    try:
        session.start(env_overrides={"FUN_TIME_FAKE_MONITORS": FAKE_MONITORS})
        portrait = wait_for_window_by_title(SATELLITE_PORTRAIT_TITLE, timeout_s=10, exact=True)
        main = wait_for_window_by_title("Main Player", timeout_s=10, exact=True)
        rects = partial(secondary_monitor_rects, SECONDARY_MONITOR, session.config.layout)

        append_command(session.config.main_player_cmd_file, play_file(PlaylistItem(tall_clip)))
        crowned = rects(majority=Crown.MAIN)
        _wait_until_seated(session, timeout=20, main_player=(main, crowned.main),
                           portrait_player=(portrait, crowned.portrait))

        session.write_dashboard_command(Crown.PORTRAIT.command)
        usual = rects(majority=Crown.PORTRAIT)
        _wait_until_seated(session, timeout=20, main_player=(main, usual.main),
                           portrait_player=(portrait, usual.portrait))
    finally:
        session.stop()


def test_a_portrait_genau_clip_on_the_crowned_main_player_trades_places_with_the_portrait_player():
    temp_root = build_integration_temp_root()
    clip_folder = temp_root / "genau_clips"
    clip_folder.mkdir()
    _tall_test_pattern(clip_folder, seconds=2)
    session = FunTimeIntegrationSession(_config_whose_genau_plays(temp_root, clip_folder))
    try:
        session.start(env_overrides={"FUN_TIME_FAKE_MONITORS": FAKE_MONITORS})
        portrait = wait_for_window_by_title(SATELLITE_PORTRAIT_TITLE, timeout_s=10, exact=True)
        rects = partial(secondary_monitor_rects, SECONDARY_MONITOR, session.config.layout)

        session.write_dashboard_command("genau_activate")
        session.wait_for_new_log("Switched to genau mode", timeout=12)
        genau = wait_for_window_by_title(GENAU_TITLE, timeout_s=10, exact=True)
        crowned = rects(majority=Crown.MAIN)
        _wait_until_seated(session, timeout=30, genau=(genau, crowned.main),
                           portrait_player=(portrait, crowned.portrait))

        session.write_dashboard_command(Crown.PORTRAIT.command)
        usual = rects(majority=Crown.PORTRAIT)
        _wait_until_seated(session, timeout=20, genau=(genau, usual.main),
                           portrait_player=(portrait, usual.portrait))
    finally:
        session.stop()

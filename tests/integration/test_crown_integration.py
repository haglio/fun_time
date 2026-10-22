"""The crown on real windows: a portrait video or Genau flick on the crowned Main
Funestra trades places with the Portrait Funestra on the secondary monitor, and
crowning the Portrait Funestra trades them back -- the windows moved, and what
each draws resized with them.  The clips are synthetic test patterns made for
the run."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
from functools import partial
from pathlib import Path

import pytest
from app_support.flick_folder import flat_flicks_in, flicks_folder_in
from funestra.file_channel import append_command
from funestra.funestra_verbs import play_file
from funestra.playlist import PlaylistItem

from fun_time.crown import Crown
from fun_time.win32 import window_rect
from fun_time.win32_loader import Win32Rect
from fun_time.window_layout import WindowRect, secondary_monitor_rects
from fun_time.windows_bridge_startup import SATELLITE_PORTRAIT_TITLE
from tests.made_up_genau import made_up_genau, point_the_shared_settings_at

from .integration_support import (
    SESSION_MONITORS,
    FunTimeIntegrationSession,
    await_window,
    build_integration_config,
    build_integration_temp_root,
    clip_of_a_test_pattern,
)

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]

_, SECONDARY_MONITOR = SESSION_MONITORS


def _tall_test_pattern(folder: Path, *, seconds: int) -> Path:
    return clip_of_a_test_pattern(folder / "tall_test_pattern.mp4", size="360x640", seconds=seconds)


def _client_size(hwnd: int) -> tuple[int, int]:
    rect = Win32Rect()
    ctypes.windll.user32.GetClientRect(wt.HWND(hwnd), ctypes.byref(rect))
    return rect.right - rect.left, rect.lower - rect.top


def _seated(hwnd: int, rect: WindowRect) -> bool:
    return (window_rect(hwnd) == (rect.x, rect.y, rect.width, rect.height)
            and _client_size(hwnd) == (rect.width, rect.height))


def _wait_until_seated(session: FunTimeIntegrationSession,
                       **seats: tuple[int, WindowRect]) -> None:
    session.wait_until(
        lambda: all(_seated(hwnd, rect) for hwnd, rect in seats.values()),
        description=lambda: "; ".join(
            f"{name} on {rect}, which is on {window_rect(hwnd)} drawing {_client_size(hwnd)}"
            for name, (hwnd, rect) in seats.items()))


def test_a_portrait_video_on_the_crowned_main_funestra_trades_places_with_the_portrait_funestra():
    temp_root = build_integration_temp_root()
    session = FunTimeIntegrationSession(build_integration_config(temp_root))
    tall_clip = _tall_test_pattern(temp_root, seconds=30)
    try:
        session.start()
        portrait = await_window(SATELLITE_PORTRAIT_TITLE, 10, exact=True)
        main = await_window("Main Funestra", 10, exact=True)
        rects = partial(secondary_monitor_rects, SECONDARY_MONITOR, session.config.layout)

        append_command(session.config.main_funestra_cmd_file, play_file(PlaylistItem(tall_clip)))
        crowned = rects(majority=Crown.MAIN)
        _wait_until_seated(session, main_funestra=(main, crowned.main),
                           portrait_funestra=(portrait, crowned.portrait))

        session.write_dashboard_command(Crown.PORTRAIT.command)
        usual = rects(majority=Crown.PORTRAIT)
        _wait_until_seated(session, main_funestra=(main, usual.main),
                           portrait_funestra=(portrait, usual.portrait))
    finally:
        session.stop()


def test_a_portrait_genau_flick_on_the_crowned_main_funestra_trades_places_with_the_portrait_funestra(
        monkeypatch):
    temp_root = build_integration_temp_root()
    genau = made_up_genau(temp_root)
    point_the_shared_settings_at(monkeypatch, genau)
    played_from = flat_flicks_in(flicks_folder_in(genau)) / "AI"
    played_from.mkdir()
    _tall_test_pattern(played_from, seconds=2)
    session = FunTimeIntegrationSession(build_integration_config(temp_root))
    try:
        session.start()
        portrait = await_window(SATELLITE_PORTRAIT_TITLE, 10, exact=True)
        rects = partial(secondary_monitor_rects, SECONDARY_MONITOR, session.config.layout)

        session.write_dashboard_command("genau_activate")
        session.wait_for_new_log("Switched to genau mode")
        main = await_window("Main Funestra", 10, exact=True)
        crowned = rects(majority=Crown.MAIN)
        _wait_until_seated(session, main_funestra=(main, crowned.main),
                           portrait_funestra=(portrait, crowned.portrait))

        session.write_dashboard_command(Crown.PORTRAIT.command)
        usual = rects(majority=Crown.PORTRAIT)
        _wait_until_seated(session, main_funestra=(main, usual.main),
                           portrait_funestra=(portrait, usual.portrait))
    finally:
        session.stop()

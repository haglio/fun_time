"""A click in a corner of a real satellite's picture asks the room for its HUD there.

The mouse reaches the window the way a hand's does, as Windows messages to the
satellite's own window: SDL turns them into pygame's events, and the Funestra
under them sends the corner's ask down the dashboard channel the session reads.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

import pytest

from .integration_support import await_window
from .lone_satellite import a_lone_satellite, library_clips_of

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

WIDTH, HEIGHT = 800, 600
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
MK_LBUTTON = 0x0001


def _click(hwnd: int, x: int, y: int) -> None:
    where = wt.LPARAM((y << 16) | x)
    post = ctypes.windll.user32.PostMessageW
    post(wt.HWND(hwnd), WM_MOUSEMOVE, wt.WPARAM(0), where)
    post(wt.HWND(hwnd), WM_LBUTTONDOWN, wt.WPARAM(MK_LBUTTON), where)
    post(wt.HWND(hwnd), WM_LBUTTONUP, wt.WPARAM(0), where)


def _asked(dashboard_cmd) -> list[str]:
    return dashboard_cmd.read_text(encoding="utf-8").split() if dashboard_cmd.exists() else []


def test_a_click_in_a_corner_its_hud_is_not_in_asks_for_the_hud_there():
    sat = a_lone_satellite()
    with sat.launched(library_clips_of("portrait", 2), width=WIDTH, height=HEIGHT, hud=True):
        sat.wait_until_playing()
        sat.hud.write_text(json.dumps({"player": "portrait"}), encoding="utf-8")
        hwnd = await_window("Portrait AI Funestra", 30.0)

        def asked_after_a_click() -> bool:
            _click(hwnd, WIDTH - 3, HEIGHT - 3)
            time.sleep(0.5)
            return "portrait_hud_restore_at|lower_right" in _asked(sat.dashboard_cmd)

        sat.wait_for(asked_after_a_click,
                     desc="the satellite to ask for its HUD in the lower right corner")

from __future__ import annotations

import ctypes
import sys
import time
from collections import Counter
from collections.abc import Callable
from ctypes import wintypes
from itertools import pairwise

import pytest

from fun_time.win32 import (
    find_window_for_process,
    is_window_minimized,
    iter_zorder,
    windows_obscuring,
)
from fun_time.window_roles import GENAU_TITLE, GENAU_TITLES, GENAU_VIDEO_TITLE

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)


@pytest.fixture(scope="module")
def shared_integration_session():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        yield session
    finally:
        session.stop()


def _main_player_and_genau(s: FunTimeIntegrationSession) -> tuple[int, int]:
    pids = s.read_child_pids()
    main_player = find_window_for_process(pids["main_player_pid"], "Main Player")
    genau = next(hwnd for hwnd in (find_window_for_process(pids["genau_pid"], title)
                                   for title in GENAU_TITLES) if hwnd)
    return main_player, genau


def _directly_beneath(lower: int, upper: int) -> bool:
    stack = [window.hwnd for window in iter_zorder()]
    return upper in stack and lower in stack and stack.index(lower) == stack.index(upper) + 1


def test_a_switch_to_video_while_paused_keeps_the_video_directly_under_genaus_hud(
        shared_integration_session: FunTimeIntegrationSession):
    s = shared_integration_session
    main_player, genau = _main_player_and_genau(s)
    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("Topmost [post-enter]", timeout=12)
    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode", timeout=12)
    s.wait_until(
        lambda: is_window_minimized(main_player),
        timeout=30,
        description="the main player to go down for genau mode",
    )

    s.write_dashboard_command("main_video_activate")
    s.wait_for_new_log("Switched to video mode", timeout=12)
    s.wait_until(
        lambda: _directly_beneath(main_player, genau),
        timeout=30,
        description="the main player to sit directly under Genau's HUD while paused",
    )

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("Topmost [post-leave]", timeout=12)


_user32 = ctypes.WinDLL("user32")
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def _title(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, buffer, 256)
    return buffer.value


def _what_else_shows_through_genau(genau: int, main_player: int) -> str:
    stack = iter_zorder()
    hwnds = [window.hwnd for window in stack]
    if genau not in hwnds:
        return "Genau itself was gone"
    under_genau = stack[hwnds.index(genau) + 1:]
    if main_player not in [window.hwnd for window in under_genau]:
        return "the main player was parked"
    between = windows_obscuring(main_player, under_genau)
    return f"{[window.title for window in between]} sat between them" if between else ""


def _switch_watching_genaus_hud(s: FunTimeIntegrationSession, command: str, *, genau: int,
                                main_player: int,
                                done: Callable[[list[tuple[float, str]]], bool],
                                ) -> list[tuple[float, str]]:
    looks: list[tuple[float, str]] = []
    s.write_dashboard_command(command)
    deadline = time.monotonic() + 30
    while not done(looks):
        assert time.monotonic() < deadline, (
            f"{command} never finished: Genau's caption reads {_title(genau)!r}, and the main "
            f"player is {'parked' if is_window_minimized(main_player) else 'up'}")
        if _title(genau) == GENAU_VIDEO_TITLE:
            looks.append((time.monotonic(), _what_else_shows_through_genau(genau, main_player)))
        time.sleep(0.001)
    return looks


def _hud_up_for(seconds: float) -> Callable[[list[tuple[float, str]]], bool]:
    return lambda looks: bool(looks) and time.monotonic() - looks[0][0] >= seconds


def _gaps_that_held(looks: list[tuple[float, str]]) -> list[str]:
    return [what for (_, before), (_, what) in pairwise(looks) if before and what]


def test_a_switch_either_way_never_shows_anything_but_the_main_player_through_genaus_hud(
        shared_integration_session: FunTimeIntegrationSession):
    s = shared_integration_session
    main_player, genau = _main_player_and_genau(s)
    for paused in (True, False):
        if paused:
            s.write_dashboard_command("omnipause_toggle")
            s.wait_for_new_log("Topmost [post-enter]", timeout=12)
        to_genau = _switch_watching_genaus_hud(
            s, "genau_activate", genau=genau, main_player=main_player,
            done=lambda _looks: is_window_minimized(main_player) and _title(genau) == GENAU_TITLE)
        to_video = _switch_watching_genaus_hud(
            s, "main_video_activate", genau=genau, main_player=main_player,
            done=_hud_up_for(0.5))

        uncovered = Counter([("to genau", what) for what in _gaps_that_held(to_genau)]
                            + [("to video", what) for what in _gaps_that_held(to_video)])
        assert not uncovered, (
            f"{'paused' if paused else 'playing'}: Genau's HUD was see-through over more than "
            f"the main player in {sum(uncovered.values())} of {len(to_genau) + len(to_video)} "
            f"looks: {dict(uncovered)}")
        if paused:
            s.write_dashboard_command("omnipause_toggle")
            s.wait_for_new_log("Topmost [post-leave]", timeout=12)

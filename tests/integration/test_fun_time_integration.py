from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from app_support.file_channel import read_key_values, write_flag
from app_support.funscript import document, write
from app_support.subprocess_utils import hidden_subprocess_kwargs
from player_core.file_channel import append_command
from player_core.modes import LoopState
from player_core.player_verbs import RELOAD_PLAYLIST, SET_SPEED, play_file
from player_core.playlist import PlaylistItem

from fun_time.config import SatelliteFiles
from fun_time.media_actions import remove_from_favs
from fun_time.player_status import MainPlayerStatus, read_genau_status, read_main_player_status
from fun_time.players import Player
from fun_time.role_windows import MAIN_BLANK_SETTLE_S
from fun_time.satellite_control import SatelliteStatus, read_satellite_status
from fun_time.shared_state import read_shared_state, shared_state_path
from fun_time.win32 import (
    find_window_by_title,
    is_window_minimized,
    is_window_topmost,
)
from fun_time.win32_process import is_process_alive
from fun_time.windows_bridge_sequencer import _resolve_satellite_hwnds
from main_player.controls import SEEK_STEP_MS

from .integration_support import (
    COMMAND_BUDGET_S,
    QUIT_BUDGET_S,
    START_BUDGET_S,
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
    published_status,
    senders,
    tcode_heard,
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


@pytest.fixture
def isolated_integration_session():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        yield session
    finally:
        session.stop()


def test_fun_time_startup_runtime_smoke(shared_integration_session: FunTimeIntegrationSession):
    assert shared_integration_session.windows_bridge_log.exists()
    assert shared_integration_session.orchestrator_log.exists()


def test_fun_time_portrait_lock_unlock_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait satellite")

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Unlocked portrait satellite")


def test_fun_time_omnipause_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: entering")

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: leaving")


def test_fun_time_fmode_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    """The bare command still narrows every player at once, and lifts it again."""
    shared_integration_session.write_dashboard_command("fmode_toggle")
    shared_integration_session.wait_for_new_log("F-mode enabled: main, portrait, landscape")

    shared_integration_session.write_dashboard_command("fmode_toggle")
    shared_integration_session.wait_for_new_log("F-mode disabled: main, portrait, landscape")


def test_fun_time_sided_fmode_flow(shared_integration_session: FunTimeIntegrationSession):
    """What a satellite's own F button posts: that player alone goes into F-mode,
    and the whole-room command afterwards finishes the job rather than undoing it."""
    shared_integration_session.write_dashboard_command("portrait_fmode")
    shared_integration_session.wait_for_new_log("F-mode enabled: portrait")

    shared_integration_session.write_dashboard_command("fmode_toggle")
    shared_integration_session.wait_for_new_log("F-mode enabled: main, landscape")

    shared_integration_session.write_dashboard_command("fmode_off")
    shared_integration_session.wait_for_new_log("F-mode disabled: main, portrait, landscape")


def test_fun_time_genau_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    """Pressing 'g' (genau_activate) then 'h' (main_video_activate) switches modes."""
    s = shared_integration_session
    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode")

    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "0",
        description="Genau paused file to flip off (active)",
    )
    s.wait_until(
        lambda: s.config.main_player_paused_file.read_text(encoding="utf-8") == "1",
        description="the main player paused file to flip on (inactive)",
    )

    s.write_dashboard_command("main_video_activate")
    s.wait_for_new_log("Switched to video mode")

    s.wait_until(
        lambda: s.config.main_player_paused_file.read_text(encoding="utf-8") == "0",
        description="the main player paused file to flip back off (active)",
    )
    # Genau runs on in video mode — its HUD over the video, the Robot Hand
    # under it for the funscript's gaps — so its flag never flips back.
    assert s.config.genau_paused_file.read_text(encoding="utf-8") == "0"


def _osr2_still(heights: list[float]) -> bool:
    return bool(heights) and max(heights) - min(heights) < 0.02


def _osr2_swinging(heights: list[float]) -> bool:
    return bool(heights) and max(heights) - min(heights) > 0.3


def _osr2_heights(sink: int, *, seconds: float) -> list[float]:
    return [height for _at, _sender, height in tcode_heard(sink, seconds=seconds)]


def _osr2_heard_until(sink: int, shows) -> list[float]:
    deadline = time.monotonic() + COMMAND_BUDGET_S
    heard = _osr2_heights(sink, seconds=3)
    while not shows(heard) and time.monotonic() < deadline:
        heard = _osr2_heights(sink, seconds=3)
    return heard


def _wait_for_genau_to_say(session: FunTimeIntegrationSession, **fields: int) -> None:
    wanted = {key: str(value) for key, value in fields.items()}

    def saying() -> bool:
        try:
            said = read_key_values(session.config.genau_status_file)
        except (OSError, ValueError):
            return False
        return all(said.get(key, "").strip() == value for key, value in wanted.items())

    session.wait_until(saying, description=f"Genau's status to say {wanted}")


def test_fun_time_the_max_intensity_pushes_the_robot_hand_down_and_leaves_it_there(
    shared_integration_session: FunTimeIntegrationSession,
):
    s = shared_integration_session
    sink = s.config.main_player_tcode.port
    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode")
    try:
        for command in ("play", "robot_hand_amp_100", "robot_hand_speed_50"):
            s.write_dashboard_command(command)
        swinging = _osr2_heard_until(sink, _osr2_swinging)
        s.write_dashboard_command("max_intensity_0")
        held = _osr2_heard_until(sink, _osr2_still)
        _wait_for_genau_to_say(s, max_intensity=0)
        s.write_dashboard_command("max_intensity_100")
        _wait_for_genau_to_say(s, max_intensity=100)
        left_where_it_pushed = _osr2_heights(sink, seconds=3)
        for command in ("robot_hand_amp_100", "robot_hand_speed_50"):
            s.write_dashboard_command(command)
        free = _osr2_heard_until(sink, _osr2_swinging)
    finally:
        s.write_dashboard_command("max_intensity_100")
        s.write_dashboard_command("main_video_activate")
        s.wait_for_new_log("Switched to video mode")

    assert _osr2_swinging(swinging), swinging
    assert _osr2_still(held), held
    assert _osr2_still(left_where_it_pushed), left_where_it_pushed
    assert _osr2_swinging(free), free


def test_fun_time_the_max_intensity_holds_a_funscript_down_through_its_gaps_too(
    shared_integration_session: FunTimeIntegrationSession,
):
    s = shared_integration_session
    sink = s.config.main_player_tcode.port
    s.write_dashboard_command("main_video_activate")
    s.write_dashboard_command("play")
    try:
        free = _osr2_heard_until(sink, _osr2_swinging)
        s.write_dashboard_command("max_intensity_0")
        held = _osr2_heard_until(sink, _osr2_still)
        still_after = _osr2_heights(sink, seconds=10)
    finally:
        s.write_dashboard_command("max_intensity_100")

    assert _osr2_swinging(free), free
    assert _osr2_still(held), held
    assert _osr2_still(still_after), still_after


def test_fun_time_mode_switch_swaps_primary_slot_window_visibility(shared_integration_session: FunTimeIntegrationSession):
    """The main-slot players share one screen rect, so a mode switch settles
    which is on screen: in video mode both are restored — the main player's video with
    Genau's HUD stacked above it, both topmost — and genau mode parks the main player
    (minimized, never hidden — both keep a taskbar button all session, so both
    stay findable by title; is_window_minimized tells them apart)."""
    s = shared_integration_session

    # video mode: the main player restored AND topmost (its video above the desktop), and
    # Genau restored and topmost too, promoted after the main player so the HUD lands above
    # the video.  Exact, so a caption merely containing the name cannot answer.
    s.wait_until(
        lambda: find_window_by_title("Main Player", exact=True) != 0,
        description="the main player window to exist in video mode",
    )
    main_player_hwnd = find_window_by_title("Main Player", exact=True)
    s.wait_until(
        lambda: is_window_topmost(main_player_hwnd) and not is_window_minimized(main_player_hwnd),
        description="the main player to be restored and topmost in video mode",
    )
    s.wait_until(
        lambda: (not is_window_minimized(find_window_by_title("Genau"))
                 and is_window_topmost(find_window_by_title("Genau"))),
        description="Genau's HUD to be restored and topmost in video mode, above the main player",
    )

    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode")

    s.wait_until(
        lambda: is_window_minimized(find_window_by_title("Main Player", exact=True)),
        description="the main player window to minimize when Genau mode activates",
    )
    s.wait_until(
        lambda: not is_window_minimized(find_window_by_title("Genau")),
        description="Genau window to stay up as the display in genau mode",
    )

    # Back to video mode: the main player is restored and reclaims the topmost band, Genau
    # stays up as the HUD above it — BOTH in the topmost band, leaving the
    # session where it started.
    s.write_dashboard_command("main_video_activate")
    s.wait_for_new_log("Switched to video mode")

    s.wait_until(
        lambda: not is_window_minimized(find_window_by_title("Main Player", exact=True)),
        description="the main player window to restore in video mode",
    )
    s.wait_until(
        lambda: is_window_topmost(find_window_by_title("Main Player", exact=True)),
        description="the main player to float topmost in video mode (video above the desktop)",
    )
    s.wait_until(
        lambda: (not is_window_minimized(find_window_by_title("Genau"))
                 and is_window_topmost(find_window_by_title("Genau"))),
        description="Genau's HUD to be topmost in video mode, stacked above the main player",
    )


def test_fun_time_leaving_player_stays_up_long_enough_to_go_dark(
    shared_integration_session: FunTimeIntegrationSession,
):
    """Minimizing freezes a window's Alt-Tab thumbnail — Windows stops drawing a
    minimized window, so whatever it last painted is what the thumbnail keeps —
    and the same switch is what tells the outgoing player to go black.  Minimize
    before it has painted that and the thumbnail keeps the video frame it was
    sitting on, which is the whole point of the blanking.  So the minimize is
    held back (MAIN_BLANK_SETTLE_S); this measures that it really is.
    """
    s = shared_integration_session
    s.write_dashboard_command("main_video_activate")
    s.wait_until(
        lambda: not is_window_minimized(find_window_by_title("Main Player", exact=True)),
        description="the main player restored, so the switch away from it has something to hold",
    )

    started = time.monotonic()
    s.write_dashboard_command("genau_activate")
    # Sampled rather than waited on: how LONG the main player stays up is the assertion, and
    # a log line read at 200 ms cannot see a 250 ms window.
    while time.monotonic() - started < COMMAND_BUDGET_S:
        if is_window_minimized(find_window_by_title("Main Player", exact=True)):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("the main player never minimized after switching to genau mode")
    held = time.monotonic() - started

    assert held >= MAIN_BLANK_SETTLE_S, (
        f"the main player was minimized after {held:.3f}s, inside the "
        f"{MAIN_BLANK_SETTLE_S}s it is given to paint its black"
    )
    s.write_dashboard_command("main_video_activate")
    s.wait_for_new_log("Switched to video mode")


def test_fun_time_landscape_lock_unlock_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape satellite")

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Unlocked landscape satellite")


def test_fun_time_portrait_next_cancels_lock(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait satellite")

    shared_integration_session.write_dashboard_command("portrait_next")
    time.sleep(1.5)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait satellite")

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Unlocked portrait satellite")


def test_fun_time_landscape_next_cancels_lock(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape satellite")

    shared_integration_session.write_dashboard_command("landscape_next")
    time.sleep(1.5)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape satellite")

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Unlocked landscape satellite")


def test_fun_time_omnipause_while_genau_mode(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("genau_activate")
    shared_integration_session.wait_for_new_log("Switched to genau mode")

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: entering")
    shared_integration_session.wait_until(
        lambda: shared_integration_session.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        description="Genau paused file to flip on",
    )

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: leaving")
    shared_integration_session.wait_until(
        lambda: shared_integration_session.config.genau_paused_file.read_text(encoding="utf-8") == "0",
        description="Genau paused file to flip off",
    )

    shared_integration_session.write_dashboard_command("main_video_activate")
    shared_integration_session.wait_for_new_log("Switched to video mode")


def test_fun_time_omnipause_does_not_kill_genau(shared_integration_session: FunTimeIntegrationSession):
    """Regression: omnipause must pause Genau, not close it.

    The old AHK HandleOmniPauseToggle never removed Genau's topmost
    flag.  When omnipause was ported to Python, an explicit
    set_topmost(Genau, False) was added by mistake, causing the
    window to fall under other windows (appearing "closed").  Verify the
    Genau process survives an omnipause round-trip while in genau
    mode.
    """
    s = shared_integration_session
    rh_pid = s.read_genau_pid()
    assert is_process_alive(rh_pid), "Genau should be alive before test"

    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode")

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: entering")
    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        description="Genau paused file to flip on",
    )

    # Genau must still be running — omnipause should pause, not close.
    assert is_process_alive(rh_pid), (
        "Genau process died during omnipause — "
        "Esc should pause Genau, not close it"
    )

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: leaving")

    assert is_process_alive(rh_pid), "Genau should survive leaving omnipause"

    s.write_dashboard_command("main_video_activate")
    s.wait_for_new_log("Switched to video mode")


def test_fun_time_omnipause_drops_satellites_from_topmost(shared_integration_session: FunTimeIntegrationSession):
    """Entering OmniPause must free the desktop — the Portrait and Landscape
    satellites must leave the topmost band, not stay pinned on top of the
    windows the user reaches for while paused."""
    s = shared_integration_session
    # Known starting point: video mode, not omnipaused ("play" is an idempotent
    # leave-omnipause; a no-op when already live).
    s.write_dashboard_command("main_video_activate")
    s.write_dashboard_command("play")

    # Resolve exactly as startup does: by each satellite's DISTINCT caption
    # ("Portrait AI Player" / "Landscape AI Player").  A pid cannot find either
    # window — the venv's pythonw launcher spawns the interpreter that owns it as
    # a child — and the distinct captions are what tell portrait from landscape
    # without swapping them.
    portrait_hwnd, landscape_hwnd = _resolve_satellite_hwnds()
    assert portrait_hwnd, "Portrait satellite window must be resolvable"
    assert landscape_hwnd, "Landscape satellite window must be resolvable"

    # Satellites float topmost while the desktop is live.
    s.wait_until(
        lambda: is_window_topmost(portrait_hwnd) and is_window_topmost(landscape_hwnd),
        description="Portrait + Landscape satellites to be topmost before OmniPause",
    )

    # Enter OmniPause — every managed window must drop out of the topmost band.
    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: entering")
    s.wait_until(
        lambda: not is_window_topmost(portrait_hwnd),
        description="Portrait satellite to leave the topmost band on OmniPause enter",
    )
    s.wait_until(
        lambda: not is_window_topmost(landscape_hwnd),
        description="Landscape satellite to leave the topmost band on OmniPause enter",
    )

    # Restore the shared session.
    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: leaving")


def test_fun_time_omnipause_freezes_the_satellites(
    shared_integration_session: FunTimeIntegrationSession,
):
    """OmniPause freezes the native satellites through their paused flag file.

    The player obeys that flag every tick and simply cannot auto-advance while it
    is set, so entering OmniPause is a settled state that needs no policing.  We
    assert each satellite reports paused and its playhead stops moving."""
    s = shared_integration_session
    portrait_status = s.config.paths.state_dir / "portrait_status.txt"
    landscape_status = s.config.paths.state_dir / "landscape_status.txt"

    # Known starting point (video mode, live): the satellites are playing.
    s.write_dashboard_command("main_video_activate")
    s.write_dashboard_command("play")  # idempotent leave-omnipause; a no-op if live
    s.wait_until(
        lambda: not read_satellite_status(portrait_status).paused,
        description="Portrait satellite to be playing before OmniPause",
    )

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: entering")
    s.wait_until(
        lambda: read_satellite_status(portrait_status).paused,
        description="Portrait satellite to report paused under OmniPause",
    )
    s.wait_until(
        lambda: read_satellite_status(landscape_status).paused,
        description="Landscape satellite to report paused under OmniPause",
    )
    # The playhead must not advance while paused.
    pos_a = published_status(read_satellite_status, portrait_status).position_ms
    time.sleep(1.2)
    pos_b = published_status(read_satellite_status, portrait_status).position_ms
    assert pos_b == pos_a, f"paused satellite kept playing ({pos_a} -> {pos_b})"

    # Restore the shared session.
    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: leaving")


def test_fun_time_the_satellites_take_the_main_players_playback_speed(
    shared_integration_session: FunTimeIntegrationSession,
):
    s = shared_integration_session
    statuses = [s.config.paths.state_dir / f"{side}_status.txt"
                for side in ("portrait", "landscape")]
    s.write_dashboard_command("main_video_activate")
    s.write_dashboard_command("play")
    s.wait_until(
        lambda: s.read_main_player_status().video != "" and s.read_main_player_status().speed == 1.0,
        description="the main player to be playing at normal speed",
    )

    s.write_dashboard_command("main_player_speed_150")
    s.wait_until(
        lambda: all(read_satellite_status(status).speed == 1.5 for status in statuses),
        description="both satellites to take the main player's one and a half speed",
    )

    s.write_dashboard_command("main_player_speed_100")
    s.wait_until(
        lambda: all(read_satellite_status(status).speed == 1.0 for status in statuses),
        description="both satellites back at normal speed with the main player",
    )


NUDGE_SLACK_MS = 1_000
NUDGE_HEADROOM_MS = SEEK_STEP_MS + 2_000
LONG_ENOUGH_TO_NUDGE_MS = 25_000


def _loaded_and_playing(status: MainPlayerStatus) -> bool:
    return status.video != "" and status.duration_ms > 0 and not status.paused


def _main_player_playing_in_video_mode(session: FunTimeIntegrationSession) -> None:
    session.write_dashboard_command("main_video_activate")
    session.write_dashboard_command("play")
    session.wait_until(
        lambda: _loaded_and_playing(session.read_main_player_status()),
        description="the main player to be playing a loaded video in video mode",
    )


@contextmanager
def _main_player_held_still(session: FunTimeIntegrationSession):
    paused_file = session.config.main_player_paused_file
    try:
        session.wait_until(
            lambda: write_flag(paused_file, True) and session.read_main_player_status().paused,
            description="the main player to hold still",
        )
        yield
    finally:
        session.wait_until(lambda: write_flag(paused_file, False),
                           description="the main player's paused flag to be lifted")


def _a_different_video_loaded(status: MainPlayerStatus, than: str) -> bool:
    return status.video not in ("", than) and status.duration_ms > 0


def _step_to_a_video_long_enough_to_nudge(session: FunTimeIntegrationSession) -> int:
    for _ in range(12):
        shown = session.read_main_player_status()
        if shown.duration_ms >= LONG_ENOUGH_TO_NUDGE_MS:
            return shown.duration_ms
        session.write_dashboard_command("main_next")
        session.wait_until(
            lambda than=shown.video: _a_different_video_loaded(session.read_main_player_status(), than),
            description="the main player to load the next video",
        )
    raise AssertionError(f"no sampled video runs {LONG_ENOUGH_TO_NUDGE_MS} ms, to nudge each way")


def _nudge(session: FunTimeIntegrationSession, command: str, *, by_ms: int) -> None:
    before = session.read_main_player_status()
    aim = min(max(before.position_ms + by_ms, 0), before.duration_ms)
    session.write_dashboard_command(command)
    session.wait_until(
        lambda: abs(session.read_main_player_status().position_ms - aim) <= NUDGE_SLACK_MS,
        description=lambda: (f"{command} to move the held main player from {before.position_ms} ms "
                             f"to {aim} ms; it reads {session.read_main_player_status().position_ms}"),
    )


def test_fun_time_main_player_nudge_seeks_playback(shared_integration_session: FunTimeIntegrationSession):
    """main_nudge_next/prev in video mode drive the main player's seek via its command
    file, observed through the main player's published status position."""
    s = shared_integration_session
    _main_player_playing_in_video_mode(s)
    with _main_player_held_still(s):
        duration = _step_to_a_video_long_enough_to_nudge(s)
        while s.read_main_player_status().position_ms > duration - NUDGE_HEADROOM_MS:
            _nudge(s, "main_nudge_prev", by_ms=-SEEK_STEP_MS)
        _nudge(s, "main_nudge_next", by_ms=SEEK_STEP_MS)
        _nudge(s, "main_nudge_prev", by_ms=-SEEK_STEP_MS)


def test_fun_time_main_player_record_loop_cancel_cycle(shared_integration_session: FunTimeIntegrationSession):
    """The record gesture round-trips through the main player: record → looping → cancel,
    observed through the main player's published loop state."""
    s = shared_integration_session
    _main_player_playing_in_video_mode(s)
    with _main_player_held_still(s):
        assert s.read_main_player_status().loop_state is LoopState.NORMAL

        s.write_dashboard_command("main_player_record_tap")
        s.wait_until(
            lambda: s.read_main_player_status().loop_state is LoopState.RECORDING,
            description="the main player to enter recording state",
        )

        s.write_dashboard_command("main_player_record_tap")
        s.wait_until(
            lambda: s.read_main_player_status().loop_state is LoopState.LOOPING,
            description="the main player to enter looping state",
        )

        s.write_dashboard_command("main_player_loop_cancel")
        s.wait_until(
            lambda: s.read_main_player_status().loop_state is LoopState.NORMAL,
            description="the main player to return to normal state",
        )


def test_fun_time_video_mode_comes_back_to_the_video_main_player_was_showing(shared_integration_session: FunTimeIntegrationSession):
    """A round trip through genau mode hands the main player nothing new: the video it was
    parked on is the one it resumes, and prev/next/nudge dispatch to it again
    just as before.

    (The precise +10s the main player seek is covered by the nudge test above, which
    exercises the identical dispatch path.)

    Must run before isolated-session tests (trash), whose teardown kills all
    recent player processes and would leave the shared session's players dead.
    """
    s = shared_integration_session

    main_player_video_before = s.read_main_player_status().video
    assert main_player_video_before, "expected the main player to be playing before switching to genau"

    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode")
    s.write_dashboard_command("main_video_activate")
    s.wait_for_new_log("Switched to video mode")

    # The main player is the display again and keeps playing its current video — no handoff.
    s.wait_until(
        lambda: s.read_main_player_status().video == main_player_video_before,
        description="the main player to come back on the video it was showing",
    )

    # A nudge in video mode reaches the normal dispatch path.
    s.write_dashboard_command("main_nudge_next")
    s.wait_for_new_log("Dispatching command: main_nudge_next")


def _swinging_script(path: Path) -> Path:
    write(path, document([{"at": ms, "pos": 0 if ms % 2000 == 0 else 100}
                          for ms in range(0, 3_600_000, 1000)], duration_seconds=3600))
    return path


def _script_that_starts_in_ten_hours(path: Path) -> Path:
    write(path, document([{"at": 36_000_000, "pos": 50}], duration_seconds=36_000))
    return path


@contextmanager
def _the_portrait_player_driving_the_osr2(s: FunTimeIntegrationSession, tmp_path: Path):
    portrait = s.config.satellite(Player.PORTRAIT)
    s.wait_until(lambda: bool(read_satellite_status(portrait.status_file).video)
                 and not read_satellite_status(portrait.status_file).paused,
                 description="the portrait player playing a video")
    video = Path(read_satellite_status(portrait.status_file).video)
    try:
        s.write_dashboard_command("portrait_take_osr2")
        append_command(portrait.cmd_file,
                       play_file(PlaylistItem(video, _swinging_script(tmp_path / "swings.funscript"))))
        s.write_dashboard_command("portrait_lock")
        s.wait_for_new_log("Locked portrait satellite")
        s.wait_until(lambda: read_satellite_status(portrait.status_file).funscript_driving,
                     description="the portrait player's funscript to be driving")
        yield portrait, video
    finally:
        s.write_dashboard_command("main_take_osr2")
        if read_satellite_status(portrait.status_file).locked:
            s.write_dashboard_command("portrait_lock")
            s.wait_for_new_log("Unlocked portrait satellite")
        append_command(portrait.cmd_file, RELOAD_PLAYLIST)


def test_fun_time_a_satellite_with_the_osr2_drives_it_from_its_videos_funscript(
    shared_integration_session: FunTimeIntegrationSession, tmp_path: Path,
):
    s = shared_integration_session
    sink = s.config.main_player_tcode.port
    before = senders(tcode_heard(sink, seconds=2))
    with _the_portrait_player_driving_the_osr2(s, tmp_path) as (portrait, video):
        time.sleep(1.0)
        scripted = senders(tcode_heard(sink, seconds=4))
        assert len(scripted) == 1, (before, scripted)
        ((portraits_line, (_moves, _first, _last, lowest, highest)),) = scripted.items()
        assert highest - lowest > 0.5, scripted

        s.write_dashboard_command("speed_up")
        s.wait_until(lambda: read_satellite_status(portrait.status_file).speed > 1.0,
                     description="the speed nudge to reach the portrait player")
        append_command(portrait.cmd_file, f"{SET_SPEED} 1")

        append_command(portrait.cmd_file, play_file(PlaylistItem(
            video, _script_that_starts_in_ten_hours(tmp_path / "later.funscript"))))
        s.wait_until(lambda: read_satellite_status(portrait.status_file).funscript_resting,
                     description="the portrait player to reach a stretch with no funscript")
        time.sleep(2.0)
        resting = senders(tcode_heard(sink, seconds=4))
        assert resting and portraits_line not in resting, (portraits_line, resting)


def test_fun_time_the_max_intensity_holds_a_side_players_funscript_down_too(
    shared_integration_session: FunTimeIntegrationSession, tmp_path: Path,
):
    s = shared_integration_session
    sink = s.config.main_player_tcode.port
    with _the_portrait_player_driving_the_osr2(s, tmp_path):
        try:
            free = _osr2_heard_until(sink, _osr2_swinging)
            s.write_dashboard_command("max_intensity_0")
            held = _osr2_heard_until(sink, _osr2_still)
        finally:
            s.write_dashboard_command("max_intensity_100")

    assert _osr2_swinging(free), free
    assert _osr2_still(held), held


def _held_still(session: FunTimeIntegrationSession, side: SatelliteFiles) -> SatelliteStatus:
    session.wait_until(
        lambda: bool(read_satellite_status(side.status_file).video),
        description="the player to name the clip it opened on",
    )
    session.wait_until(
        lambda: write_flag(side.paused_file, True) and read_satellite_status(side.status_file).paused,
        description="the player to hold its clip",
    )
    return published_status(read_satellite_status, side.status_file)


def _showing(side: SatelliteFiles) -> Path:
    return Path(read_satellite_status(side.status_file).video or "x").resolve()


def test_fun_time_landscape_trash_of_a_favorite_only_unfavorites_it(
    isolated_integration_session: FunTimeIntegrationSession,
):
    """Every sample the isolated session links in is seeded into its favs.csv, so
    the clip on screen is a favorite and discard demotes it: the row leaves the
    list, the file stays in the library, and the clip stays in the rotation —
    W then A comes straight back to it."""
    s = isolated_integration_session
    landscape = s.config.satellite(Player.LANDSCAPE)
    held = _held_still(s, landscape)

    s.write_dashboard_command("landscape_trash")
    chunk = s.wait_for_new_log("Removed from favorites on player 3:")
    match = re.search(r"Removed from favorites on player 3:\s*(.+)", chunk)
    assert match, "Expected the unfavorite log chunk to include the landscape path"
    demoted_path = Path(match.group(1).strip()).resolve()
    assert demoted_path == Path(held.video).resolve()

    s.wait_until(
        lambda: not s.favs_contains(demoted_path),
        description="landscape sample to be removed from integration favs.csv",
    )
    assert demoted_path.exists(), "A demoted favorite must stay where it is"
    assert not any(p.name == demoted_path.name for p in s.weird_dir.iterdir())

    s.wait_until(
        lambda: any(n.message == "Unfavorited" and n.source == "landscape" for n in s.notices()),
        description="an \"Unfavorited\" notice over the landscape player",
    )

    s.wait_until(
        lambda: _showing(landscape) != demoted_path,
        description="the landscape player to move on from the demoted clip",
    )
    assert read_satellite_status(landscape.status_file).playlist_length == held.playlist_length, (
        "the demoted clip must stay in the landscape rotation"
    )
    s.write_dashboard_command("landscape_prev")
    s.wait_until(
        lambda: _showing(landscape) == demoted_path,
        description="landscape prev to land back on the demoted clip, still in the rotation",
    )


def test_fun_time_portrait_trash_of_a_non_favorite_moves_it_to_weird(
    isolated_integration_session: FunTimeIntegrationSession,
):
    """Discarding a clip that is not in the favorites is the full condemnation —
    it leaves the playlist and the file moves into the weird dir."""
    status_file = isolated_integration_session.config.paths.state_dir / "portrait_status.txt"
    isolated_integration_session.wait_until(
        lambda: bool(read_satellite_status(status_file).video),
        description="portrait satellite to publish the clip it is playing",
    )
    # Hold the clip before reading which one it is.  These are a few seconds long,
    # so an unlocked satellite can advance between the read below and the discard,
    # and then the discard meets a *different* clip — one still in the favorites, so
    # it is unfavorited instead of condemned and the weird-dir wait times out on a
    # file that was never going there.  A lock is repeat-one, which closes that.
    isolated_integration_session.write_dashboard_command("portrait_lock_on")
    isolated_integration_session.wait_for_new_log("Locked portrait satellite")
    held = published_status(read_satellite_status, status_file).video
    # Take the clip out of the favorites the way the app does, so the discard
    # below meets an ordinary library file rather than a favorite.
    remove_from_favs(isolated_integration_session.favs_file, held)

    isolated_integration_session.write_dashboard_command("portrait_trash")
    chunk = isolated_integration_session.wait_for_new_log("Discarding from player 2:")
    match = re.search(r"Discarding from player 2:\s*(.+)", chunk)
    assert match, "Expected discard log chunk to include the discarded portrait path"
    trashed_path = Path(match.group(1).strip()).resolve()

    isolated_integration_session.wait_until(
        lambda: not isolated_integration_session.favs_contains(trashed_path),
        description="portrait sample to be removed from integration favs.csv",
    )
    isolated_integration_session.wait_until(
        lambda: any(p.name == trashed_path.name for p in isolated_integration_session.weird_dir.iterdir()),
        description="portrait sample to be moved into the integration weird dir",
    )
    isolated_integration_session.wait_until(
        lambda: any(
            n.message == "Marked weird" and n.source == "portrait"
            for n in isolated_integration_session.notices()
        ),
        description='a "Marked weird" notice over the portrait player',
    )


def test_fun_time_reset_all_leaves_every_player_unlocked(
    isolated_integration_session: FunTimeIntegrationSession,
):
    session = isolated_integration_session
    state_dir = session.config.paths.state_dir
    main_player_status = session.config.main_player_status_file
    satellite_statuses = [state_dir / f"{side}_status.txt" for side in ("portrait", "landscape")]
    for side in ("portrait", "landscape"):
        session.write_dashboard_command(f"{side}_lock_on")
        session.wait_for_new_log(f"Locked {side} satellite")
    session.wait_until(
        lambda: read_main_player_status(main_player_status).locked and all(
            read_satellite_status(path).locked for path in satellite_statuses),
        description="all three players to report themselves locked",
    )

    session.write_dashboard_command("all_reset")

    session.wait_until(
        lambda: main_player_status.exists()
        and not read_main_player_status(main_player_status).locked and all(
            read_satellite_status(path).video and not read_satellite_status(path).locked
            for path in satellite_statuses),
        description="Reset All to leave all three players unlocked",
    )


def _videos(playlist: Path) -> list[str]:
    """The video column of each playlist line, dropping any funscript."""
    return [
        line.partition("\t")[0]
        for line in playlist.read_text(encoding="utf-8").splitlines()
    ]


# Two session starts, a quit and up to five command waits, each at the family's budget:
# on a loaded machine the two starts alone have taken the suite's whole 240s.
TWO_SESSIONS_BUDGET_S = 2 * START_BUDGET_S + QUIT_BUDGET_S + 5 * COMMAND_BUDGET_S


@pytest.mark.timeout(TWO_SESSIONS_BUDGET_S)
def test_fun_time_reopens_on_the_video_it_was_closed_on():
    """Close Fun Time on one video and it comes back on that one.

    Nothing is written at shutdown: each player publishes what it is showing to
    its status file every tick, and the next start rotates that player's playlist
    onto the video named there rather than building a fresh shuffle.  Only a real
    session proves it — the record has to survive the force-kill that ends one.

    The main player carries the assertion because its library is the several-entry one, so
    the resumed playlist has to be an exact rotation of the last one — an order
    a rebuild would reproduce only by chance.
    """
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)

    first = FunTimeIntegrationSession(config_path)
    playlist = first.config.main_player_playlist_file
    try:
        first.start()
        opened_with = _videos(playlist)
        # Navigate off the top of the playlist, so resuming onto entry 0 — which
        # every session does anyway — cannot pass this by accident.
        first.write_dashboard_command("main_next")
        first.wait_until(
            lambda: first.read_main_player_status().video not in ("", opened_with[0]),
            description="the main player to navigate off the first video",
        )
        # Then freeze the session before closing it. Some of the main player library
        # is seconds long, and a main player that auto-advanced while the shutdown ran
        # would leave a different video than the one read here.
        first.write_dashboard_command("omnipause_toggle")
        first.wait_until(
            lambda: first.read_main_player_status().paused,
            description="the main player to freeze under OmniPause",
        )
        left_on = first.read_main_player_status().video
        assert left_on != opened_with[0], "the session must close off the top of its playlist"
        first.quit_gracefully()
    finally:
        first.stop()

    second = FunTimeIntegrationSession(config_path)
    try:
        second.start()
        resumed = _videos(playlist)
        position = opened_with.index(left_on)
        assert resumed == opened_with[position:] + opened_with[:position], (
            "the reopened playlist must be last session's rotated onto the video it "
            f"was closed on (entry {position}), not a fresh shuffle\n"
            f"left on: {left_on}\n"
            "opened with:\n" + "\n".join(opened_with)
            + "\nreopened with:\n" + "\n".join(resumed)
        )
        second.wait_until(
            lambda: second.read_main_player_status().video == left_on,
            description="the main player to come back up on the video the last session ended on",
        )
    finally:
        second.stop()


@pytest.mark.timeout(TWO_SESSIONS_BUDGET_S)
def test_fun_time_reopens_at_the_speed_and_the_hold_it_was_closed_with():
    """Close Fun Time with the room playing fast and unheld, and it comes back
    that way.

    A player's rate and its hold live in the player process and in nothing the
    next one reads, so the only record of either is what each player publishes to
    its status file every tick.  The reopened session re-sends both, and only a
    real session proves that: they have to survive the force-kill that ends one
    and arrive as verbs the new player obeys.
    """
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)

    first = FunTimeIntegrationSession(config_path)
    try:
        first.start()
        first.write_dashboard_command("main_video_activate")
        first.write_dashboard_command("play")
        first.wait_until(
            lambda: _loaded_and_playing(first.read_main_player_status()),
            description="the main player to be playing",
        )
        first.write_dashboard_command("main_player_speed_150")
        first.write_dashboard_command("main_player_lock")
        first.wait_until(
            lambda: (first.read_main_player_status().speed == 1.5
                     and not first.read_main_player_status().locked),
            description="the main player to take one and a half speed and let its video go",
        )
        first.quit_gracefully()
    finally:
        first.stop()

    second = FunTimeIntegrationSession(config_path)
    try:
        second.start()
        second.wait_until(
            lambda: (second.read_main_player_status().speed == 1.5
                     and not second.read_main_player_status().locked),
            description="the reopened main player to come back fast and still unheld",
        )
    finally:
        second.stop()


def _clips_oldest_to_newest(folder: Path, *names: str) -> list[Path]:
    """*names* as two-second test patterns in *folder*, the first the oldest arrival."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is what makes this test's clips, and it is not on PATH")
    clips = []
    for arrived, name in enumerate(names, start=1):
        clip = folder / name
        subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30",
             "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
            check=True, timeout=60, **hidden_subprocess_kwargs())
        os.utime(clip, (1_000_000_000 + arrived, 1_000_000_000 + arrived))
        clips.append(clip)
    return clips


def _config_whose_genau_browses_in_folder_order(temp_root: Path, clip_folder: Path) -> Path:
    """Genau on *clip_folder*, unshuffled, so the order it reopens in can be
    told from the order it was left in."""
    config_path = build_integration_config(temp_root)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["paths"]["clips_dir"] = str(clip_folder)
    genau_config_path = Path(config["paths"]["genau_config_path"])
    genau_config = json.loads(genau_config_path.read_text(encoding="utf-8"))
    genau_config["genau"]["shuffle_on_load"] = False
    genau_config_path.write_text(json.dumps(genau_config), encoding="utf-8")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def _wait_for_genau_on(session: FunTimeIntegrationSession, clip: Path, waiting_for: str) -> None:
    def showing() -> bool:
        published = read_genau_status(session.config.genau_status_file).clip
        return bool(published) and Path(published).resolve() == clip.resolve()

    session.wait_until(showing, description=waiting_for)


@pytest.mark.timeout(TWO_SESSIONS_BUDGET_S)
def test_fun_time_reopens_genau_in_the_order_it_was_left_browsing():
    """Close Fun Time with Genau browsing Latest and it comes back browsing
    Latest, on the clip it was on.

    Genau has no playlist file to resume from: it rescans its folder every
    launch, so its order and its clip are both named on its launch line, off
    the state and the status the last session published.  Only a real session
    proves the two arrive together -- the LATEST verb sent after the launch
    browses the new order from its top and loses the clip.

    Three clips, oldest to newest: alpha, beta, gamma.  Latest is gamma, beta,
    alpha, and the session closes on beta -- so a reopen that kept the order
    steps from beta to alpha, where the folder's order would step to gamma.
    """
    temp_root = build_integration_temp_root()
    clip_folder = temp_root / "genau_clips"
    (clip_folder / "2D" / "AI").mkdir(parents=True)
    alpha, beta, gamma = _clips_oldest_to_newest(clip_folder / "2D" / "AI",
                                                 "alpha.mp4", "beta.mp4", "gamma.mp4")
    config_path = _config_whose_genau_browses_in_folder_order(temp_root, clip_folder)

    first = FunTimeIntegrationSession(config_path)
    try:
        first.start()
        first.write_dashboard_command("genau_activate")
        first.wait_for_new_log("Switched to genau mode")
        first.write_dashboard_command("main_latest")
        _wait_for_genau_on(first, gamma, "Latest to put the newest clip on screen")
        first.write_dashboard_command("genau_next_clip")
        _wait_for_genau_on(first, beta, "Genau to step on to the next-newest clip")
        first.quit_gracefully()
    finally:
        first.stop()

    second = FunTimeIntegrationSession(config_path)
    try:
        second.start()
        _wait_for_genau_on(
            second, beta, "Genau to come back up on the clip the last session ended on")
        assert read_shared_state(shared_state_path(second.config.paths.state_dir)).genau_latest, (
            "the reopened session must still say Genau is browsing Latest")
        second.write_dashboard_command("genau_next_clip")
        _wait_for_genau_on(
            second, alpha, "the reopened Genau to step on in the order the last session left")
    finally:
        second.stop()


@pytest.mark.timeout(TWO_SESSIONS_BUDGET_S)
def test_fun_time_reopens_holding_the_osr2_to_the_max_intensity_it_was_closed_at():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)

    first = FunTimeIntegrationSession(config_path)
    state_file = shared_state_path(first.config.paths.state_dir)
    try:
        first.start()
        first.write_dashboard_command("max_intensity_0")
        first.wait_until(
            lambda: getattr(read_shared_state(state_file), "max_intensity", None) == 0,
            description="the session to hold the max intensity at zero",
        )
        first.quit_gracefully()
    finally:
        first.stop()

    second = FunTimeIntegrationSession(config_path)
    sink = second.config.main_player_tcode.port
    try:
        second.start()
        came_back_at = getattr(read_shared_state(state_file), "max_intensity", None)
        second.write_dashboard_command("genau_activate")
        second.wait_for_new_log("Switched to genau mode")
        second.write_dashboard_command("play")
        _wait_for_genau_to_say(second, playing=1, max_intensity=0)
        held = _osr2_heard_until(sink, _osr2_still)
        for command in ("max_intensity_100", "robot_hand_amp_100", "robot_hand_speed_50"):
            second.write_dashboard_command(command)
        free = _osr2_heard_until(sink, _osr2_swinging)
    finally:
        second.stop()

    assert came_back_at == 0
    assert _osr2_still(held), held
    assert _osr2_swinging(free), free


def test_fun_time_quit_cleans_up_processes():
    """The real quit path (AHK exit → orchestrator cleanup) must kill all child processes."""
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()

        child_pids = session.read_child_pids()
        live_pids = {name: pid for name, pid in child_pids.items() if pid and is_process_alive(pid)}
        assert live_pids, "Expected at least some child processes to be running after startup"

        session.quit_gracefully()

        assert session._proc.poll() is not None, "Orchestrator should have exited"

        def still_alive() -> dict[str, int]:
            return {name: pid for name, pid in live_pids.items() if is_process_alive(pid)}

        session.wait_until(
            lambda: not still_alive(),
            timeout=QUIT_BUDGET_S,
            description=lambda: f"the quit path to end every child it launched; still running: "
                                f"{still_alive()}",
        )
    finally:
        session.stop()


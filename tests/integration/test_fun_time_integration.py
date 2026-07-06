from __future__ import annotations

import contextlib
import ctypes
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

from fun_time.win32 import (
    find_window_by_pid,
    find_window_by_title,
    get_foreground_window,
    is_process_alive,
    is_window_topmost,
)

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
        shutil.rmtree(temp_root, ignore_errors=True)


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
        shutil.rmtree(temp_root, ignore_errors=True)


def test_fun_time_startup_runtime_smoke(shared_integration_session: FunTimeIntegrationSession):
    assert shared_integration_session.windows_bridge_log.exists()
    assert shared_integration_session.orchestrator_log.exists()


def test_fun_time_portrait_lock_unlock_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait VLC", timeout=12)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Unlocked portrait VLC", timeout=12)


def test_fun_time_omnipause_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: entering", timeout=12)

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: leaving", timeout=12)


def test_fun_time_fmode_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("fmode_toggle")
    shared_integration_session.wait_for_new_log("F-mode hotkey: enabled", timeout=12)

    shared_integration_session.write_dashboard_command("fmode_toggle")
    shared_integration_session.wait_for_new_log("F-mode hotkey: disabled", timeout=12)


def test_fun_time_genau_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    """Pressing 'g' (genau_activate) then 'n' (nau_activate) switches modes."""
    s = shared_integration_session
    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode", timeout=12)

    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Genau paused file to flip off (active)",
    )
    s.wait_until(
        lambda: s.config.nau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Nau paused file to flip on (inactive)",
    )

    s.write_dashboard_command("nau_activate")
    s.wait_for_new_log("Switched to nau mode", timeout=12)

    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau paused file to flip back on (inactive)",
    )
    s.wait_until(
        lambda: s.config.nau_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Nau paused file to flip back off (active)",
    )


def test_fun_time_mode_switch_swaps_primary_slot_window_visibility(shared_integration_session: FunTimeIntegrationSession):
    """The primary-slot players share one screen rect, so a mode switch
    swaps window VISIBILITY: exactly the active mode's player is on screen
    (find_window_by_title only sees visible windows — a hidden window's
    lookup returns 0). Nau carries its static non-topmost band (it rides under
    Genau's HUD), so it stays OUT of the topmost band even while visible."""
    s = shared_integration_session

    # nau mode: Nau visible (and non-topmost, per its static band), Genau
    # hidden.  The lookup is exact because 'Nau' is a substring of 'Genau'.
    s.wait_until(
        lambda: find_window_by_title("Nau", exact=True) != 0,
        timeout=12,
        description="Nau window to be visible in nau mode",
    )
    nau_hwnd = find_window_by_title("Nau", exact=True)
    s.wait_until(
        lambda: not is_window_topmost(nau_hwnd),
        timeout=5,
        description="Nau to stay out of the topmost band while visible",
    )
    s.wait_until(
        lambda: find_window_by_title("Genau") == 0,
        timeout=12,
        description="Genau window to be hidden in nau mode",
    )

    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode", timeout=12)

    s.wait_until(
        lambda: find_window_by_title("Nau", exact=True) == 0,
        timeout=12,
        description="Nau window to hide when Genau mode activates",
    )
    s.wait_until(
        lambda: find_window_by_title("Genau") != 0,
        timeout=12,
        description="Genau window to become visible in genau mode",
    )

    s.write_dashboard_command("nau_activate")
    s.wait_for_new_log("Switched to nau mode", timeout=12)

    s.wait_until(
        lambda: find_window_by_title("Nau", exact=True) != 0,
        timeout=12,
        description="Nau window to reappear in nau mode",
    )
    s.wait_until(
        lambda: find_window_by_title("Genau") == 0,
        timeout=12,
        description="Genau window to hide again in nau mode",
    )


def test_fun_time_landscape_lock_unlock_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape VLC", timeout=12)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Unlocked landscape VLC", timeout=12)


def test_fun_time_portrait_next_cancels_lock(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait VLC", timeout=12)

    shared_integration_session.write_dashboard_command("portrait_next")
    time.sleep(1.5)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait VLC", timeout=12)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Unlocked portrait VLC", timeout=12)


def test_fun_time_landscape_next_cancels_lock(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape VLC", timeout=12)

    shared_integration_session.write_dashboard_command("landscape_next")
    time.sleep(1.5)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape VLC", timeout=12)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Unlocked landscape VLC", timeout=12)


def test_fun_time_omnipause_while_genau_mode(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("genau_activate")
    shared_integration_session.wait_for_new_log("Switched to genau mode", timeout=12)

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: entering", timeout=12)
    shared_integration_session.wait_until(
        lambda: shared_integration_session.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau paused file to flip on",
    )

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: leaving", timeout=12)
    shared_integration_session.wait_until(
        lambda: shared_integration_session.config.genau_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Genau paused file to flip off",
    )

    shared_integration_session.write_dashboard_command("nau_activate")
    shared_integration_session.wait_for_new_log("Switched to nau mode", timeout=12)


def test_fun_time_omnipause_does_not_kill_genau(shared_integration_session: FunTimeIntegrationSession):
    """Regression: omnipause must pause Genau, not close it.

    The old AHK HandleOmniPauseToggle never removed Genau's topmost
    flag.  When omnipause was ported to Python, an explicit
    set_topmost(Genau, False) was added by mistake, causing the
    window to fall behind other windows (appearing "closed").  Verify the
    Genau process survives an omnipause round-trip while in genau
    mode.
    """
    s = shared_integration_session
    rh_pid = s.read_genau_pid()
    assert is_process_alive(rh_pid), "Genau should be alive before test"

    s.write_dashboard_command("genau_activate")
    s.wait_for_new_log("Switched to genau mode", timeout=12)

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: entering", timeout=12)
    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau paused file to flip on",
    )

    # Genau must still be running — omnipause should pause, not close.
    assert is_process_alive(rh_pid), (
        "Genau process died during omnipause — "
        "Esc should pause Genau, not close it"
    )

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: leaving", timeout=12)

    assert is_process_alive(rh_pid), "Genau should survive leaving omnipause"

    s.write_dashboard_command("nau_activate")
    s.wait_for_new_log("Switched to nau mode", timeout=12)


def test_fun_time_nau_nudge_seeks_playback(shared_integration_session: FunTimeIntegrationSession):
    """primary_nudge_next/prev in nau mode drive Nau's seek via its command
    file, observed through Nau's published status position."""
    s = shared_integration_session

    # Let the orchestrator finish processing commands from prior tests.
    time.sleep(2.0)
    # Ensure we're in nau mode so Nau is the active display and its seek is
    # observable in the published status.
    s.write_dashboard_command("nau_activate")
    # Wait for a *loaded* video: a non-zero duration means mpv knows the
    # length, so a seek target won't be clamped to 0 by an as-yet-unknown
    # duration (which would make the forward seek a no-op).
    s.wait_until(
        lambda: s.read_nau_status().video != "" and s.read_nau_duration_ms() > 0,
        timeout=15,
        description="Nau to report a loaded video with a known duration",
    )

    # The library is a random sample of real clips with mixed lengths, and a
    # ±10s nudge is only observable on a video long enough to hold ~15s of
    # forward headroom. Advance through the playlist until one loads that is
    # long enough for the seek assertions below.
    MIN_DURATION_MS = 25_000
    for _ in range(12):
        if s.read_nau_duration_ms() >= MIN_DURATION_MS:
            break
        prev_video = s.read_nau_status().video
        s.write_dashboard_command("primary_next")
        s.wait_until(
            lambda pv=prev_video: (
                s.read_nau_status().video not in ("", pv)
                and s.read_nau_duration_ms() > 0
            ),
            timeout=15,
            description="Nau to load the next video",
        )
    duration = s.read_nau_duration_ms()
    assert duration >= MIN_DURATION_MS, (
        f"no sampled video long enough for a ±10s nudge test: duration={duration}"
    )

    # The looping playhead sits at an arbitrary spot; if it is near the end, a
    # forward seek clamps at the duration and never advances. Nudge back until
    # there is comfortable forward headroom first.
    for _ in range(30):
        if s.read_nau_status().position_ms <= duration - 15_000:
            break
        s.write_dashboard_command("primary_nudge_prev")
        time.sleep(0.4)

    before = s.read_nau_status().position_ms
    assert before <= duration - 12_000, (
        f"could not create forward headroom: pos={before} duration={duration}"
    )

    s.write_dashboard_command("primary_nudge_next")
    s.wait_until(
        lambda: s.read_nau_status().position_ms >= before + 9_000,
        timeout=10,
        description=f"Nau to jump forward ~10s after nudge (before={before}, duration={duration})",
    )

    after_fwd = s.read_nau_status().position_ms
    s.write_dashboard_command("primary_nudge_prev")
    s.wait_until(
        lambda: s.read_nau_status().position_ms <= after_fwd - 9_000,
        timeout=10,
        description=f"Nau to jump back ~10s after nudge (after_fwd={after_fwd})",
    )


def test_fun_time_nau_record_loop_cancel_cycle(shared_integration_session: FunTimeIntegrationSession):
    """The record gesture round-trips through Nau: record → looping → cancel,
    observed through Nau's published loop state."""
    s = shared_integration_session
    s.wait_until(
        lambda: s.read_nau_status().video != "",
        timeout=15,
        description="Nau status file to report a current video",
    )
    assert s.read_nau_status().state == "normal"

    s.write_dashboard_command("nau_record_tap")
    s.wait_until(
        lambda: s.read_nau_status().state == "recording",
        timeout=10,
        description="Nau to enter recording state",
    )

    s.write_dashboard_command("nau_record_tap")
    s.wait_until(
        lambda: s.read_nau_status().state == "looping",
        timeout=10,
        description="Nau to enter looping state",
    )

    s.write_dashboard_command("nau_loop_cancel")
    s.wait_until(
        lambda: s.read_nau_status().state == "normal",
        timeout=10,
        description="Nau to return to normal state",
    )


def test_fun_time_hybrid_keeps_nau_as_the_display(shared_integration_session: FunTimeIntegrationSession):
    """Hybrid keeps Nau as the on-screen player while Genau drives the OSR2, so
    the video Nau was playing simply continues — there is no separate primary-VLC
    handoff — and prev/next/nudge dispatch to Nau just as they do in nau mode.

    (The precise +10s Nau seek is covered by the nau-mode nudge test above,
    which exercises the identical dispatch path.)

    Must run before isolated-session tests (trash), whose teardown kills all
    recent VLC processes and would leave the shared session's VLC dead.
    """
    s = shared_integration_session

    nau_video_before = s.read_nau_status().video
    assert nau_video_before, "expected Nau to be playing before switching to hybrid"

    s.write_dashboard_command("hybrid_activate")
    s.wait_for_new_log("Switched to hybrid mode", timeout=12)

    # Nau stays the display and keeps playing its current video — no handoff to
    # a separate primary VLC.
    s.wait_until(
        lambda: s.read_nau_status().video == nau_video_before,
        timeout=12,
        description="Nau to keep playing its current video in hybrid mode",
    )

    # A nudge in hybrid now reaches the normal dispatch path (previously it was
    # intercepted and stacked into a primary-VLC seek).
    s.write_dashboard_command("primary_nudge_next")
    s.wait_for_new_log("Dispatching command: primary_nudge_next", timeout=10)

    s.write_dashboard_command("nau_activate")
    s.wait_for_new_log("Switched to nau mode", timeout=12)


@contextlib.contextmanager
def _foreground_sentinel():
    """Create a tiny popup window and make it the foreground window.

    Gives focus-stealing tests a deterministic foreground to verify.
    The production code saves the foreground hwnd in integration mode
    and restores it after minimizing VLC windows.

    Uses the Alt-key trick to gain foreground activation privilege —
    without it, SetForegroundWindow fails from a background process
    (e.g. pytest running under a terminal).
    """
    _user32 = ctypes.windll.user32
    WS_POPUP = 0x80000000
    WS_VISIBLE = 0x10000000
    hwnd = _user32.CreateWindowExW(
        0, "Static", "FocusSentinel",
        WS_POPUP | WS_VISIBLE,
        0, 0, 1, 1,
        0, 0, 0, 0,
    )
    assert hwnd, "Failed to create sentinel window"
    try:
        # Press/release Alt to gain foreground activation privilege.
        VK_MENU = 0x12
        KEYEVENTF_EXTENDEDKEY = 0x0001
        KEYEVENTF_KEYUP = 0x0002
        _user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
        _user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
        _user32.SetForegroundWindow(hwnd)
        yield hwnd
    finally:
        _user32.DestroyWindow(hwnd)


def test_fun_time_startup_does_not_steal_foreground():
    """Session startup must not steal the user's foreground window.

    Creates a sentinel window as a deterministic foreground target so the
    production code's save/restore cycle has a known hwnd to work with.
    The lock held during startup + minimize prevents VLC's Qt from calling
    SetForegroundWindow; after unlock the restore puts the sentinel back.
    """
    with _foreground_sentinel() as sentinel_hwnd:
        assert get_foreground_window() == sentinel_hwnd, (
            "Sentinel failed to become foreground — test environment issue"
        )

        temp_root = build_integration_temp_root()
        config_path = build_integration_config(temp_root)
        session = FunTimeIntegrationSession(config_path)
        try:
            session.start()

            fg_hwnd = get_foreground_window()
            child_pids = session.read_child_pids()
            for name, pid in child_pids.items():
                if not pid:
                    continue
                child_hwnd = find_window_by_pid(pid)
                if child_hwnd:
                    assert child_hwnd != fg_hwnd, (
                        f"Foreground stolen by {name} (pid={pid}, hwnd={child_hwnd})"
                    )
        finally:
            session.stop()
            shutil.rmtree(temp_root, ignore_errors=True)


def test_fun_time_landscape_trash_updates_temp_state(isolated_integration_session: FunTimeIntegrationSession):
    isolated_integration_session.write_dashboard_command("landscape_trash")
    chunk = isolated_integration_session.wait_for_new_log("Discarding from player 3:", timeout=12)
    match = re.search(r"Discarding from player 3:\s*(.+)", chunk)
    assert match, "Expected discard log chunk to include the discarded landscape path"
    trashed_path = Path(match.group(1).strip()).resolve()

    isolated_integration_session.wait_until(
        lambda: not isolated_integration_session.favs_contains(trashed_path),
        timeout=12,
        description="landscape sample to be removed from integration favs.csv",
    )
    isolated_integration_session.wait_until(
        lambda: any(p.name == trashed_path.name for p in isolated_integration_session.weird_dir.iterdir()),
        timeout=12,
        description="landscape sample to be moved into the integration weird dir",
    )


def test_fun_time_portrait_trash_updates_temp_state(isolated_integration_session: FunTimeIntegrationSession):
    isolated_integration_session.write_dashboard_command("portrait_trash")
    chunk = isolated_integration_session.wait_for_new_log("Discarding from player 2:", timeout=12)
    match = re.search(r"Discarding from player 2:\s*(.+)", chunk)
    assert match, "Expected discard log chunk to include the discarded portrait path"
    trashed_path = Path(match.group(1).strip()).resolve()

    isolated_integration_session.wait_until(
        lambda: not isolated_integration_session.favs_contains(trashed_path),
        timeout=12,
        description="portrait sample to be removed from integration favs.csv",
    )
    isolated_integration_session.wait_until(
        lambda: any(p.name == trashed_path.name for p in isolated_integration_session.weird_dir.iterdir()),
        timeout=12,
        description="portrait sample to be moved into the integration weird dir",
    )


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

        session.quit_gracefully(timeout=15.0)

        assert session._proc.poll() is not None, "Orchestrator should have exited"

        deadline = time.time() + 5.0
        while time.time() < deadline:
            still_alive = {name: pid for name, pid in live_pids.items() if is_process_alive(pid)}
            if not still_alive:
                break
            time.sleep(0.5)
        assert not still_alive, (
            f"Quit path failed to clean up processes: {still_alive}\n{session._log_tail()}"
        )
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


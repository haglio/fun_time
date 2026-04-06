from __future__ import annotations

import contextlib
import ctypes
import configparser
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

from fun_time.vlc_actions import ensure_playback_state, get_playback_state, get_playback_time, vlc_http_cmd
from fun_time.win32 import find_window_by_pid, get_foreground_window, is_window_topmost

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is still running.

    On Windows, os.kill(pid, 0) can return True for zombie processes
    whose kernel objects haven't been released.  GetExitCodeProcess
    reliably distinguishes running (STILL_ACTIVE) from terminated.
    """
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    exit_code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return exit_code.value == STILL_ACTIVE


def _read_vlc_config_from_manifest(session: FunTimeIntegrationSession) -> tuple[int, str]:
    """Return (primary_vlc_port, vlc_password) from the runtime manifest."""
    manifest_path = session.config.paths.state_dir / "windows_bridge_launch.ini"
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(manifest_path), encoding="utf-8")
    port = int(parser["vlc"]["primary_vlc_port"])
    password = parser["vlc"]["vlc_pass"]
    return port, password


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
    """Pressing 'g' (genau_toggle) activates then deactivates Genau."""
    s = shared_integration_session
    s.write_dashboard_command("genau_toggle")
    s.wait_for_new_log("Genau activated", timeout=12)

    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Genau paused file to flip off (active)",
    )

    s.write_dashboard_command("genau_toggle")
    s.wait_for_new_log("Genau deactivated", timeout=12)

    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau paused file to flip back on (inactive)",
    )



def test_fun_time_primary_vlc_not_topmost_in_genau_mode(shared_integration_session: FunTimeIntegrationSession):
    """Primary VLC must leave the TOPMOST z-band while Genau mode is
    active so VLC video transitions cannot bring it above Genau."""
    s = shared_integration_session
    primary_pid = s.read_child_pids()["primary_pid"]
    hwnd = find_window_by_pid(primary_pid)
    assert hwnd, f"Primary VLC window not found for pid {primary_pid}"

    assert is_window_topmost(hwnd), "Primary VLC should be TOPMOST before Genau activation"

    s.write_dashboard_command("genau_toggle")
    s.wait_for_new_log("Genau activated", timeout=12)

    s.wait_until(
        lambda: not is_window_topmost(find_window_by_pid(primary_pid)),
        timeout=12,
        description="Primary VLC to lose TOPMOST when Genau is active",
    )

    s.write_dashboard_command("genau_toggle")
    s.wait_for_new_log("Genau deactivated", timeout=12)

    s.wait_until(
        lambda: is_window_topmost(find_window_by_pid(primary_pid)),
        timeout=12,
        description="Primary VLC to regain TOPMOST after Genau deactivated",
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
    shared_integration_session.write_dashboard_command("genau_toggle")
    shared_integration_session.wait_for_new_log("Genau activated", timeout=12)

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

    shared_integration_session.write_dashboard_command("genau_toggle")
    shared_integration_session.wait_for_new_log("Genau deactivated", timeout=12)



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
    assert _is_pid_alive(rh_pid), "Genau should be alive before test"

    s.write_dashboard_command("genau_toggle")
    s.wait_for_new_log("Genau activated", timeout=12)

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: entering", timeout=12)
    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau paused file to flip on",
    )

    # Genau must still be running — omnipause should pause, not close.
    assert _is_pid_alive(rh_pid), (
        "Genau process died during omnipause — "
        "Esc should pause Genau, not close it"
    )

    s.write_dashboard_command("omnipause_toggle")
    s.wait_for_new_log("OmniPause: leaving", timeout=12)

    assert _is_pid_alive(rh_pid), "Genau should survive leaving omnipause"

    s.write_dashboard_command("genau_toggle")
    s.wait_for_new_log("Genau deactivated", timeout=12)



def test_fun_time_vlc_nudge_forward_and_backward(shared_integration_session: FunTimeIntegrationSession):
    """Verify vlc_nudge_next/prev actually seek VLC's primary player by ~10 seconds.

    Must run before isolated-session tests (trash), whose teardown kills all
    recent VLC processes and would leave the shared session's VLC dead.
    """
    s = shared_integration_session
    port, password = _read_vlc_config_from_manifest(s)

    # The previous test may have toggled omnipause, leaving VLC paused.
    # Actively drive VLC into "playing" state rather than passively waiting
    # for the orchestrator's async omnipause-leave to complete the resume.
    # The sleep lets the orchestrator finish processing queued commands
    # (omnipause-leave, genau-deactivation) before we send nudge commands
    # that compete for the same dispatch loop.
    time.sleep(2.0)
    ensure_playback_state(port, password, should_play=True)
    s.wait_until(
        lambda: get_playback_state(port, password) == "playing",
        timeout=10,
        description="VLC to resume playing before nudge test",
    )
    # Freeze playback rate to prevent auto-advance.  VLC stays in
    # "playing" state (seek commands work normally) but can never reach
    # the end of the video and wrap around, which would reset the
    # position and break the nudge assertions.
    vlc_http_cmd(port, "rate&val=0.1", password)

    # Seek to 30s so there's room to nudge both directions without hitting 0 or end.
    # Retry until VLC reports a position near 30s — the seek + HTTP response can
    # lag significantly when the suite has multiple VLC instances running.
    vlc_http_cmd(port, "seek&val=30", password)
    result: list[float] = []
    s.wait_until(
        lambda: (t := get_playback_time(port, password)) is not None and t >= 25 and (result.append(t) or True),
        timeout=10,
        description="VLC to reach seek position (~30s)",
    )
    before = result[0]

    # --- nudge forward ---
    # The dashboard command goes through the orchestrator's async dispatch
    # loop.  Under load the loop can stall, so re-send the command if VLC
    # doesn't respond within a short window.
    for _attempt in range(3):
        s.write_dashboard_command("vlc_nudge_next")
        try:
            s.wait_until(
                lambda: (t := get_playback_time(port, password)) is not None and t >= before + 7,
                timeout=8,
                description="VLC playback time to advance ~10s after nudge forward",
            )
            break
        except AssertionError:
            if _attempt == 2:
                raise

    # Read the forward position.  The rate freeze (0.1x) keeps VLC
    # effectively frozen, so pausing is unnecessary — and the old
    # pause/resume cycle could disrupt VLC's window state, causing
    # the subsequent backward nudge's PostMessage to miss.
    after_forward = get_playback_time(port, password)
    assert after_forward is not None

    # --- nudge backward ---
    for _attempt in range(3):
        s.write_dashboard_command("vlc_nudge_prev")
        try:
            s.wait_until(
                lambda: (t := get_playback_time(port, password)) is not None and t <= after_forward - 7,
                timeout=8,
                description="VLC playback time to retreat ~10s after nudge backward",
            )
            break
        except AssertionError:
            if _attempt == 2:
                raise



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
        live_pids = {name: pid for name, pid in child_pids.items() if pid and _is_pid_alive(pid)}
        assert live_pids, "Expected at least some child processes to be running after startup"

        session.quit_gracefully(timeout=15.0)

        assert session._proc.poll() is not None, "Orchestrator should have exited"

        deadline = time.time() + 5.0
        while time.time() < deadline:
            still_alive = {name: pid for name, pid in live_pids.items() if _is_pid_alive(pid)}
            if not still_alive:
                break
            time.sleep(0.5)
        assert not still_alive, (
            f"Quit path failed to clean up processes: {still_alive}\n{session._log_tail()}"
        )
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


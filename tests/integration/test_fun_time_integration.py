from __future__ import annotations

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



def test_fun_time_robot_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    assert shared_integration_session.genau_enabled_file.read_text(encoding="utf-8") == "1"
    shared_integration_session.write_dashboard_command("robot_toggle")
    shared_integration_session.wait_until(
        lambda: shared_integration_session.genau_enabled_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Genau enabled file to flip off",
    )

    shared_integration_session.write_dashboard_command("robot_toggle")
    shared_integration_session.wait_until(
        lambda: shared_integration_session.genau_enabled_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau enabled file to flip back on",
    )



def test_fun_time_genau_mode_file_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_genau_mode(True)
    shared_integration_session.wait_for_new_log("Entering Genau mode", timeout=12)

    shared_integration_session.write_genau_mode(False)
    shared_integration_session.wait_for_new_log("Leaving Genau mode", timeout=12)



def test_fun_time_genau_active_playback(shared_integration_session: FunTimeIntegrationSession):
    """Entering Genau mode with link enabled must unpause Genau and audio."""
    s = shared_integration_session
    assert s.genau_enabled_file.read_text(encoding="utf-8") == "1"

    s.write_genau_mode(True)
    s.wait_for_new_log("Entering Genau mode", timeout=12)

    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Genau paused file to flip off (active playback)",
    )
    s.wait_until(
        lambda: s.config.audio_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Audio paused file to flip off (audio companion active)",
    )

    s.write_genau_mode(False)
    s.wait_for_new_log("Leaving Genau mode", timeout=12)

    s.wait_until(
        lambda: s.config.genau_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Genau paused file to flip back on after leaving mode",
    )
    s.wait_until(
        lambda: s.config.audio_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Audio paused file to flip back on after leaving mode",
    )



def test_fun_time_primary_vlc_not_topmost_in_genau_mode(shared_integration_session: FunTimeIntegrationSession):
    """Primary VLC must leave the TOPMOST z-band while Genau mode is
    active so VLC video transitions cannot bring it above Genau."""
    s = shared_integration_session
    primary_pid = s.read_child_pids()["primary_pid"]
    hwnd = find_window_by_pid(primary_pid)
    assert hwnd, f"Primary VLC window not found for pid {primary_pid}"

    assert is_window_topmost(hwnd), "Primary VLC should be TOPMOST before robot hand mode"

    s.write_genau_mode(True)
    s.wait_for_new_log("Entering Genau mode", timeout=12)

    s.wait_until(
        lambda: not is_window_topmost(find_window_by_pid(primary_pid)),
        timeout=12,
        description="Primary VLC to lose TOPMOST in robot hand mode",
    )

    s.write_genau_mode(False)
    s.wait_for_new_log("Leaving Genau mode", timeout=12)

    s.wait_until(
        lambda: is_window_topmost(find_window_by_pid(primary_pid)),
        timeout=12,
        description="Primary VLC to regain TOPMOST after leaving robot hand mode",
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
    shared_integration_session.write_genau_mode(True)
    shared_integration_session.wait_for_new_log("Entering Genau mode", timeout=12)

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

    shared_integration_session.write_genau_mode(False)
    shared_integration_session.wait_for_new_log("Leaving Genau mode", timeout=12)



def test_fun_time_omnipause_does_not_kill_genau(shared_integration_session: FunTimeIntegrationSession):
    """Regression: omnipause must pause Genau, not close it.

    The old AHK HandleOmniPauseToggle never removed Genau's topmost
    flag.  When omnipause was ported to Python, an explicit
    set_topmost(Genau, False) was added by mistake, causing the
    window to fall behind other windows (appearing "closed").  Verify the
    Genau process survives an omnipause round-trip while in robot
    hand mode.
    """
    s = shared_integration_session
    rh_pid = s.read_genau_pid()
    assert _is_pid_alive(rh_pid), "Genau should be alive before test"

    s.write_genau_mode(True)
    s.wait_for_new_log("Entering Genau mode", timeout=12)

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

    s.write_genau_mode(False)
    s.wait_for_new_log("Leaving Genau mode", timeout=12)



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
    ensure_playback_state(port, password, should_play=True)
    s.wait_until(
        lambda: get_playback_state(port, password) == "playing",
        timeout=10,
        description="VLC to resume playing before nudge test",
    )

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
    s.write_dashboard_command("vlc_nudge_next")
    s.wait_until(
        lambda: (t := get_playback_time(port, password)) is not None and t >= before + 7,
        timeout=10,
        description="VLC playback time to advance ~10s after nudge forward",
    )
    after_forward = get_playback_time(port, password)
    assert after_forward is not None

    # Pause VLC to freeze the position before measuring — playback
    # advancing during the backward nudge can mask the seek.
    ensure_playback_state(port, password, should_play=False)
    after_forward = get_playback_time(port, password)
    assert after_forward is not None
    ensure_playback_state(port, password, should_play=True)

    # --- nudge backward ---
    s.write_dashboard_command("vlc_nudge_prev")
    s.wait_until(
        lambda: (t := get_playback_time(port, password)) is not None and t <= after_forward - 7,
        timeout=10,
        description="VLC playback time to retreat ~10s after nudge backward",
    )



def test_fun_time_startup_does_not_steal_foreground(isolated_integration_session: FunTimeIntegrationSession):
    """The second session startup must not steal the user's foreground window.

    This is the exact regression that five previous fix attempts failed to
    solve (2026-03-26).  The root cause was minimize_window using SW_MINIMIZE
    which activates the next z-order window on each call, creating a chain of
    focus transfers.  The fix uses SW_SHOWMINNOACTIVE instead.
    """
    s = isolated_integration_session
    fg_hwnd = get_foreground_window()
    child_pids = s.read_child_pids()
    for name, pid in child_pids.items():
        if not pid:
            continue
        child_hwnd = find_window_by_pid(pid)
        if child_hwnd:
            assert child_hwnd != fg_hwnd, (
                f"Foreground stolen by {name} (pid={pid}, hwnd={child_hwnd})"
            )



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


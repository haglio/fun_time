from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_AHK = PROJECT_ROOT / "controller.ahk"


def _controller_text() -> str:
    return CONTROLLER_AHK.read_text(encoding="utf-8")


def test_controller_uses_manifest_argument_instead_of_positional_protocol():
    text = _controller_text()

    assert "if (A_Args.Length < 1)" in text
    assert 'CONTROLLER_MANIFEST_PATH := A_Args[1]' in text
    assert 'RequireManifestValue("executables", "vlc_exe")' in text
    assert 'RequireManifestValue("commands", "robot_hand_enabled_file")' in text
    assert 'RequireManifestValue("controller", "primary_vlc_port")' in text
    assert 'RequireManifestValue("layout", "main_monitor")' in text
    assert 'RequireManifestValue("layout", "secondary_monitor")' in text
    assert "A_Args[29]" not in text


def test_controller_defines_robot_hand_status_indicator():
    text = _controller_text()

    assert "CreateFunTimeDashboard()" in text
    assert "UpdateFunTimeDashboard()" in text
    assert "TraySetIcon(ICON_PATH)" in text


def test_controller_uses_explicit_primary_vlc_playback_state_helpers():
    text = _controller_text()

    assert "EnsurePrimaryVlcPlayback(true)" in text
    assert "EnsurePrimaryVlcPlayback(false)" in text
    assert "SetRobotHandEnabled(true)" in text
    assert "broker_tray\\.ps1|launch_broker_tray\\.vbs" in text
    assert 'ControlSend("{Space}", , "ahk_pid " pid1)' not in text


def test_controller_waits_for_primary_vlc_before_launching_mfp_and_satellites():
    text = _controller_text()

    primary_launch = text.index("pid1 := RunVLC(")
    primary_wait = text.index("WaitForHttp(PRIMARY_VLC_PORT, 7000)", primary_launch)
    mfp_launch = text.index("pidM := RunApp(MFP_EXE, \"\")", primary_wait)
    satellite_launch = text.index("pid2 := RunVLC(", mfp_launch)

    assert primary_launch < primary_wait < mfp_launch < satellite_launch


def test_controller_reloads_f_mode_via_generated_playlist_files():
    text = _controller_text()

    assert 'WritePlaylistFile(playlistPath, paths)' in text
    assert 'SendVlcInputCommand(port, "in_play", playlistPath)' in text


def test_controller_dashboard_wires_existing_actions_into_click_targets():
    text = _controller_text()

    assert "ToggleRobotHandEnabled()" in text
    assert "QueueRobotHandOffsetQuarterCycle()" in text
    assert "HandlePrevAction()" in text
    assert "HandleNextAction()" in text
    assert "ToggleLock(2)" in text
    assert "ToggleLock(3)" in text
    assert "Discard(2)" in text
    assert "Discard(3)" in text


def test_controller_broker_probe_uses_q_wrapped_powershell_command():
    text = _controller_text()

    assert 'psCmd := "$targets = Get-CimInstance Win32_Process | Where-Object { "' in text
    assert 'cmd := "powershell.exe -NoProfile -WindowStyle Hidden -Command " . Q(psCmd)' in text


def test_controller_uses_main_monitor_for_landscape_and_mfp_layout():
    text = _controller_text()

    assert "GetLogicalMonitorRects(&mainRect, &secondaryRect)" in text
    assert 'MovePidWindow(pid3, landscapeX, mainT, landscapeW, mainH)' in text
    assert 'PositionMfpWindow(pidM)' in text


def test_controller_uses_secondary_monitor_for_portrait_primary_and_robot_hand():
    text = _controller_text()

    assert "GetLogicalMonitorRects(&mainRect, &secondaryRect)" in text
    assert 'MovePidWindow(pid2, secondaryL, secondaryT, secondaryW, portraitH)' in text
    assert 'MovePidWindow(pid1, secondaryL, secondaryT + portraitH, secondaryW, primaryH)' in text
    assert 'x := secondaryL' in text

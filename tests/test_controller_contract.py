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

    assert 'DASHBOARD_MODULE := RequireManifestValue("modules", "dashboard_module")' in text
    assert "pidD := LaunchDashboardApp()" in text
    assert "UpdateFunTimeDashboard()" in text
    assert "SetTimer(ProcessDashboardCommand, 150)" in text
    assert "GetDashboardStatusSnapshot(&brokerRunning, &mfpConnected)" in text
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

    assert 'WriteFModePlaylists(enabled)' in text
    assert 'BuildPrimaryPlaylistPaths(fMode)' not in text
    assert 'BuildSatellitePlaylistPaths(sourceSpec, fMode)' not in text
    assert 'ReplaceVlcPlaylistFromFile(PRIMARY_VLC_PORT, BuildPlaylistFilePath("primary_vlc_playlist"))' in text
    assert 'ReplaceVlcPlaylistFromFile(VLC2_PORT, BuildPlaylistFilePath("portrait_vlc_playlist"), "all")' in text
    assert 'ReplaceVlcPlaylistFromFile(VLC3_PORT, BuildPlaylistFilePath("landscape_vlc_playlist"), "all")' in text
    assert "ToFileUri(winPath) {" in text
    assert 'uri := ToFileUri(fullPath)' in text


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


def test_controller_broker_probe_uses_wmi_instead_of_hidden_powershell():
    text = _controller_text()
    fn_start = text.index("IsBrokerRunning() {")
    fn_end = text.index("\nIsProcessAlive(pid) {", fn_start)
    broker_block = text[fn_start:fn_end]

    assert 'wmi := ComObjGet("winmgmts:")' in broker_block
    assert 'query := "SELECT Name, CommandLine FROM Win32_Process WHERE "' in broker_block
    assert 'for process in wmi.ExecQuery(query) {' in broker_block
    assert 'InStr(cmdLine, "fun_time.broker_app")' in broker_block
    assert 'InStr(cmdLine, "broker_tray.ps1") || InStr(cmdLine, "launch_broker_tray.vbs")' in broker_block
    assert '"powershell.exe -NoProfile -WindowStyle Hidden -Command "' not in broker_block


def test_controller_reads_dashboard_bridge_paths_from_manifest():
    text = _controller_text()

    assert 'DASHBOARD_STATE_FILE := RequireManifestValue("commands", "dashboard_state_file")' in text
    assert 'DASHBOARD_CMD_FILE := RequireManifestValue("commands", "dashboard_cmd_file")' in text


def test_controller_reads_media_actions_module_from_manifest():
    text = _controller_text()

    assert 'MEDIA_ACTIONS_MODULE := RequireManifestValue("modules", "media_actions_module")' in text
    assert 'ROBOT_HAND_PY := RequireManifestValue("executables", "python_exe")' in text


def test_controller_reads_controller_modes_module_from_manifest():
    text = _controller_text()

    assert 'CONTROLLER_MODES_MODULE := RequireManifestValue("modules", "controller_modes_module")' in text


def test_controller_reads_controller_lock_module_from_manifest():
    text = _controller_text()

    assert 'CONTROLLER_LOCK_MODULE := RequireManifestValue("modules", "controller_lock_module")' in text


def test_controller_reads_controller_robot_hand_module_from_manifest():
    text = _controller_text()

    assert 'CONTROLLER_ROBOT_HAND_MODULE := RequireManifestValue("modules", "controller_robot_hand_module")' in text


def test_controller_reads_controller_omnipause_module_from_manifest():
    text = _controller_text()

    assert 'CONTROLLER_OMNIPAUSE_MODULE := RequireManifestValue("modules", "controller_omnipause_module")' in text


def test_controller_dashboard_update_does_not_shadow_robot_hand_enabled_helper():
    text = _controller_text()

    assert 'robotHandEnabledNow := RobotHandEnabled()' in text
    assert 'GetDashboardStatusSnapshot(&brokerRunningNow, &mfpConnectedNow)' in text
    assert 'primaryUsesRobotHand := robotHandMode && robotHandEnabledNow' in text
    assert 'WriteDashboardStateSnapshot(primaryPath, portraitPath, landscapePath, primaryUsesRobotHand, osr2Auto, robotHandEnabledNow, brokerRunningNow, mfpConnectedNow, x, y, w, h, locked2, locked3)' in text


def test_controller_only_activates_robot_hand_window_on_transition():
    text = _controller_text()

    assert "EnforceRobotHandOutputs(active, isTransition := false) {" in text
    assert 'if (isTransition) {' in text
    assert 'try WinActivate("Robot Hand")' in text


def test_controller_dashboard_refresh_repositions_only_when_rect_changes():
    text = _controller_text()

    update_start = text.index("UpdateFunTimeDashboard() {")
    snapshot_fn_start = text.index("\nWriteDashboardStateSnapshot(", update_start)
    update_block = text[update_start:snapshot_fn_start]

    assert 'funTimeDashboardGui.Show("NA x" . x . " y" . y . " w" . w . " h" . h)' not in update_block
    assert 'WinMove(x, y, w, h, "ahk_id " funTimeDashboardGui.Hwnd)' not in update_block
    assert "GetFunTimeDashboardRect(&x, &y, &w, &h)" in update_block
    assert 'WriteDashboardStateSnapshot(primaryPath, portraitPath, landscapePath, primaryUsesRobotHand, osr2Auto, robotHandEnabledNow, brokerRunningNow, mfpConnectedNow, x, y, w, h, locked2, locked3)' in update_block


def test_controller_processes_python_dashboard_commands_from_state_file():
    text = _controller_text()

    assert "ProcessDashboardCommand() {" in text
    assert 'action := Trim(FileRead(DASHBOARD_CMD_FILE, "UTF-8"))' in text
    assert 'FileDelete(DASHBOARD_CMD_FILE)' in text
    assert 'case "portrait_prev":' in text
    assert 'case "portrait_lock":' in text
    assert 'case "primary_prev":' in text
    assert 'case "quarter_button":' in text
    assert 'case "landscape_trash":' in text
    assert 'case "link_toggle":' in text


def test_controller_shutdown_closes_python_dashboard_process():
    text = _controller_text()

    assert "ShutdownAll() {" in text
    assert "SetTimer(ProcessDashboardCommand, 0)" in text
    assert "for pid in [pid1, pid2, pid3, pidM, pidD, pidR, pidA]" in text


def test_controller_delegates_lock_state_decisions_to_python_plan():
    text = _controller_text()

    assert 'RunControllerLockAction(action, which, locked, currentPath, planPath)' in text
    assert 'LoadLockActionPlan(path)' in text
    assert 'plan := RunControllerLockAction("toggle-lock", which, currentLocked, currentPath, planPath)' in text
    assert 'plan := RunControllerLockAction("discard", which, currentLocked, src, planPath)' in text
    assert 'plan := RunControllerLockAction("cancel-lock", which, currentLocked, "", planPath)' in text


def test_controller_keeps_robot_hand_sync_local_but_delegates_toggle_plan():
    text = _controller_text()

    assert 'RunControllerRobotHandAction(action, robotHandModeOn, enabled, omniPausedOn, planPath)' in text
    assert 'LoadRobotHandActionPlan(path)' in text
    assert 'plan := RunControllerRobotHandAction("sync-state", robotHandMode, RobotHandEnabled(), omniPaused, planPath)' not in text
    assert 'plan := RunControllerRobotHandAction("toggle-enabled", robotHandMode, RobotHandEnabled(), omniPaused, planPath)' in text
    assert 'modeState := EffectiveRobotHandModeState()' in text

    sync_start = text.index("SyncRobotHandState() {")
    toggle_start = text.index("ToggleRobotHandEnabled() {", sync_start)
    sync_block = text[sync_start:toggle_start]
    assert "UpdateFunTimeDashboard()" not in sync_block


def test_controller_delegates_omnipause_state_decisions_to_python_plan():
    text = _controller_text()

    assert 'RunControllerOmniPauseAction(action, omniPausedOn, robotHandModeOn, skipPrimaryResume, planPath)' in text
    assert 'LoadOmniPauseActionPlan(path)' in text
    assert 'plan := RunControllerOmniPauseAction("toggle", omniPaused, robotHandMode, false, planPath)' in text
    assert 'plan := RunControllerOmniPauseAction("leave", omniPaused, robotHandMode, skipPrimaryVlcPlaybackToggleOnResume, planPath)' in text


def test_controller_does_not_keep_temporary_focus_debug_monitoring():
    text = _controller_text()

    assert "DescribeWindow(" not in text
    assert "IsWindowTopMost(" not in text
    assert "LogFocusTrace(" not in text
    assert "LogFunTimeTopMostState(" not in text
    assert "StartFocusMonitor(" not in text
    assert "StopFocusMonitor(" not in text
    assert "MonitorFocusTick()" not in text


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

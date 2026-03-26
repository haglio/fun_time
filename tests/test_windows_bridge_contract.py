from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_SHIM_AHK = PROJECT_ROOT / "controller.ahk"
WINDOWS_BRIDGE_AHK = PROJECT_ROOT / "windows_bridge.ahk"
WINDOWS_BRIDGE_WINDOWS_AHK = PROJECT_ROOT / "windows_bridge_windows.ahk"
WINDOWS_BRIDGE_RUNTIME_AHK = PROJECT_ROOT / "windows_bridge_runtime.ahk"
WINDOWS_BRIDGE_ACTIONS_AHK = PROJECT_ROOT / "windows_bridge_actions.ahk"


def _windows_bridge_text() -> str:
    return (
        WINDOWS_BRIDGE_AHK.read_text(encoding="utf-8")
        + "\n"
        + WINDOWS_BRIDGE_WINDOWS_AHK.read_text(encoding="utf-8")
        + "\n"
        + WINDOWS_BRIDGE_RUNTIME_AHK.read_text(encoding="utf-8")
        + "\n"
        + WINDOWS_BRIDGE_ACTIONS_AHK.read_text(encoding="utf-8")
    )


def test_controller_shim_includes_windows_bridge():
    text = CONTROLLER_SHIM_AHK.read_text(encoding="utf-8")

    assert "#Include windows_bridge.ahk" in text


def test_windows_bridge_includes_windows_bridge_helpers():
    text = WINDOWS_BRIDGE_AHK.read_text(encoding="utf-8")

    assert "#Include windows_bridge_windows.ahk" in text
    assert "#Include windows_bridge_runtime.ahk" in text
    assert "#Include windows_bridge_actions.ahk" in text


def test_windows_bridge_runs_startup_before_hotkey_block():
    text = WINDOWS_BRIDGE_AHK.read_text(encoding="utf-8")

    startup_call = text.index("StartWindowsBridge()")
    suspend_exempt = text.index("#SuspendExempt true")

    assert startup_call < suspend_exempt


def test_controller_uses_manifest_argument_instead_of_positional_protocol():
    text = _windows_bridge_text()

    assert "if (A_Args.Length < 1)" in text
    assert 'WINDOWS_BRIDGE_MANIFEST_PATH := A_Args[1]' in text
    assert 'RequireManifestValue("controller", "vlc_pass")' in text
    assert 'RequireManifestValue("layout", "main_monitor")' in text
    assert 'RequireManifestValue("layout", "secondary_monitor")' in text
    assert "A_Args[29]" not in text
    # Globals only used by startup CLI are no longer read from manifest
    for removed in [
        "VLC_EXE", "MFP_EXE", "PRIMARY_VLC_SOURCES", "PORTRAIT_DIR", "LANDSCAPE_DIR",
        "PRIMARY_VLC_PORT", "VLC2_PORT", "VLC3_PORT",
        "ROBOT_HAND_MODULE", "DASHBOARD_MODULE",
        "ROBOT_HAND_CLIPS", "ROBOT_HAND_AUDIO_MODULE", "ROBOT_HAND_AUDIO",
        "ROBOT_HAND_CMD_FILE", "ROBOT_HAND_PAUSED_FILE",
        "BROKER_CMD_FILE", "AUDIO_CMD_FILE", "AUDIO_PAUSED_FILE",
    ]:
        assert f"{removed} :=" not in text, f"{removed} should be removed"


def test_controller_defines_robot_hand_status_indicator():
    text = _windows_bridge_text()

    assert 'DASHBOARD_MODULE' not in text
    assert 'WINDOWS_BRIDGE_RUNTIME_FLOW_MODULE' not in text
    assert 'args := "launch-ui-companions"' in text
    assert "UpdateFunTimeDashboard()" not in text
    assert "SetTimer(ProcessDashboardCommand, 150)" in text
    assert "TraySetIcon(ICON_PATH)" in text


def test_controller_no_longer_keeps_playback_helpers_in_ahk():
    text = _windows_bridge_text()

    assert "EnsurePrimaryVlcPlayback(" not in text
    assert "SendVlcCommand(" not in text
    assert 'ControlSend("{Space}", , "ahk_pid " pid1)' not in text
    assert 'args := "start-core-session"' in text


def test_controller_delegates_core_media_launch_and_waits_for_mfp_window_afterward():
    text = _windows_bridge_text()

    core_launch = text.index('args := "start-core-session"')
    core_result = text.index('coreResult := LoadStartupActionResult(coreResultPath)', core_launch)
    mfp_wait = text.index('WinWait("ahk_pid " pidM, , 15)', core_result)
    position_all = text.index("PositionAll(pid1, pid2, pid3, pidM)", mfp_wait)

    assert core_launch < core_result < mfp_wait < position_all


def test_controller_delegates_f_mode_execution_to_python_dispatch():
    text = _windows_bridge_text()

    assert 'DispatchBridgeCommand("fmode_toggle")' in text
    assert 'RunWindowsBridgeRuntimeFlowAction(' not in text
    assert 'BuildRuntimeFlowResultPath() {' not in text
    assert 'LoadWindowsBridgeRuntimeFlowResult(' not in text
    assert 'args := "toggle-fmode"' not in text
    assert 'BuildPrimaryPlaylistPaths(fMode)' not in text
    assert 'BuildSatellitePlaylistPaths(sourceSpec, fMode)' not in text
    assert 'WriteFModePlaylists(enabled)' not in text
    assert 'ReplaceVlcPlaylistFromFile(' not in text


def test_controller_dashboard_dispatches_all_commands_via_python():
    text = _windows_bridge_text()

    assert "DispatchBridgeCommand(action)" in text
    assert "ToggleRobotHandEnabled()" not in text
    assert "QueueRobotHandOffsetQuarterCycle()" not in text
    assert "HandlePrevAction()" not in text
    assert "HandleNextAction()" not in text
    assert "ToggleLock(2)" not in text
    assert "ToggleLock(3)" not in text
    assert "Discard(2)" not in text
    assert "Discard(3)" not in text


def test_controller_dashboard_no_longer_polls_broker_or_mfp_status_in_ahk():
    text = _windows_bridge_text()

    assert "IsBrokerRunning() {" not in text
    assert "IsProcessAlive(pid) {" not in text
    assert "GetDashboardStatusSnapshot(&brokerRunning, &mfpConnected)" not in text
    assert 'RequireManifestValue("commands", "broker_heartbeat_file")' not in text


def test_controller_reads_dashboard_bridge_paths_from_manifest():
    text = _windows_bridge_text()

    assert 'DASHBOARD_STATE_FILE := RequireManifestValue("commands", "dashboard_state_file")' in text
    assert 'DASHBOARD_CMD_FILE := RequireManifestValue("commands", "dashboard_cmd_file")' in text


def test_controller_no_longer_reads_media_actions_module_from_manifest():
    text = _windows_bridge_text()

    assert 'MEDIA_ACTIONS_MODULE := RequireManifestValue("modules", "media_actions_module")' not in text
    assert 'ROBOT_HAND_PY := RequireManifestValue("executables", "python_exe")' in text


def test_controller_no_longer_reads_legacy_runtime_modules_from_manifest():
    text = _windows_bridge_text()

    assert 'CONTROLLER_MODES_MODULE := RequireManifestValue("modules", "controller_modes_module")' not in text
    assert 'CONTROLLER_ROBOT_HAND_MODULE := RequireManifestValue("modules", "controller_robot_hand_module")' not in text
    assert 'CONTROLLER_OMNIPAUSE_MODULE := RequireManifestValue("modules", "controller_omnipause_module")' not in text


def test_controller_no_longer_reads_lock_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_LOCK_MODULE' not in text


def test_controller_reads_windows_bridge_window_layout_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_WINDOW_LAYOUT_MODULE := RequireManifestValue("modules", "windows_bridge_window_layout_module")' in text


def test_controller_no_longer_reads_vlc_actions_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_VLC_ACTIONS_MODULE' not in text
    assert 'RunWindowsBridgeVlcAction(' not in text


def test_controller_reads_windows_bridge_random_favs_browser_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_RANDOM_FAVS_BROWSER_MODULE := RequireManifestValue("modules", "windows_bridge_random_favs_browser_module")' in text
    assert 'RANDOM_FAVS_BROWSER_ENABLED := RequireManifestValue("random_favs_browser", "enabled") = "1"' in text


def test_controller_reads_windows_bridge_startup_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_STARTUP_MODULE := RequireManifestValue("modules", "windows_bridge_startup_module")' in text
    assert 'RunWindowsBridgeStartupAction(args) {' in text
    assert 'cmd := Q(ROBOT_HAND_PY) . " -m " . WINDOWS_BRIDGE_STARTUP_MODULE . " " . args' in text
    assert 'LoadStartupActionResult(path) {' in text
    assert 'BuildStartupResultPath() {' in text


def test_controller_no_longer_reads_dashboard_bridge_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_DASHBOARD_BRIDGE_MODULE' not in text
    assert 'RunWindowsBridgeDashboardBridgeAction(' not in text


def test_controller_dashboard_update_is_handled_by_dispatch_channel():
    text = _windows_bridge_text()

    assert 'UpdateFunTimeDashboard()' not in text
    assert 'RunWindowsBridgeDashboardBridgeAction(' not in text
    assert '. " --dashboard-state-file " . Q(DASHBOARD_STATE_FILE)' in text
    assert '. " --dashboard-enabled " . (DASHBOARD_ENABLED ? "1" : "0")' in text
    assert '. " --mfp-alive " . (mfpAlive ? "1" : "0")' in text


def test_controller_dispatch_executes_window_ops_from_python_result():
    text = _windows_bridge_text()

    assert "DispatchBridgeCommand(cmd) {" in text
    assert "Critical" in text[text.index("DispatchBridgeCommand(cmd) {"):text.index("DispatchBridgeCommand(cmd) {") + 100]
    assert 'case "set_topmost":' in text
    assert 'case "activate":' in text
    assert 'case "suspend_hotkeys":' in text
    assert 'case "unsuspend_hotkeys":' in text
    assert 'case "send_key":' in text
    assert "ApplyRobotHandPlanWindowState(" not in text


def test_controller_dashboard_no_longer_repositions_or_refreshes_on_a_timer():
    text = _windows_bridge_text()

    assert "UpdateFunTimeDashboard() {" not in text
    assert "GetFunTimeDashboardRect(&x, &y, &w, &h)" not in text
    assert 'SetTimer(UpdateFunTimeDashboard, 500)' not in text


def test_controller_dashboard_snapshot_writer_is_delegated_to_python():
    text = _windows_bridge_text()

    assert "WriteDashboardStateSnapshot(" not in text
    assert 'RunWindowsBridgeDashboardBridgeAction(' not in text
    assert '. " --dashboard-state-file " . Q(DASHBOARD_STATE_FILE)' in text


def test_controller_dashboard_export_is_raw_runtime_state_only():
    text = _windows_bridge_text()

    assert "ClipLabelFromPath(" not in text
    assert "PrimaryPanelShouldHighlight(" not in text
    assert "SatellitePanelShouldHighlight(" not in text
    assert "BuildMirroredFunscriptPath(" not in text
    assert "HasMatchingFunscript(" not in text
    assert "ReadFavsContent(" not in text
    assert "IsFavoritePath(" not in text


def test_controller_restores_random_favs_browser_launch_spec_helpers():
    text = _windows_bridge_text()

    assert 'try FileGetShortcut(RANDOM_FAVS_BROWSER_SHORTCUT_PATH, &target, &workDir, &args, &description, &iconPath, &iconNum, &runState)' in text
    assert 'LaunchRandomFavsBrowserViaPython(RANDOM_FAVS_BROWSER_MANIFEST_FILE, target, workDir, args)' in text
    assert 'Base64EncodeUtf8(s) {' in text
    assert 'LoadRandomFavsBrowserLaunchPlan(path) {' not in text
    assert 'if (!RANDOM_FAVS_BROWSER_ENABLED)' in text


def test_controller_delegates_startup_to_python_via_manifest():
    text = _windows_bridge_text()

    assert 'args := "start-core-session"' in text
    assert 'args := "launch-ui-companions"' in text
    assert '. " --manifest " . Q(WINDOWS_BRIDGE_MANIFEST_PATH)' in text
    assert '. " --result-file " . Q(coreResultPath)' in text
    assert '. " --result-file " . Q(uiResultPath)' in text

    # Extract the start-core-session and launch-ui-companions arg blocks
    core_start = text.index('args := "start-core-session"')
    core_end = text.index("RunWindowsBridgeStartupAction(args)", core_start)
    core_block = text[core_start:core_end]

    ui_start = text.index('args := "launch-ui-companions"')
    ui_end = text.index("RunWindowsBridgeStartupAction(args)", ui_start)
    ui_block = text[ui_start:ui_end]

    # start-core-session only needs --manifest and --result-file
    assert "--config" not in core_block
    assert "--random-favs-browser-manifest-file" not in core_block
    assert "--enabled-file" not in core_block
    assert "--vlc-exe" not in core_block
    assert "--password" not in core_block

    # launch-ui-companions only needs --manifest + runtime-only values
    assert "--python-exe" not in ui_block
    assert "--dashboard-module" not in ui_block
    assert "--robot-hand-module" not in ui_block
    assert "--audio-module" not in ui_block
    assert "--clips-folder" not in ui_block
    assert "--audio-folder" not in ui_block
    assert "--config" not in ui_block
    assert "--dashboard-enabled" not in ui_block

    assert "RestartBroker() {" not in text


def test_controller_launches_robot_hand_and_audio_via_startup_helper():
    text = _windows_bridge_text()

    assert 'uiResultPath := BuildStartupResultPath()' in text
    assert 'RunWindowsBridgeStartupAction(args) != 0' in text
    assert 'startupResult := LoadStartupActionResult(uiResultPath)' in text
    assert 'pidD := startupResult["dashboard_pid"]' in text
    assert 'pidR := startupResult["robot_hand_pid"]' in text
    assert 'pidA := startupResult["audio_pid"]' in text
    assert 'LaunchDashboardApp(' not in text
    assert 'pidR := RunApp(ROBOT_HAND_PY' not in text
    assert 'pidA := RunApp(ROBOT_HAND_PY' not in text


def test_controller_launches_primary_mfp_and_satellites_via_startup_helper():
    text = _windows_bridge_text()

    assert 'coreResultPath := BuildStartupResultPath()' in text
    assert 'args := "start-core-session"' in text
    assert 'coreResult := LoadStartupActionResult(coreResultPath)' in text
    assert 'pid1 := coreResult["primary_pid"]' in text
    assert 'pidM := coreResult["mfp_pid"]' in text
    assert 'pid2 := coreResult["portrait_pid"]' in text
    assert 'pid3 := coreResult["landscape_pid"]' in text
    assert 'pid1 := RunVLC(' not in text
    assert 'pidM := RunApp(MFP_EXE, "")' not in text
    assert text.count('pid2 := RunVLC(') == 0
    assert text.count('pid3 := RunVLC(') == 0
    # Dead function definitions removed
    assert 'RunApp(' not in text
    assert 'RunVLC(' not in text
    assert 'RunDetached(' not in text
    assert 'WriteRawStateFile(' not in text


def test_controller_launches_dashboard_via_startup_helper_after_window_layout_is_known():
    text = _windows_bridge_text()

    get_layout = text.index('GetCurrentWindowLayout(&plan)')
    robot_rect = text.index('GetRobotHandRect(&rx, &ry, &rw, &rh)', get_layout)
    ui_launch = text.index('args := "launch-ui-companions"', robot_rect)
    ui_result = text.index('pidD := startupResult["dashboard_pid"]', ui_launch)

    assert get_layout < robot_rect < ui_launch < ui_result
    assert '. " --manifest " . Q(WINDOWS_BRIDGE_MANIFEST_PATH)' in text
    assert '. " --dashboard-x " . dashboardX' in text
    assert '. " --dashboard-y " . dashboardY' in text
    assert '. " --dashboard-width " . dashboardW' in text
    assert '. " --dashboard-height " . dashboardH' in text
    assert '. " --robot-x " . rx' in text
    assert '. " --robot-y " . ry' in text
    assert '. " --robot-width " . rw' in text
    assert '. " --robot-height " . rh' in text


def test_controller_processes_python_dashboard_commands_via_dispatch():
    text = _windows_bridge_text()

    assert "ProcessDashboardCommand() {" in text
    assert 'action := Trim(FileRead(DASHBOARD_CMD_FILE, "UTF-8"))' in text
    assert 'FileDelete(DASHBOARD_CMD_FILE)' in text
    assert "DispatchBridgeCommand(action)" in text
    assert 'case "portrait_prev":' not in text


def test_controller_shutdown_closes_python_dashboard_process():
    text = _windows_bridge_text()

    assert "ShutdownAll() {" in text
    assert "SetTimer(ProcessDashboardCommand, 0)" in text
    assert "SetTimer(UpdateFunTimeDashboard, 0)" not in text
    assert "for pid in [pid1, pid2, pid3, pidM, pidD, pidR, pidA]" in text


def test_controller_no_longer_keeps_lock_helpers_in_ahk():
    text = _windows_bridge_text()

    assert 'RunWindowsBridgeLockAction(' not in text
    assert 'LoadLockActionPlan(' not in text
    assert 'BuildLockPlanPath(' not in text
    assert 'DispatchBridgeCommand("portrait_lock")' in text
    assert 'DispatchBridgeCommand("landscape_lock")' in text


def test_controller_delegates_robot_hand_sync_to_python_dispatch():
    text = _windows_bridge_text()

    sync_start = text.index("SyncRobotHandState() {")
    sync_end = text.index("\n}", sync_start) + 2
    sync_block = text[sync_start:sync_end]

    assert 'DispatchBridgeCommand("sync_robot_hand")' in sync_block
    assert 'UpdateFunTimeDashboard()' not in sync_block
    assert 'args := "sync-robot-hand"' not in text
    assert 'args := "toggle-robot-hand-enabled"' not in text
    assert 'ToggleRobotHandEnabled() {' not in text
    assert 'EnforceRobotHandOutputs(' not in text
    assert 'SetRobotHandPaused(' not in text
    assert 'SetRobotHandAudioPaused(' not in text


def test_controller_uses_dispatch_tracked_robot_hand_mode_not_file_readers():
    text = _windows_bridge_text()

    assert 'EffectiveRobotHandModeState() {' not in text
    assert 'RobotHandModeState() {' not in text
    assert 'RobotHandEnabled() {' not in text
    assert 'ROBOT_HAND_MODE_FILE' not in text
    assert 'ROBOT_HAND_ENABLED_FILE' not in text
    assert 'if (robotHandMode)' in text


def test_controller_delegates_omnipause_state_decisions_to_python():
    text = _windows_bridge_text()

    assert 'HandleOmniPauseToggle()' in text
    assert 'DispatchBridgeCommand("omnipause_toggle")' in text
    assert 'DispatchBridgeCommand("enter_omnipause")' in text
    assert 'DispatchBridgeCommand("leave_omnipause_skip_primary")' in text
    assert 'RunWindowsBridgeRuntimeFlowAction(' not in text
    assert 'LoadWindowsBridgeRuntimeFlowResult(' not in text
    assert 'args := "build-omnipause-toggle"' not in text
    assert '\nOmniPauseToggle() {' not in text
    assert 'args := "apply-enter-omnipause"' not in text
    assert 'args := "apply-leave-omnipause"' not in text
    assert 'EnterOmniPause() {' not in text
    assert 'LeaveOmniPause(' not in text


def test_controller_does_not_keep_temporary_focus_debug_monitoring():
    text = _windows_bridge_text()

    assert "DescribeWindow(" not in text
    assert "IsWindowTopMost(" not in text
    assert "LogFocusTrace(" not in text
    assert "LogFunTimeTopMostState(" not in text
    assert "StartFocusMonitor(" not in text
    assert "StopFocusMonitor(" not in text
    assert "MonitorFocusTick()" not in text


def test_controller_uses_main_monitor_for_landscape_and_mfp_layout():
    text = _windows_bridge_text()

    assert "GetLogicalMonitorRects(&mainRect, &secondaryRect)" in text
    assert 'plan := RunWindowsBridgeWindowLayout(mainRect, secondaryRect, mfpW, mfpH, planPath)' in text
    assert 'MovePidWindow(pid3, plan["landscape"]["x"], plan["landscape"]["y"], plan["landscape"]["w"], plan["landscape"]["h"])' in text
    assert 'PositionMfpWindow(pidM)' in text


def test_controller_uses_secondary_monitor_for_portrait_primary_and_robot_hand():
    text = _windows_bridge_text()

    assert "GetLogicalMonitorRects(&mainRect, &secondaryRect)" in text
    assert 'MovePidWindow(pid2, plan["portrait"]["x"], plan["portrait"]["y"], plan["portrait"]["w"], plan["portrait"]["h"])' in text
    assert 'MovePidWindow(pid1, plan["primary"]["x"], plan["primary"]["y"], plan["primary"]["w"], plan["primary"]["h"])' in text
    assert 'x := plan["robot_hand"]["x"]' in text
    assert 'y := plan["robot_hand"]["y"]' in text
    assert 'w := plan["robot_hand"]["w"]' in text
    assert 'h := plan["robot_hand"]["h"]' in text


def test_controller_delegates_window_layout_planning_to_python_plan():
    text = _windows_bridge_text()

    assert 'BuildWindowLayoutPlanPath() {' in text
    assert 'static counter := 0' in text
    assert 'counter += 1' in text
    assert 'return STATE_DIR . "\\window_layout_plan_" . A_TickCount . "_" . counter . ".ini"' in text
    assert 'RunWindowsBridgeWindowLayout(mainRect, secondaryRect, mfpW, mfpH, planPath) {' in text
    assert 'LoadWindowLayoutPlan(path) {' in text
    assert 'cmd := Q(ROBOT_HAND_PY) . " -m " . WINDOWS_BRIDGE_WINDOW_LAYOUT_MODULE . " " . args' in text
    assert 'plan["dashboard"] := ' not in text
    assert 'for section in ["portrait", "primary", "landscape", "mfp", "dashboard", "random_favs_browser", "robot_hand"] {' in text


def test_controller_no_longer_keeps_vlc_action_helpers_in_ahk():
    text = _windows_bridge_text()

    assert 'RunWindowsBridgeVlcAction(' not in text
    assert 'WINDOWS_BRIDGE_VLC_ACTIONS_MODULE' not in text
    assert 'args := "send-command"' not in text
    assert 'args := "ensure-playback-state"' not in text
    assert 'args := "set-repeat-mode"' not in text
    assert "SendVlcInputCommand(" not in text
    assert "GetRepeatMode(" not in text


def test_controller_no_longer_keeps_vlc_query_helpers_in_ahk():
    text = _windows_bridge_text()

    assert 'BuildVlcQueryOutputPath(' not in text
    assert 'args := "wait-for-http"' not in text
    assert 'args := "current-file-path"' not in text
    assert "DecodeFileUri(" not in text
    assert "UrlDecode(" not in text


def test_controller_no_longer_keeps_any_vlc_command_sender_in_ahk():
    text = _windows_bridge_text()

    assert "VlcHttpCmd(port, cmd) {" not in text
    assert "SendVlcCommand(" not in text


def test_controller_uses_hidden_wait_to_suppress_loading_cursor():
    text = _windows_bridge_text()

    assert "RunHiddenWait(cmdLine, workDir" in text
    assert "STARTF_FORCEOFFFEEDBACK" in text
    assert 'RunWait(' not in text



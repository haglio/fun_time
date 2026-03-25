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
    assert 'RequireManifestValue("executables", "vlc_exe")' in text
    assert 'RequireManifestValue("commands", "robot_hand_enabled_file")' in text
    assert 'RequireManifestValue("controller", "primary_vlc_port")' in text
    assert 'RequireManifestValue("layout", "main_monitor")' in text
    assert 'RequireManifestValue("layout", "secondary_monitor")' in text
    assert "A_Args[29]" not in text


def test_controller_defines_robot_hand_status_indicator():
    text = _windows_bridge_text()

    assert 'DASHBOARD_MODULE := RequireManifestValue("modules", "dashboard_module")' in text
    assert 'WINDOWS_BRIDGE_RUNTIME_FLOW_MODULE := RequireManifestValue("modules", "windows_bridge_runtime_flow_module")' in text
    assert 'args := "launch-ui-companions"' in text
    assert "UpdateFunTimeDashboard()" in text
    assert "SetTimer(ProcessDashboardCommand, 150)" in text
    assert "TraySetIcon(ICON_PATH)" in text


def test_controller_uses_explicit_primary_vlc_playback_state_helpers():
    text = _windows_bridge_text()

    assert "EnsurePrimaryVlcPlayback(shouldPlay) {" in text
    assert 'args := "start-core-session"' in text
    assert 'ControlSend("{Space}", , "ahk_pid " pid1)' not in text


def test_controller_delegates_core_media_launch_and_waits_for_mfp_window_afterward():
    text = _windows_bridge_text()

    core_launch = text.index('args := "start-core-session"')
    core_result = text.index('coreResult := LoadStartupActionResult(coreResultPath)', core_launch)
    mfp_wait = text.index('WinWait("ahk_pid " pidM, , 15)', core_result)
    position_all = text.index("PositionAll(pid1, pid2, pid3, pidM)", mfp_wait)

    assert core_launch < core_result < mfp_wait < position_all


def test_controller_delegates_f_mode_execution_to_python_runtime_flow():
    text = _windows_bridge_text()

    assert 'RunWindowsBridgeRuntimeFlowAction(args)' in text
    assert 'BuildRuntimeFlowResultPath() {' in text
    assert 'LoadWindowsBridgeRuntimeFlowResult(path) {' in text
    assert 'args := "toggle-fmode"' in text
    assert 'BuildPrimaryPlaylistPaths(fMode)' not in text
    assert 'BuildSatellitePlaylistPaths(sourceSpec, fMode)' not in text
    assert 'WriteFModePlaylists(enabled)' not in text
    assert 'ReplaceVlcPlaylistFromFile(' not in text


def test_controller_dashboard_wires_existing_actions_into_click_targets():
    text = _windows_bridge_text()

    assert "ToggleRobotHandEnabled()" in text
    assert "QueueRobotHandOffsetQuarterCycle()" in text
    assert "HandlePrevAction()" in text
    assert "HandleNextAction()" in text
    assert "ToggleLock(2)" in text
    assert "ToggleLock(3)" in text
    assert "Discard(2)" in text
    assert "Discard(3)" in text


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


def test_controller_reads_windows_bridge_lock_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_LOCK_MODULE := RequireManifestValue("modules", "windows_bridge_lock_module")' in text


def test_controller_reads_windows_bridge_window_layout_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_WINDOW_LAYOUT_MODULE := RequireManifestValue("modules", "windows_bridge_window_layout_module")' in text


def test_controller_reads_windows_bridge_vlc_actions_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_VLC_ACTIONS_MODULE := RequireManifestValue("modules", "windows_bridge_vlc_actions_module")' in text


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


def test_controller_reads_windows_bridge_dashboard_bridge_module_from_manifest():
    text = _windows_bridge_text()

    assert 'WINDOWS_BRIDGE_DASHBOARD_BRIDGE_MODULE := RequireManifestValue("modules", "windows_bridge_dashboard_bridge_module")' in text
    assert 'RunWindowsBridgeDashboardBridgeAction(args) {' in text
    assert 'cmd := Q(ROBOT_HAND_PY) . " -m " . WINDOWS_BRIDGE_DASHBOARD_BRIDGE_MODULE . " " . args' in text


def test_controller_dashboard_update_does_not_shadow_robot_hand_enabled_helper():
    text = _windows_bridge_text()

    assert 'robotHandEnabledNow := RobotHandEnabled()' in text
    assert 'primaryUsesRobotHand := robotHandMode && robotHandEnabledNow' in text
    assert 'mfpAlive := pidM && ProcessExist(pidM)' in text
    assert 'RunWindowsBridgeDashboardBridgeAction(args)' in text


def test_controller_only_activates_robot_hand_window_on_transition():
    text = _windows_bridge_text()

    assert "ApplyRobotHandPlanWindowState(plan) {" in text
    assert 'if (isTransition) {' in text
    assert 'try WinActivate("Robot Hand")' in text


def test_controller_dashboard_no_longer_repositions_or_refreshes_on_a_timer():
    text = _windows_bridge_text()

    update_start = text.index("UpdateFunTimeDashboard() {")
    snapshot_fn_start = text.index("\nEffectiveRobotHandModeState(", update_start)
    update_block = text[update_start:snapshot_fn_start]

    assert "GetFunTimeDashboardRect(&x, &y, &w, &h)" not in update_block
    assert 'SetTimer(UpdateFunTimeDashboard, 500)' not in text
    assert 'args := "--output-file " . Q(DASHBOARD_STATE_FILE)' in update_block
    assert 'RunWindowsBridgeDashboardBridgeAction(args)' in update_block


def test_controller_dashboard_snapshot_writer_is_delegated_to_python():
    text = _windows_bridge_text()

    assert "WriteDashboardStateSnapshot(" not in text
    assert 'RunWindowsBridgeDashboardBridgeAction(args)' in text


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


def test_controller_delegates_startup_broker_restart_and_browser_manifest_prep_to_python():
    text = _windows_bridge_text()

    assert 'args := "start-core-session"' in text
    assert 'args := "launch-ui-companions"' in text
    assert '. " --config " . Q(CONFIG_PATH)' in text
    assert '. " --random-favs-browser-manifest-file " . Q(RANDOM_FAVS_BROWSER_MANIFEST_FILE)' in text
    assert '. " --enabled-file " . Q(ROBOT_HAND_ENABLED_FILE)' in text
    assert '. " --paused-file " . Q(ROBOT_HAND_PAUSED_FILE)' in text
    assert '. " --audio-paused-file " . Q(AUDIO_PAUSED_FILE)' in text
    assert '. " --result-file " . Q(uiResultPath)' in text
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


def test_controller_launches_dashboard_via_startup_helper_after_window_layout_is_known():
    text = _windows_bridge_text()

    get_layout = text.index('GetCurrentWindowLayout(&plan)')
    robot_rect = text.index('GetRobotHandRect(&rx, &ry, &rw, &rh)', get_layout)
    ui_launch = text.index('args := "launch-ui-companions"', robot_rect)
    ui_result = text.index('pidD := startupResult["dashboard_pid"]', ui_launch)

    assert get_layout < robot_rect < ui_launch < ui_result
    assert '. " --dashboard-module " . Q(DASHBOARD_MODULE)' in text
    assert '. " --windows-bridge-manifest-path " . Q(WINDOWS_BRIDGE_MANIFEST_PATH)' in text
    assert '. " --dashboard-x " . dashboardX' in text
    assert '. " --dashboard-y " . dashboardY' in text
    assert '. " --dashboard-width " . dashboardW' in text
    assert '. " --dashboard-height " . dashboardH' in text
    assert '. " --robot-x " . rx' in text
    assert '. " --robot-y " . ry' in text
    assert '. " --robot-width " . rw' in text
    assert '. " --robot-height " . rh' in text


def test_controller_processes_python_dashboard_commands_from_state_file():
    text = _windows_bridge_text()

    assert "ProcessDashboardCommand() {" in text
    assert 'action := Trim(FileRead(DASHBOARD_CMD_FILE, "UTF-8"))' in text
    assert 'FileDelete(DASHBOARD_CMD_FILE)' in text
    assert 'case "portrait_prev":' in text
    assert 'case "portrait_lock":' in text
    assert 'case "primary_prev":' in text
    assert 'case "quarter_button":' in text
    assert 'case "landscape_trash":' in text
    assert 'case "link_toggle":' in text
    assert 'case "omnipause_toggle":' in text
    assert 'case "fmode_toggle":' in text
    assert 'case "robot_toggle":' in text


def test_controller_shutdown_closes_python_dashboard_process():
    text = _windows_bridge_text()

    assert "ShutdownAll() {" in text
    assert "SetTimer(ProcessDashboardCommand, 0)" in text
    assert "SetTimer(UpdateFunTimeDashboard, 0)" not in text
    assert "for pid in [pid1, pid2, pid3, pidM, pidD, pidR, pidA]" in text


def test_controller_delegates_lock_execution_to_python_app():
    text = _windows_bridge_text()

    assert 'RunWindowsBridgeLockAction(action, which, locked, currentPath, planPath, extraArgs := "")' in text
    assert 'LoadLockActionPlan(path)' in text
    assert 'plan := RunWindowsBridgeLockAction("apply-toggle-lock", which, currentLocked, currentPath, planPath' in text
    assert 'plan := RunWindowsBridgeLockAction("apply-discard", which, currentLocked, src, planPath' in text
    assert 'plan := RunWindowsBridgeLockAction("apply-cancel-lock", which, currentLocked, "", planPath' in text
    assert 'SetRepeatMode(port, plan["repeat_mode"])' not in text
    assert 'EnsureInFavs(currentPath)' not in text
    assert 'RemoveFromFavs(src)' not in text
    assert 'MoveToWeird(src)' not in text


def test_controller_delegates_robot_hand_runtime_flow_to_python_helper():
    text = _windows_bridge_text()

    assert 'RunWindowsBridgeRuntimeFlowAction(args)' in text
    assert 'LoadWindowsBridgeRuntimeFlowResult(path)' in text
    assert 'args := "sync-robot-hand"' in text
    assert 'args := "toggle-robot-hand-enabled"' in text
    assert '--mode-state-file ' in text
    assert '--enabled-file ' in text
    assert '--paused-file ' in text
    assert '--audio-paused-file ' in text
    assert 'modeState := EffectiveRobotHandModeState()' not in text
    assert 'EnforceRobotHandOutputs(active, isTransition := false) {' not in text
    assert 'SetRobotHandPaused(' not in text
    assert 'SetRobotHandAudioPaused(' not in text

    sync_start = text.index("SyncRobotHandState() {")
    toggle_start = text.index("ToggleRobotHandEnabled() {", sync_start)
    sync_block = text[sync_start:toggle_start]
    assert 'UpdateFunTimeDashboard()' in sync_block


def test_controller_delegates_omnipause_state_decisions_to_python_plan():
    text = _windows_bridge_text()

    assert 'RunWindowsBridgeRuntimeFlowAction(args)' in text
    assert 'LoadWindowsBridgeRuntimeFlowResult(path)' in text
    assert 'args := "build-omnipause-toggle"' in text
    assert 'args := "apply-enter-omnipause"' in text
    assert 'args := "apply-leave-omnipause"' in text
    assert '--robot-hand-paused-file ' in text
    assert '--audio-paused-file ' in text


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


def test_controller_delegates_write_side_vlc_actions_to_python():
    text = _windows_bridge_text()

    assert 'RunWindowsBridgeVlcAction(args) {' in text
    assert 'cmd := Q(ROBOT_HAND_PY) . " -m " . WINDOWS_BRIDGE_VLC_ACTIONS_MODULE . " " . args' in text
    assert 'args := "send-command"' in text
    assert 'args := "ensure-playback-state"' in text
    assert 'args := "set-repeat-mode"' in text
    assert 'args := "replace-playlist"' not in text
    assert "SendVlcInputCommand(" not in text
    assert "GetRepeatMode(" not in text


def test_controller_delegates_current_file_path_and_http_wait_to_python():
    text = _windows_bridge_text()

    assert 'BuildVlcQueryOutputPath(prefix) {' in text
    assert 'args := "wait-for-http"' in text
    assert 'args := "current-file-path"' in text
    assert 'return Trim(FileRead(outputPath, "UTF-8"))' in text
    assert "DecodeFileUri(" not in text
    assert "UrlDecode(" not in text


def test_controller_no_longer_keeps_raw_vlc_command_sender_in_ahk():
    text = _windows_bridge_text()

    assert "VlcHttpCmd(port, cmd) {" not in text
    assert 'SendVlcCommand(port, cmd) {' in text



from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_AHK = PROJECT_ROOT / "controller.ahk"
CONTROLLER_WINDOWS_AHK = PROJECT_ROOT / "controller_windows.ahk"
CONTROLLER_RUNTIME_AHK = PROJECT_ROOT / "controller_runtime.ahk"
CONTROLLER_ACTIONS_AHK = PROJECT_ROOT / "controller_actions.ahk"


def _controller_text() -> str:
    return (
        CONTROLLER_AHK.read_text(encoding="utf-8")
        + "\n"
        + CONTROLLER_WINDOWS_AHK.read_text(encoding="utf-8")
        + "\n"
        + CONTROLLER_RUNTIME_AHK.read_text(encoding="utf-8")
        + "\n"
        + CONTROLLER_ACTIONS_AHK.read_text(encoding="utf-8")
    )


def test_controller_includes_windows_bridge_helpers():
    text = CONTROLLER_AHK.read_text(encoding="utf-8")

    assert "#Include controller_windows.ahk" in text
    assert "#Include controller_runtime.ahk" in text
    assert "#Include controller_actions.ahk" in text


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
    assert "TraySetIcon(ICON_PATH)" in text


def test_controller_uses_explicit_primary_vlc_playback_state_helpers():
    text = _controller_text()

    assert "EnsurePrimaryVlcPlayback(true)" in text
    assert "EnsurePrimaryVlcPlayback(false)" in text
    assert "SetRobotHandEnabled(true)" in text
    assert 'args := "restart-broker --project-dir " . Q(PROJECT_DIR)' in text
    assert 'ControlSend("{Space}", , "ahk_pid " pid1)' not in text


def test_controller_waits_for_primary_vlc_before_launching_mfp_and_satellites():
    text = _controller_text()

    primary_launch = text.index("pid1 := RunVLC(")
    primary_wait = text.index("WaitForHttp(PRIMARY_VLC_PORT, 7000)", primary_launch)
    mfp_launch = text.index("pidM := RunApp(MFP_EXE, \"\")", primary_wait)
    satellite_launch = text.index("pid2 := RunVLC(", mfp_launch)

    assert primary_launch < primary_wait < mfp_launch < satellite_launch


def test_controller_delegates_f_mode_execution_to_python_modes_app():
    text = _controller_text()

    assert 'args := "apply-fmode"' in text
    assert 'BuildModesResultPath() {' in text
    assert 'LoadModesActionResult(path) {' in text
    assert 'BuildPrimaryPlaylistPaths(fMode)' not in text
    assert 'BuildSatellitePlaylistPaths(sourceSpec, fMode)' not in text
    assert 'WriteFModePlaylists(enabled)' not in text
    assert 'ReplaceVlcPlaylistFromFile(' not in text


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


def test_controller_dashboard_no_longer_polls_broker_or_mfp_status_in_ahk():
    text = _controller_text()

    assert "IsBrokerRunning() {" not in text
    assert "IsProcessAlive(pid) {" not in text
    assert "GetDashboardStatusSnapshot(&brokerRunning, &mfpConnected)" not in text
    assert 'RequireManifestValue("commands", "broker_heartbeat_file")' not in text


def test_controller_reads_dashboard_bridge_paths_from_manifest():
    text = _controller_text()

    assert 'DASHBOARD_STATE_FILE := RequireManifestValue("commands", "dashboard_state_file")' in text
    assert 'DASHBOARD_CMD_FILE := RequireManifestValue("commands", "dashboard_cmd_file")' in text


def test_controller_no_longer_reads_media_actions_module_from_manifest():
    text = _controller_text()

    assert 'MEDIA_ACTIONS_MODULE := RequireManifestValue("modules", "media_actions_module")' not in text
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


def test_controller_reads_controller_window_layout_module_from_manifest():
    text = _controller_text()

    assert 'CONTROLLER_WINDOW_LAYOUT_MODULE := RequireManifestValue("modules", "controller_window_layout_module")' in text


def test_controller_reads_controller_vlc_actions_module_from_manifest():
    text = _controller_text()

    assert 'CONTROLLER_VLC_ACTIONS_MODULE := RequireManifestValue("modules", "controller_vlc_actions_module")' in text


def test_controller_reads_controller_random_favs_browser_module_from_manifest():
    text = _controller_text()

    assert 'CONTROLLER_RANDOM_FAVS_BROWSER_MODULE := RequireManifestValue("modules", "controller_random_favs_browser_module")' in text
    assert 'RANDOM_FAVS_BROWSER_ENABLED := RequireManifestValue("random_favs_browser", "enabled") = "1"' in text


def test_controller_reads_controller_startup_module_from_manifest():
    text = _controller_text()

    assert 'CONTROLLER_STARTUP_MODULE := RequireManifestValue("modules", "controller_startup_module")' in text
    assert 'RunControllerStartupAction(args) {' in text
    assert 'cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_STARTUP_MODULE . " " . args' in text


def test_controller_dashboard_update_does_not_shadow_robot_hand_enabled_helper():
    text = _controller_text()

    assert 'robotHandEnabledNow := RobotHandEnabled()' in text
    assert 'primaryUsesRobotHand := robotHandMode && robotHandEnabledNow' in text
    assert 'primaryResponsive := IsVlcResponsive(PRIMARY_VLC_PORT)' in text
    assert 'mfpAlive := pidM && ProcessExist(pidM)' in text
    assert 'WriteDashboardStateSnapshot(primaryPath, portraitPath, landscapePath, primaryUsesRobotHand, osr2Auto, robotHandEnabledNow, primaryResponsive, mfpAlive, x, y, w, h, locked2, locked3)' in text


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
    assert 'WriteDashboardStateSnapshot(primaryPath, portraitPath, landscapePath, primaryUsesRobotHand, osr2Auto, robotHandEnabledNow, primaryResponsive, mfpAlive, x, y, w, h, locked2, locked3)' in update_block


def test_controller_dashboard_snapshot_writer_declares_cache_global():
    text = _controller_text()

    snapshot_start = text.index("WriteDashboardStateSnapshot(")
    escape_start = text.index("\nIniEscape(value) {", snapshot_start)
    snapshot_block = text[snapshot_start:escape_start]

    assert 'lastDashboardSnapshotText := ""' in text
    assert "global fModeEnabled, lastDashboardSnapshotText" in snapshot_block
    assert "if (snapshotText = lastDashboardSnapshotText)" in snapshot_block


def test_controller_dashboard_export_is_raw_runtime_state_only():
    text = _controller_text()

    assert "ClipLabelFromPath(" not in text
    assert "PrimaryPanelShouldHighlight(" not in text
    assert "SatellitePanelShouldHighlight(" not in text
    assert "BuildMirroredFunscriptPath(" not in text
    assert "HasMatchingFunscript(" not in text
    assert "ReadFavsContent(" not in text
    assert "IsFavoritePath(" not in text


def test_controller_restores_random_favs_browser_launch_spec_helpers():
    text = _controller_text()

    assert 'try FileGetShortcut(RANDOM_FAVS_BROWSER_SHORTCUT_PATH, &target, &workDir, &args, &description, &iconPath, &iconNum, &runState)' in text
    assert 'LaunchRandomFavsBrowserViaPython(RANDOM_FAVS_BROWSER_MANIFEST_FILE, target, workDir, args)' in text
    assert 'encodedShortcutArgs := Base64EncodeUtf8(shortcutArgs)' in text
    assert 'args := "launch"' in text
    assert ' --shortcut-args-b64 ' in text
    assert 'LoadRandomFavsBrowserLaunchPlan(path) {' not in text
    assert 'if (!RANDOM_FAVS_BROWSER_ENABLED)' in text


def test_controller_delegates_startup_broker_restart_and_browser_manifest_prep_to_python():
    text = _controller_text()

    assert 'args := "restart-broker --project-dir " . Q(PROJECT_DIR)' in text
    assert 'args := "prepare-random-favs-browser-manifest"' in text
    assert '. " --config " . Q(CONFIG_PATH)' in text
    assert '. " --output " . Q(RANDOM_FAVS_BROWSER_MANIFEST_FILE)' in text
    assert '"powershell.exe -NoProfile -WindowStyle Hidden -Command "' not in text[text.index("RestartBroker() {"):text.index("\nPositionAll(pid1, pid2, pid3, pidM) {")]
    assert '. " -m fun_time.random_favs_browser"' not in text[text.index("PrepareRandomFavsBrowserManifest() {"):text.index("\nMaybeLaunchRandomFavsBrowser(pidM) {")]


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
    assert 'case "omnipause_toggle":' in text
    assert 'case "fmode_toggle":' in text
    assert 'case "robot_toggle":' in text


def test_controller_shutdown_closes_python_dashboard_process():
    text = _controller_text()

    assert "ShutdownAll() {" in text
    assert "SetTimer(ProcessDashboardCommand, 0)" in text
    assert "for pid in [pid1, pid2, pid3, pidM, pidD, pidR, pidA]" in text


def test_controller_delegates_lock_execution_to_python_app():
    text = _controller_text()

    assert 'RunControllerLockAction(action, which, locked, currentPath, planPath, extraArgs := "")' in text
    assert 'LoadLockActionPlan(path)' in text
    assert 'plan := RunControllerLockAction("apply-toggle-lock", which, currentLocked, currentPath, planPath' in text
    assert 'plan := RunControllerLockAction("apply-discard", which, currentLocked, src, planPath' in text
    assert 'plan := RunControllerLockAction("apply-cancel-lock", which, currentLocked, "", planPath' in text
    assert 'SetRepeatMode(port, plan["repeat_mode"])' not in text
    assert 'EnsureInFavs(currentPath)' not in text
    assert 'RemoveFromFavs(src)' not in text
    assert 'MoveToWeird(src)' not in text


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
    assert 'plan := RunControllerWindowLayout(mainRect, secondaryRect, mfpW, mfpH, planPath)' in text
    assert 'MovePidWindow(pid3, plan["landscape"]["x"], plan["landscape"]["y"], plan["landscape"]["w"], plan["landscape"]["h"])' in text
    assert 'PositionMfpWindow(pidM)' in text


def test_controller_uses_secondary_monitor_for_portrait_primary_and_robot_hand():
    text = _controller_text()

    assert "GetLogicalMonitorRects(&mainRect, &secondaryRect)" in text
    assert 'MovePidWindow(pid2, plan["portrait"]["x"], plan["portrait"]["y"], plan["portrait"]["w"], plan["portrait"]["h"])' in text
    assert 'MovePidWindow(pid1, plan["primary"]["x"], plan["primary"]["y"], plan["primary"]["w"], plan["primary"]["h"])' in text
    assert 'x := plan["robot_hand"]["x"]' in text
    assert 'y := plan["robot_hand"]["y"]' in text
    assert 'w := plan["robot_hand"]["w"]' in text
    assert 'h := plan["robot_hand"]["h"]' in text


def test_controller_delegates_window_layout_planning_to_python_plan():
    text = _controller_text()

    assert 'BuildWindowLayoutPlanPath() {' in text
    assert 'static counter := 0' in text
    assert 'counter += 1' in text
    assert 'return STATE_DIR . "\\window_layout_plan_" . A_TickCount . "_" . counter . ".ini"' in text
    assert 'RunControllerWindowLayout(mainRect, secondaryRect, mfpW, mfpH, planPath) {' in text
    assert 'LoadWindowLayoutPlan(path) {' in text
    assert 'cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_WINDOW_LAYOUT_MODULE . " " . args' in text
    assert 'plan["dashboard"] := ' not in text
    assert 'for section in ["portrait", "primary", "landscape", "mfp", "dashboard", "random_favs_browser", "robot_hand"] {' in text


def test_controller_delegates_write_side_vlc_actions_to_python():
    text = _controller_text()

    assert 'RunControllerVlcAction(args) {' in text
    assert 'cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_VLC_ACTIONS_MODULE . " " . args' in text
    assert 'args := "send-command"' in text
    assert 'args := "ensure-playback-state"' in text
    assert 'args := "set-repeat-mode"' in text
    assert 'args := "replace-playlist"' not in text
    assert "SendVlcInputCommand(" not in text
    assert "GetRepeatMode(" not in text


def test_controller_delegates_current_file_path_and_http_wait_to_python():
    text = _controller_text()

    assert 'BuildVlcQueryOutputPath(prefix) {' in text
    assert 'args := "wait-for-http"' in text
    assert 'args := "current-file-path"' in text
    assert 'return Trim(FileRead(outputPath, "UTF-8"))' in text
    assert "DecodeFileUri(" not in text
    assert "UrlDecode(" not in text


def test_controller_no_longer_keeps_raw_vlc_command_sender_in_ahk():
    text = _controller_text()

    assert "VlcHttpCmd(port, cmd) {" not in text
    assert 'SendVlcCommand(port, cmd) {' in text

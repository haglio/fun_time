from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_SHIM_AHK = PROJECT_ROOT / "controller.ahk"
WINDOWS_BRIDGE_AHK = PROJECT_ROOT / "windows_bridge.ahk"
WINDOWS_BRIDGE_WINDOWS_AHK = PROJECT_ROOT / "windows_bridge_windows.ahk"
WINDOWS_BRIDGE_RUNTIME_AHK = PROJECT_ROOT / "windows_bridge_runtime.ahk"
WINDOWS_BRIDGE_ACTIONS_AHK = PROJECT_ROOT / "windows_bridge_actions.ahk"
DASHBOARD_LAYOUT_PY = PROJECT_ROOT / "fun_time" / "dashboard_layout.py"


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


def _dashboard_layout_text() -> str:
    return DASHBOARD_LAYOUT_PY.read_text(encoding="utf-8")


def test_all_fun_time_action_hotkeys_are_global():
    text = _windows_bridge_text()

    assert "#HotIf IsOurWindow()" not in text

    for hotkey in (
        "[::HandlePrevAction()",
        "SC01A::HandlePrevAction()",
        "]::HandleNextAction()",
        "SC01B::HandleNextAction()",
        "r::ToggleRobotHandEnabled()",
        "$f::ToggleFMode()",
        "\\::{",
        "-::try ControlSend(\"!{Left}\", , \"ahk_pid \" pid1)",
        "=::try ControlSend(\"!{Right}\", , \"ahk_pid \" pid1)",
        "Left::{",
        "Right::{",
        "Up::Discard(2)",
        "Down::ToggleLock(2)",
        "a::{",
        "d::{",
        "w::Discard(3)",
        "s::ToggleLock(3)",
    ):
        assert hotkey in text


def test_only_escape_and_shutdown_are_suspend_exempt():
    text = _windows_bridge_text()

    suspend_exempt_start = text.index("#SuspendExempt true")
    suspend_exempt_end = text.index("#SuspendExempt false", suspend_exempt_start)
    suspend_exempt_block = text[suspend_exempt_start:suspend_exempt_end]

    assert "^!q::ShutdownAll()" in suspend_exempt_block
    assert "Esc::OmniPauseToggle()" in suspend_exempt_block

    for hotkey in (
        "[::HandlePrevAction()",
        "]::HandleNextAction()",
        "r::ToggleRobotHandEnabled()",
        "$f::ToggleFMode()",
        "\\::{",
        "Down::ToggleLock(2)",
        "s::ToggleLock(3)",
    ):
        assert hotkey not in suspend_exempt_block


def test_omnipause_toggle_no_longer_depends_on_active_window():
    text = _windows_bridge_text()

    toggle_start = text.index("OmniPauseToggle() {")
    enter_start = text.index("EnterOmniPause() {", toggle_start)
    toggle_block = text[toggle_start:enter_start]

    assert 'args := "build-omnipause-toggle"' in toggle_block
    assert 'RunWindowsBridgeRuntimeFlowAction(args)' in toggle_block
    assert 'if (plan["action"] = "enter")' in toggle_block
    assert "LeaveOmniPause()" in toggle_block
    assert "IsOurWindow()" not in toggle_block


def test_omnipause_non_window_side_effects_are_applied_via_python_helper():
    text = _windows_bridge_text()

    enter_start = text.index("EnterOmniPause() {")
    leave_start = text.index("LeaveOmniPause(", enter_start)
    enter_block = text[enter_start:leave_start]
    leave_end = text.index("StartWindowsBridge() {", leave_start)
    leave_block = text[leave_start:leave_end]

    assert 'args := "apply-enter-omnipause"' in enter_block
    assert 'RunWindowsBridgeRuntimeFlowAction(args)' in enter_block
    assert 'SendVlcCommand(VLC2_PORT, "pl_pause")' not in enter_block
    assert 'SetRobotHandPaused(true)' not in enter_block
    assert 'EnsurePrimaryVlcPlayback(false)' not in enter_block

    assert 'args := "apply-leave-omnipause"' in leave_block
    assert 'RunWindowsBridgeRuntimeFlowAction(args)' in leave_block
    assert 'SendVlcCommand(VLC2_PORT, "pl_pause")' not in leave_block
    assert 'SetRobotHandPaused(false)' not in leave_block
    assert 'EnsurePrimaryVlcPlayback(true)' not in leave_block


def test_omnipause_still_restores_topmost_for_robot_hand_and_media_windows():
    text = _windows_bridge_text()

    assert 'try WinSetAlwaysOnTop(false, "Robot Hand")' in text
    assert 'for pid in [pid1, pid2, pid3, pidM, pidD] {' in text
    assert 'try WinSetAlwaysOnTop(false, "ahk_pid " pid)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pid1)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pidD)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pid2)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pid3)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pidM)' in text
    assert 'try WinSetAlwaysOnTop(true, "Robot Hand")' in text


def test_status_indicator_shows_robot_hand_and_f_mode_state():
    text = _windows_bridge_text()

    assert 'LABEL_PRIMARY_VLC := "Non-AI VLC"' in text
    assert 'LABEL_PRIMARY_ROBOT := "Non-AI Robot Hand"' in text
    assert 'LABEL_PORTRAIT_VLC := "Portrait AI VLC"' in text
    assert 'LABEL_LANDSCAPE_VLC := "Landscape AI VLC"' in text
    assert 'LABEL_OSR2 := "OSR2"' in text
    assert 'LABEL_MFP := "MFP"' in text
    assert 'LABEL_BROKER := "Broker"' in text
    assert 'LABEL_CONTROLLER := "Controller"' in text
    assert 'LABEL_F_MODE := "F-Mode"' in text
    assert 'args := "--output-file " . Q(DASHBOARD_STATE_FILE)' in text
    assert '. " --f-mode-enabled " . (fModeEnabled ? "1" : "0")' in text


def test_dashboard_highlights_are_derived_in_python_dashboard_app():
    text = _windows_bridge_text()

    assert "PrimaryPanelShouldHighlight(" not in text
    assert "SatellitePanelShouldHighlight(" not in text
    assert "IsFavoritePath(" not in text
    assert "HasMatchingFunscript(" not in text
    assert "ReadFavsContent(" not in text
    assert "ClipLabelFromPath(" not in text


def test_dashboard_layout_uses_monitor_work_areas_for_preview_proportions():
    text = _windows_bridge_text()

    assert "GetLogicalMonitorRects(&mainRect, &secondaryRect)" in text
    assert 'GetCurrentWindowLayout(&plan, mfpW, mfpH)' in text
    assert 'plan["dashboard"]["w"]' in text
    assert 'plan["dashboard"]["h"]' in text


def test_dashboard_layout_places_main_monitor_on_left_and_secondary_on_right():
    text = _windows_bridge_text()

    assert 'MovePidWindow(pid2, plan["portrait"]["x"], plan["portrait"]["y"], plan["portrait"]["w"], plan["portrait"]["h"])' in text
    assert 'MovePidWindow(pid1, plan["primary"]["x"], plan["primary"]["y"], plan["primary"]["w"], plan["primary"]["h"])' in text
    assert 'MovePidWindow(pid3, plan["landscape"]["x"], plan["landscape"]["y"], plan["landscape"]["w"], plan["landscape"]["h"])' in text
    assert 'moveX := plan["mfp"]["x"]' in text
    assert 'moveY := plan["mfp"]["y"]' in text


def test_dashboard_places_controls_inside_vlc_panels():
    text = _dashboard_layout_text()

    assert 'portrait_prev=Rect(right_inner_x + 6, portrait_button_y, 18, 22)' in text
    assert 'portrait_trash=Rect(right_inner_x + (right_inner_w - 30) // 2, portrait_stack_y, 30, 16)' in text
    assert 'portrait_lock=Rect(right_inner_x + (right_inner_w - 30) // 2, portrait_stack_y + 20, 30, 16)' in text
    assert 'primary_prev=Rect(right_inner_x + 6, primary_button_y, 18, 22)' in text
    assert 'quarter_button=Rect(right_inner_x + (right_inner_w - 28) // 2, primary_y + (primary_h - 16) // 2, 28, 16)' in text
    assert 'landscape_prev=Rect(landscape_x + 6, landscape_button_y, 18, 22)' in text
    assert 'landscape_trash=Rect(landscape_x + (landscape_w - 30) // 2, landscape_stack_y, 30, 16)' in text


def test_dashboard_centers_main_preview_vertically_without_monitor_labels():
    text = _windows_bridge_text()

    assert 'GetCurrentWindowLayout(&plan)' in text
    assert 'plan["dashboard"]["x"]' in text
    assert 'plan["dashboard"]["y"]' in text


def test_dashboard_window_rect_centers_above_mfp():
    text = _windows_bridge_text()

    assert "GetActualMfpSize(&mfpW, &mfpH)" in text
    assert 'GetCurrentWindowLayout(&plan)' in text
    assert 'args := "launch-ui-companions"' in text
    assert 'pidD := startupResult["dashboard_pid"]' in text
    assert 'plan["dashboard"]["x"]' in text
    assert 'plan["dashboard"]["y"]' in text
    assert 'plan["dashboard"]["w"]' in text
    assert 'plan["dashboard"]["h"]' in text


def test_dashboard_does_not_include_hover_tip_workaround():
    text = _windows_bridge_text()

    assert "UpdateFunTimeDashboardHover()" not in text
    assert "SetTimer(UpdateFunTimeDashboardHover, 100)" not in text
    assert '"hover_tip"' not in text


def test_dashboard_exports_controller_state_snapshot_for_python_bridge():
    text = _windows_bridge_text()

    assert 'RunWindowsBridgeDashboardBridgeAction(args)' in text
    assert 'args := "--output-file " . Q(DASHBOARD_STATE_FILE)' in text
    assert '. " --robot-link-enabled " . (robotHandEnabledNow ? "1" : "0")' in text
    assert '. " --osr2-mode " . (osr2Auto ? "auto" : "controlled")' in text
    assert '. " --mfp-alive " . (mfpAlive ? "1" : "0")' in text
    assert '. " --primary-uses-robot-hand " . (primaryUsesRobotHand ? "1" : "0")' in text
    assert '. " --portrait-locked " . (locked2 ? "1" : "0")' in text
    assert '. " --landscape-locked " . (locked3 ? "1" : "0")' in text


def test_dashboard_no_longer_caches_broker_and_mfp_status_probes_in_ahk():
    text = _windows_bridge_text()

    assert "GetDashboardStatusSnapshot(&brokerRunning, &mfpConnected) {" not in text
    assert "dashboardStatusRefreshTick := 0" not in text
    assert "dashboardMfpConnected := " not in text
    assert 'mfpAlive := pidM && ProcessExist(pidM)' in text


def test_dashboard_snapshot_writer_logic_is_no_longer_inline_in_ahk():
    text = _windows_bridge_text()

    assert 'static lastDashboardSnapshotText := ""' not in text
    assert 'if (snapshotText = lastDashboardSnapshotText)' not in text
    assert 'lastDashboardSnapshotText := snapshotText' not in text
    assert 'IniEscape(value) {' not in text


def test_dashboard_uses_smaller_font_for_status_chips_and_keeps_title_in_bottom_left():
    text = _windows_bridge_text()

    assert 'LABEL_BROKER := "Broker"' in text
    assert 'LABEL_CONTROLLER := "Controller"' in text
    assert 'LABEL_F_MODE := "F-Mode"' in text


def test_dashboard_preview_mfp_box_uses_tall_portraitish_ratio():
    text = _windows_bridge_text()

    assert 'GetActualMfpSize(&w, &h) {' in text
    assert 'w := Floor(leftW * Clamp01(MFP_WIDTH_RATIO))' in text
    assert 'h := Floor(mainRect["h"] * Clamp01(MFP_HEIGHT_RATIO))' in text


def test_controller_no_longer_keeps_media_mutation_helpers_in_ahk():
    text = _windows_bridge_text()

    assert "RunMediaAction(" not in text
    assert "EnsureInFavs(fullPath)" not in text
    assert "RemoveFromFavs(fullPath)" not in text
    assert "MoveToWeird(srcPath)" not in text
    assert 'FileAppend("local_file,web_url`r`n", FAVS_FILE, "UTF-8")' not in text
    assert 'FileMove(srcPath, dest, false)' not in text


def test_real_mfp_window_is_centered_in_left_partition():
    text = _windows_bridge_text()

    assert "PositionMfpWindow(pidM) {" in text
    assert 'WinGetPos(&actualX, &actualY, &actualW, &actualH, "ahk_id " hwnd)' in text
    assert 'GetCurrentWindowLayout(&plan, actualW, actualH)' in text
    assert 'deltaX := plan["mfp"]["x"] - actualX' in text
    assert 'deltaY := plan["mfp"]["y"] - actualY' in text


def test_left_partition_stack_layout_uses_equal_vertical_gaps():
    text = _windows_bridge_text()

    assert 'GetCurrentWindowLayout(&plan, mfpW, mfpH)' in text
    assert 'moveX := plan["mfp"]["x"]' in text
    assert 'moveY := plan["mfp"]["y"]' in text


def test_primary_f_mode_funscript_path_logic_is_no_longer_in_controller():
    text = _windows_bridge_text()

    assert 'StrReplace(sourceRootNorm, "\\videos\\videos\\", "\\videos\\scripts\\scripts\\")' not in text
    assert 'RegExReplace(relativePath, "\\.[^.\\\\\\/]+$", ".funscript")' not in text


def test_controller_no_longer_parses_vlc_playlist_xml_in_ahk():
    text = _windows_bridge_text()

    assert 'args := "current-file-path"' in text
    assert 'args := "wait-for-http"' in text
    assert 'RegExMatch(xml, "i)uri=' not in text
    assert "DecodeFileUri(" not in text
    assert "UrlDecode(" not in text


def test_robot_hand_sync_non_window_side_effects_are_applied_via_python_helper():
    text = _windows_bridge_text()

    sync_start = text.index("SyncRobotHandState() {")
    toggle_start = text.index("ToggleRobotHandEnabled() {", sync_start)
    sync_block = text[sync_start:toggle_start]

    assert 'args := "sync-robot-hand"' in sync_block
    assert 'RunWindowsBridgeRuntimeFlowAction(args)' in sync_block
    assert '--enabled-file ' in sync_block
    assert '--mode-state-file ' in sync_block
    assert '--paused-file ' in sync_block
    assert '--audio-paused-file ' in sync_block
    assert 'EnsurePrimaryVlcPlayback(' not in sync_block
    assert 'SetRobotHandPaused(' not in sync_block
    assert 'SetRobotHandAudioPaused(' not in sync_block
    assert 'ApplyRobotHandPlanWindowState(plan)' in sync_block



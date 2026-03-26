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
        "[::{",
        "SC01A::{",
        "]::{",
        "SC01B::{",
        "r::{",
        "$f::{",
        "\\::{",
        "-::try ControlSend(\"!{Left}\", , \"ahk_pid \" pid1)",
        "=::try ControlSend(\"!{Right}\", , \"ahk_pid \" pid1)",
        "Left::{",
        "Right::{",
        "Up::{",
        "Down::{",
        "a::{",
        "d::{",
        "w::{",
        "s::{",
    ):
        assert hotkey in text


def test_only_escape_and_shutdown_are_suspend_exempt():
    text = _windows_bridge_text()

    suspend_exempt_start = text.index("#SuspendExempt true")
    suspend_exempt_end = text.index("#SuspendExempt false", suspend_exempt_start)
    suspend_exempt_block = text[suspend_exempt_start:suspend_exempt_end]

    assert "^!q::ShutdownAll()" in suspend_exempt_block
    assert 'HandleOmniPauseToggle()' in suspend_exempt_block

    for hotkey in (
        "[::{",
        "]::{",
        "r::{",
        "$f::{",
        "\\::{",
        "Down::{",
        "s::{",
    ):
        assert hotkey not in suspend_exempt_block


def test_omnipause_toggle_dispatched_via_python_with_window_ops():
    text = _windows_bridge_text()

    assert '\nOmniPauseToggle() {' not in text
    assert 'HandleOmniPauseToggle() {' in text

    handler_start = text.index("HandleOmniPauseToggle() {")
    handler_end = text.index("\n}", handler_start) + 2
    handler_block = text[handler_start:handler_end]

    assert 'DispatchBridgeCommand("omnipause_toggle")' in handler_block
    assert 'WinSetAlwaysOnTop(false, "ahk_pid " pid)' in handler_block
    assert 'WinSetAlwaysOnTop(true, "ahk_pid " pid2)' in handler_block
    assert 'WinSetAlwaysOnTop(true, "ahk_pid " pid3)' in handler_block
    assert 'WinSetAlwaysOnTop(true, "ahk_pid " pidM)' in handler_block
    assert 'SyncRobotHandState()' in handler_block


def test_omnipause_enter_leave_use_dispatch_channel_not_old_protocol():
    text = _windows_bridge_text()

    assert 'DispatchBridgeCommand("enter_omnipause")' in text
    assert 'DispatchBridgeCommand("leave_omnipause_skip_primary")' in text
    assert 'EnterOmniPause() {' not in text
    assert 'LeaveOmniPause(' not in text
    assert 'RunWindowsBridgeRuntimeFlowAction(' not in text
    assert 'args := "apply-enter-omnipause"' not in text
    assert 'args := "apply-leave-omnipause"' not in text
    assert 'SendVlcCommand(VLC2_PORT, "pl_pause")' not in text
    assert 'SetRobotHandPaused(true)' not in text
    assert 'EnsurePrimaryVlcPlayback(false)' not in text


def test_omnipause_still_restores_topmost_for_robot_hand_and_media_windows():
    text = _windows_bridge_text()

    assert 'try WinSetAlwaysOnTop(false, "ahk_pid " pid)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pid1)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pidD)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pid2)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pid3)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pidM)' in text


def test_status_indicator_shows_robot_hand_and_f_mode_state():
    text = _windows_bridge_text()

    # Labels moved to Python — no longer defined in AHK
    assert 'LABEL_PRIMARY_VLC' not in text
    assert 'LABEL_BROKER' not in text
    assert 'LABEL_F_MODE' not in text
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


def test_dashboard_state_is_written_by_dispatch_channel():
    text = _windows_bridge_text()

    assert 'RunWindowsBridgeDashboardBridgeAction(' not in text
    assert 'UpdateFunTimeDashboard()' not in text
    assert '. " --dashboard-state-file " . Q(DASHBOARD_STATE_FILE)' in text
    assert '. " --dashboard-enabled " . (DASHBOARD_ENABLED ? "1" : "0")' in text
    assert '. " --mfp-alive " . (mfpAlive ? "1" : "0")' in text


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

    # Status chip labels are now Python-only — not in AHK
    assert 'LABEL_BROKER' not in text
    assert 'LABEL_CONTROLLER' not in text
    assert 'LABEL_F_MODE' not in text


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

    assert 'args := "current-file-path"' not in text
    assert 'args := "wait-for-http"' not in text
    assert 'RegExMatch(xml, "i)uri=' not in text
    assert "DecodeFileUri(" not in text
    assert "UrlDecode(" not in text


def test_robot_hand_sync_dispatched_via_python():
    text = _windows_bridge_text()

    sync_start = text.index("SyncRobotHandState() {")
    sync_end = text.index("\n}", sync_start) + 2
    sync_block = text[sync_start:sync_end]

    assert 'DispatchBridgeCommand("sync_robot_hand")' in sync_block
    assert 'UpdateFunTimeDashboard()' not in sync_block
    assert 'ToggleRobotHandEnabled() {' not in text
    assert 'ApplyRobotHandPlanWindowState(' not in text
    assert 'EnsurePrimaryVlcPlayback(' not in text



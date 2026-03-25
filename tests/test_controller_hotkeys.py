from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_AHK = PROJECT_ROOT / "controller.ahk"


def _controller_text() -> str:
    return CONTROLLER_AHK.read_text(encoding="utf-8")


def test_all_fun_time_action_hotkeys_are_global():
    text = _controller_text()

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
    text = _controller_text()

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
    text = _controller_text()

    toggle_start = text.index("OmniPauseToggle() {")
    enter_start = text.index("EnterOmniPause() {", toggle_start)
    toggle_block = text[toggle_start:enter_start]

    assert 'plan := RunControllerOmniPauseAction("toggle", omniPaused, robotHandMode, false, planPath)' in toggle_block
    assert 'if (plan["action"] = "enter")' in toggle_block
    assert "LeaveOmniPause()" in toggle_block
    assert "IsOurWindow()" not in toggle_block


def test_omnipause_still_restores_topmost_for_robot_hand_and_media_windows():
    text = _controller_text()

    assert 'try WinSetAlwaysOnTop(false, "Robot Hand")' in text
    assert 'for pid in [pid1, pid2, pid3, pidM] {' in text
    assert 'try WinSetAlwaysOnTop(false, "ahk_pid " pid)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pid1)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pid2)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pid3)' in text
    assert 'try WinSetAlwaysOnTop(true, "ahk_pid " pidM)' in text
    assert 'try WinSetAlwaysOnTop(true, "Robot Hand")' in text


def test_status_indicator_shows_robot_hand_and_f_mode_state():
    text = _controller_text()

    assert 'LABEL_PRIMARY_VLC := "Non-AI VLC"' in text
    assert 'LABEL_PRIMARY_ROBOT := "Non-AI Robot Hand"' in text
    assert 'LABEL_PORTRAIT_VLC := "Portrait AI VLC"' in text
    assert 'LABEL_LANDSCAPE_VLC := "Landscape AI VLC"' in text
    assert 'LABEL_OSR2 := "OSR2"' in text
    assert 'LABEL_MFP := "MFP"' in text
    assert 'LABEL_BROKER := "Broker"' in text
    assert 'LABEL_CONTROLLER := "Controller"' in text
    assert 'LABEL_F_MODE := "F-Mode"' in text
    assert 'AddDashboardText(guiObj, controls, "broker_panel"' in text
    assert 'AddDashboardText(guiObj, controls, "controller_panel"' in text
    assert 'AddDashboardText(guiObj, controls, "fmode_panel"' in text
    assert '"b"' in text
    assert '"c"' in text
    assert '"f"' in text


def test_dashboard_highlights_use_favs_and_funscript_state():
    text = _controller_text()

    assert "PrimaryPanelShouldHighlight()" in text
    assert "SatellitePanelShouldHighlight(port)" in text
    assert "IsFavoritePath(GetCurrentFilePath(port), ReadFavsContent())" in text
    assert "HasMatchingFunscript(path)" in text


def test_dashboard_layout_uses_monitor_work_areas_for_preview_proportions():
    text = _controller_text()

    assert "GetDashboardMonitorPreviewLayout(&layout)" in text
    assert "GetLogicalMonitorRects(&mainRect, &secondaryRect)" in text
    assert 'leftW := Round(mainRect["w"] * baseScale)' in text
    assert 'leftH := Round(mainRect["h"] * baseScale)' in text
    assert 'rightW := Round(secondaryRect["w"] * baseScale)' in text
    assert 'rightH := Round(secondaryRect["h"] * baseScale)' in text
    assert 'layout["main_monitor"]' in text
    assert 'layout["secondary_monitor"]' in text


def test_dashboard_layout_places_main_monitor_on_left_and_secondary_on_right():
    text = _controller_text()

    assert 'SetDashboardControlRect(funTimeDashboardControls["main_status_strip"]' in text
    assert 'SetDashboardControlRect(funTimeDashboardControls["main_monitor"]' in text
    assert 'SetDashboardControlRect(funTimeDashboardControls["secondary_monitor"]' in text
    assert 'SetDashboardControlRect(funTimeDashboardControls["mfp_panel"]' in text
    assert 'SetDashboardControlRect(funTimeDashboardControls["landscape_panel"]' in text
    assert 'SetDashboardControlRect(funTimeDashboardControls["portrait_panel"]' in text
    assert 'SetDashboardControlRect(funTimeDashboardControls["primary_panel"]' in text
    assert 'SetDashboardControlRect(funTimeDashboardControls["broker_panel"]' in text
    assert 'SetDashboardControlRect(funTimeDashboardControls["controller_panel"]' in text
    assert 'SetDashboardControlRect(funTimeDashboardControls["fmode_panel"]' in text


def test_dashboard_places_controls_inside_vlc_panels():
    text = _controller_text()

    assert '"portrait_prev", Map("x", portraitRect["x"] + 6' in text
    assert '"portrait_trash", Map("x", portraitRect["x"] + Floor((portraitRect["w"] - 30) / 2), "y", portraitRect["y"] + Floor((portraitRect["h"] - 36) / 2)' in text
    assert '"portrait_lock", Map("x", portraitRect["x"] + Floor((portraitRect["w"] - 30) / 2)' in text
    assert '"primary_prev", Map("x", primaryRect["x"] + 6' in text
    assert '"quarter_button", Map("x", primaryRect["x"] + Floor((primaryRect["w"] - 28) / 2), "y", primaryRect["y"] + Floor((primaryRect["h"] - 16) / 2), "w", 28, "h", 16)' in text
    assert '"landscape_prev", Map("x", landscapeRect["x"] + 6' in text
    assert '"landscape_trash", Map("x", landscapeRect["x"] + Floor((landscapeRect["w"] - 30) / 2), "y", landscapeRect["y"] + Floor((landscapeRect["h"] - 36) / 2)' in text


def test_dashboard_centers_main_preview_vertically_without_monitor_labels():
    text = _controller_text()

    assert 'portraitUnits := 7' in text
    assert 'primaryUnits := 4' in text
    assert 'mainY := portraitY + Floor((portraitH - leftH) / 2)' in text
    assert 'topY := outerPad' in text
    assert 'secondaryY := topY' in text
    assert 'mainInnerY := mainY + innerPad' in text
    assert 'rightInnerY := secondaryY + innerPad' in text
    assert 'osr2W := 56' in text
    assert 'osr2H := 56' in text
    assert 'statusStripW := Max(mfpW, statusChipSize * 3 + statusChipGap * 2 + 8)' in text
    assert 'leftColumnNudge := 2' in text
    assert 'statusStripX := mainInnerX + Floor((leftStripW - statusStripW) / 2) - leftColumnNudge' in text
    assert 'statusRowX := statusStripX + Floor((statusStripW - (statusChipSize * 3 + statusChipGap * 2)) / 2)' in text
    assert 'mfpX := mainInnerX + Floor((leftStripW - mfpW) / 2) - leftColumnNudge' in text
    assert 'landscapeY := mainInnerY' in text


def test_dashboard_window_rect_centers_above_mfp():
    text = _controller_text()

    assert "GetActualMfpSize(&mfpW, &mfpH)" in text
    assert "GetLeftPartitionStackLayout(layout[\"dashboard_w\"], layout[\"dashboard_h\"], mfpW, mfpH, &stack)" in text
    assert 'x := stack["dashboard_x"]' in text
    assert 'y := stack["dashboard_y"]' in text
    assert 'w := layout["dashboard_w"]' in text
    assert 'h := layout["dashboard_h"]' in text


def test_dashboard_does_not_include_hover_tip_workaround():
    text = _controller_text()

    assert "UpdateFunTimeDashboardHover()" not in text
    assert "SetTimer(UpdateFunTimeDashboardHover, 100)" not in text
    assert '"hover_tip"' not in text


def test_dashboard_exports_runtime_snapshot_for_python_bridge():
    text = _controller_text()

    assert "WriteDashboardStateSnapshot(primaryPath, portraitPath, landscapePath, primaryUsesRobotHand, osr2Auto, robotHandEnabledNow, brokerRunningNow, mfpConnectedNow)" in text
    assert 'IniWrite(primaryUsesRobotHand ? LABEL_PRIMARY_ROBOT : LABEL_PRIMARY_VLC, DASHBOARD_STATE_FILE, "primary", "label")' in text
    assert 'IniWrite(ClipLabelFromPath(primaryUsesRobotHand ? "" : primaryPath), DASHBOARD_STATE_FILE, "primary", "clip")' in text
    assert 'IniWrite(mfpConnected ? "1" : "0", DASHBOARD_STATE_FILE, "mfp", "connected")' in text
    assert 'IniWrite(brokerRunning ? "1" : "0", DASHBOARD_STATE_FILE, "broker", "running")' in text
    assert 'IniWrite(osr2Auto ? "auto" : "controlled", DASHBOARD_STATE_FILE, "osr2", "mode")' in text


def test_dashboard_caches_expensive_status_probes_between_refreshes():
    text = _controller_text()

    assert "GetDashboardStatusSnapshot(&brokerRunning, &mfpConnected) {" in text
    assert 'if (dashboardStatusRefreshTick = 0 || (A_TickCount - dashboardStatusRefreshTick) >= 2000) {' in text
    assert 'brokerRunningNow := IsBrokerRunning()' in text
    assert 'dashboardMfpConnected := IsProcessAlive(pidM) && IsVlcResponsive(PRIMARY_VLC_PORT) && brokerRunningNow' in text


def test_dashboard_uses_smaller_font_for_status_chips_and_keeps_title_in_bottom_left():
    text = _controller_text()

    assert 'guiObj.SetFont("s7 Bold", "Segoe UI")' in text
    assert 'titleY := previewBottom - 14' in text


def test_dashboard_preview_mfp_box_uses_tall_portraitish_ratio():
    text = _controller_text()

    assert 'mfpPreviewAspect := 0.67' in text
    assert 'mfpW := Min(mfpMaxW, Round(mfpH * mfpPreviewAspect))' in text


def test_controller_delegates_favorites_and_weird_file_mutation_to_python_media_actions():
    text = _controller_text()

    assert 'RunMediaAction("ensure-in-favs", fullPath)' in text
    assert 'RunMediaAction("remove-from-favs", fullPath)' in text
    assert 'RunMediaAction("move-to-weird", srcPath)' in text
    assert 'FileAppend("local_file,web_url`r`n", FAVS_FILE, "UTF-8")' not in text
    assert 'FileMove(srcPath, dest, false)' not in text


def test_real_mfp_window_is_centered_in_left_partition():
    text = _controller_text()

    assert "PositionMfpWindow(pidM) {" in text
    assert 'GetDashboardMonitorPreviewLayout(&layout)' in text
    assert 'WinGetPos(&actualX, &actualY, &actualW, &actualH, "ahk_id " hwnd)' in text
    assert 'GetLeftPartitionStackLayout(layout["dashboard_w"], layout["dashboard_h"], actualW, actualH, &stack)' in text
    assert 'deltaX := stack["mfp_x"] - actualX' in text
    assert 'deltaY := stack["mfp_y"] - actualY' in text


def test_left_partition_stack_layout_uses_equal_vertical_gaps():
    text = _controller_text()

    assert "GetLeftPartitionStackLayout(dashboardW, dashboardH, mfpW, mfpH, &stack) {" in text
    assert 'gapY := Floor((mainH - dashboardH - mfpH) / 3)' in text
    assert 'dashboardY := mainT + gapY' in text
    assert 'mfpY := dashboardY + dashboardH + gapY' in text


def test_primary_f_mode_uses_mirrored_funscript_tree():
    text = _controller_text()

    assert 'StrReplace(sourceRootNorm, "\\videos\\videos\\", "\\videos\\scripts\\scripts\\")' in text
    assert 'RegExReplace(relativePath, "\\.[^.\\\\\\/]+$", ".funscript")' in text

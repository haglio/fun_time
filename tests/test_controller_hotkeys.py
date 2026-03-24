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

    assert "} else {" in toggle_block
    assert "LeaveOmniPause()" in toggle_block
    assert "IsOurWindow()" not in toggle_block


def test_status_indicator_shows_robot_hand_and_f_mode_state():
    text = _controller_text()

    assert 'robotHandStatusText.Text := "Robot Hand: Enabled`nF-Mode: " . (fModeEnabled ? "On" : "Off")' in text
    assert 'robotHandStatusText.Text := "Robot Hand: Disabled`nF-Mode: " . (fModeEnabled ? "On" : "Off")' in text


def test_primary_f_mode_uses_mirrored_funscript_tree():
    text = _controller_text()

    assert 'StrReplace(sourceRootNorm, "\\videos\\videos\\", "\\videos\\scripts\\scripts\\")' in text
    assert 'RegExReplace(relativePath, "\\.[^.\\\\\\/]+$", ".funscript")' in text

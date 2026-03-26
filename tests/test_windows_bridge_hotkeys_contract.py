"""Contract tests for the minimal AHK hotkey script (windows_bridge_hotkeys.ahk).

Verifies structural properties of the script without executing it.
"""
from __future__ import annotations

from pathlib import Path

HOTKEYS_AHK = Path(__file__).resolve().parent.parent / "windows_bridge_hotkeys.ahk"


def _hotkeys_text() -> str:
    return HOTKEYS_AHK.read_text(encoding="utf-8")


class TestStructure:
    def test_script_exists(self):
        assert HOTKEYS_AHK.is_file()

    def test_requires_two_arguments(self):
        text = _hotkeys_text()
        assert "A_Args.Length < 2" in text
        assert "WINDOWS_BRIDGE_MANIFEST_PATH := A_Args[1]" in text
        assert "PIDS_FILE_PATH := A_Args[2]" in text

    def test_reads_pids_from_file_not_startup(self):
        text = _hotkeys_text()
        assert 'IniRead(PIDS_FILE_PATH, "pids",' in text
        assert "StartWindowsBridge" not in text

    def test_no_startup_orchestration(self):
        text = _hotkeys_text()
        assert "start-core-session" not in text
        assert "launch-ui-companions" not in text
        assert "PositionAll" not in text
        assert "SetTopMost" not in text
        assert "GetLogicalMonitorRects" not in text
        assert "GetMonitorRect" not in text

    def test_no_included_files(self):
        text = _hotkeys_text()
        assert "#Include" not in text

    def test_no_shutdown_of_child_processes(self):
        """Python orchestrator owns process lifecycle."""
        text = _hotkeys_text()
        assert "ShutdownAll" not in text
        assert "TryClosePid" not in text
        assert "TryKillPid" not in text
        assert "ForceKillPid" not in text


class TestHotkeyBindings:
    def test_all_hotkeys_present(self):
        text = _hotkeys_text()
        expected_commands = [
            "primary_prev", "primary_next",
            "robot_toggle", "fmode_toggle",
            "quarter_button",
            "portrait_prev", "portrait_next", "portrait_trash", "portrait_lock",
            "landscape_prev", "landscape_next", "landscape_trash", "landscape_lock",
        ]
        for cmd in expected_commands:
            assert f'DispatchBridgeCommand("{cmd}")' in text, f"Missing hotkey dispatch for {cmd}"

    def test_escape_calls_omnipause_toggle(self):
        text = _hotkeys_text()
        assert "Esc::HandleOmniPauseToggle()" in text

    def test_ctrl_alt_q_exits(self):
        text = _hotkeys_text()
        assert "^!q::ExitApp()" in text

    def test_suspend_exempt_for_exit_and_omnipause(self):
        text = _hotkeys_text()
        assert "#SuspendExempt true" in text


class TestDispatchPattern:
    def test_dispatch_command_present(self):
        text = _hotkeys_text()
        assert "DispatchBridgeCommand(cmd) {" in text
        assert "Critical" in text

    def test_dispatch_calls_python(self):
        text = _hotkeys_text()
        assert "BRIDGE_COMMAND_DISPATCH_MODULE" in text
        assert "RunHiddenWait" in text

    def test_dispatch_reads_state_from_result(self):
        text = _hotkeys_text()
        for state_key in ["locked2", "locked3", "robot_hand_mode", "f_mode_enabled", "omni_paused"]:
            assert f'"state", "{state_key}"' in text

    def test_dispatch_executes_window_ops(self):
        text = _hotkeys_text()
        for op in ["set_topmost", "activate", "show", "hide", "suspend_hotkeys", "unsuspend_hotkeys", "send_key"]:
            assert f'case "{op}"' in text


class TestTimers:
    def test_ahk_command_poll_timer(self):
        text = _hotkeys_text()
        assert "SetTimer(ProcessAhkCommand, 150)" in text

    def test_dashboard_and_sync_moved_to_python(self):
        text = _hotkeys_text()
        assert "ProcessDashboardCommand" not in text
        assert "SyncRobotHandState" not in text

    def test_reads_shared_state(self):
        text = _hotkeys_text()
        assert "ReadSharedState()" in text
        assert "SHARED_STATE_FILE" in text

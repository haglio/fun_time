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

    def test_reads_primary_pid_from_file(self):
        text = _hotkeys_text()
        assert 'IniRead(PIDS_FILE_PATH, "pids",' in text

    def test_no_startup_orchestration(self):
        text = _hotkeys_text()
        assert "start-core-session" not in text
        assert "launch-ui-companions" not in text
        assert "PositionAll" not in text
        assert "GetLogicalMonitorRects" not in text

    def test_no_included_files(self):
        text = _hotkeys_text()
        assert "#Include" not in text

    def test_no_shutdown_of_child_processes(self):
        """Python orchestrator owns process lifecycle."""
        text = _hotkeys_text()
        assert "ShutdownAll" not in text
        assert "TryClosePid" not in text
        assert "TryKillPid" not in text

    def test_no_subprocess_dispatch(self):
        """All dispatch goes through Python's background dispatch loop."""
        text = _hotkeys_text()
        assert "RunHiddenWait" not in text
        assert "DispatchBridgeCommand" not in text
        assert "CreateProcessW" not in text


class TestHotkeyBindings:
    def test_all_hotkeys_queue_commands(self):
        text = _hotkeys_text()
        expected_commands = [
            "primary_prev", "primary_next",
            "robot_toggle", "fmode_toggle",
            "quarter_button",
            "portrait_prev", "portrait_next", "portrait_trash", "portrait_lock",
            "landscape_prev", "landscape_next", "landscape_trash", "landscape_lock",
        ]
        for cmd in expected_commands:
            assert f'QueueCommand("{cmd}")' in text, f"Missing hotkey queue for {cmd}"

    def test_omnipause_queued(self):
        text = _hotkeys_text()
        assert 'QueueCommand("omnipause_toggle")' in text

    def test_open_file_dialog_queued(self):
        text = _hotkeys_text()
        assert 'QueueCommand("open_file_dialog")' in text

    def test_ctrl_alt_q_exits(self):
        text = _hotkeys_text()
        assert "^!q::ExitApp()" in text

    def test_suspend_exempt_for_exit_and_omnipause(self):
        text = _hotkeys_text()
        assert "#SuspendExempt true" in text


class TestCommandQueue:
    def test_queue_command_function_present(self):
        text = _hotkeys_text()
        assert "QueueCommand(cmd) {" in text
        assert "DASHBOARD_CMD_FILE" in text

    def test_appends_with_newline(self):
        text = _hotkeys_text()
        assert 'cmd . "`n"' in text


class TestAhkCommands:
    def test_ahk_command_poll_timer(self):
        text = _hotkeys_text()
        assert "SetTimer(ProcessAhkCommand, 150)" in text

    def test_handles_suspend_and_unsuspend(self):
        text = _hotkeys_text()
        assert '"suspend_hotkeys"' in text
        assert '"unsuspend_hotkeys"' in text
        assert "Suspend true" in text
        assert "Suspend false" in text

    def test_reads_shared_state(self):
        text = _hotkeys_text()
        assert "ReadSharedState()" in text
        assert "SHARED_STATE_FILE" in text

    def test_reads_only_robot_hand_mode(self):
        """AHK only needs robotHandMode for the backslash hotkey branching."""
        text = _hotkeys_text()
        assert "robotHandMode" in text

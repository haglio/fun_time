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

    def test_no_pid_reading(self):
        """AHK no longer reads PIDs — all window ops handled by Python."""
        text = _hotkeys_text()
        assert "IniRead(PIDS_FILE_PATH" not in text

    def test_no_shared_state_reading(self):
        """AHK no longer reads shared state — all state managed by Python."""
        text = _hotkeys_text()
        assert "ReadSharedState" not in text
        assert "robotHandMode" not in text

    def test_no_startup_orchestration(self):
        text = _hotkeys_text()
        assert "start-core-session" not in text
        assert "launch-ui-companions" not in text
        assert "PositionAll" not in text

    def test_no_included_files(self):
        text = _hotkeys_text()
        assert "#Include" not in text

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
            "genau_toggle", "fmode_toggle",
            "backslash_key",
            "vlc_nudge_prev", "vlc_nudge_next",
            "portrait_prev", "portrait_next", "portrait_trash", "portrait_lock",
            "landscape_prev", "landscape_next", "landscape_trash", "landscape_lock",
        ]
        for cmd in expected_commands:
            assert f'QueueCommand("{cmd}")' in text, f"Missing hotkey queue for {cmd}"

    def test_omnipause_queued(self):
        text = _hotkeys_text()
        assert 'QueueCommand("omnipause_toggle")' in text

    def test_ctrl_alt_q_exits(self):
        text = _hotkeys_text()
        assert "^!q::ExitApp()" in text

    def test_space_queues_enter_omnipause(self):
        text = _hotkeys_text()
        assert 'QueueCommand("enter_omnipause")' in text

    def test_space_not_suspend_exempt(self):
        """Space must NOT be in the SuspendExempt block — it enters omnipause but cannot leave it."""
        text = _hotkeys_text()
        exempt_start = text.index("#SuspendExempt true")
        exempt_end = text.index("#SuspendExempt false")
        exempt_block = text[exempt_start:exempt_end]
        assert "Space" not in exempt_block

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

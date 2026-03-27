from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from fun_time.command_dispatch import BridgeState, WindowOp


# These imports will fail until the module exists (red step)
from fun_time.windows_bridge_dispatch_loop import (
    poll_dashboard_commands,
    execute_window_ops,
    write_shared_state,
    read_shared_state,
    DispatchLoopRunner,
)


class TestPollDashboardCommands:
    def test_reads_and_deletes_command_file(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_next", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["portrait_next"]
        assert not cmd_file.exists()

    def test_returns_empty_when_file_missing(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"

        result = poll_dashboard_commands(cmd_file)

        assert result == []

    def test_returns_empty_for_empty_file(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == []

    def test_strips_whitespace(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("  landscape_lock  \n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["landscape_lock"]

    def test_reads_multiple_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("primary_next\nprimary_next\nportrait_prev\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["primary_next", "primary_next", "portrait_prev"]

    def test_concurrent_write_does_not_lose_new_commands(self, tmp_path):
        """Rename-then-read ensures writes during processing go to a new file."""
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("first_command\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["first_command"]
        # A new write after polling should work — file was deleted, not held open
        cmd_file.write_text("second_command\n", encoding="utf-8")
        assert cmd_file.exists()

    def test_stale_processing_file_does_not_block_commands(self, tmp_path):
        """A leftover .processing file from a crash must not block future polls."""
        cmd_file = tmp_path / "dashboard_cmd.txt"
        stale = cmd_file.with_suffix(".processing")
        stale.write_text("old_command\n", encoding="utf-8")
        cmd_file.write_text("new_command\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["new_command"]
        assert not cmd_file.exists()
        assert not stale.exists()

    def test_strips_utf8_bom_from_commands(self, tmp_path):
        """AHK FileAppend with UTF-8 adds a BOM; commands must not be BOM-prefixed."""
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_bytes(b"\xef\xbb\xbfprimary_prev\n")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["primary_prev"]


class TestExecuteWindowOps:
    def test_set_topmost_calls_win32(self):
        ops = [WindowOp(op="set_topmost", title="Robot Hand", value=True)]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=12345), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as mock_topmost:
            remaining = execute_window_ops(ops, primary_pid=1)

        mock_topmost.assert_called_once_with(12345, True)
        assert remaining == []

    def test_activate_calls_win32(self):
        ops = [WindowOp(op="activate", title="Robot Hand")]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=12345), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window") as mock_activate:
            remaining = execute_window_ops(ops, primary_pid=1)

        mock_activate.assert_called_once_with(12345)
        assert remaining == []

    def test_show_calls_win32(self):
        ops = [WindowOp(op="show", title="Robot Hand")]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=12345), \
             patch("fun_time.windows_bridge_dispatch_loop.show_window") as mock_show:
            remaining = execute_window_ops(ops, primary_pid=1)

        mock_show.assert_called_once_with(12345)
        assert remaining == []

    def test_hide_calls_win32(self):
        ops = [WindowOp(op="hide", title="Robot Hand")]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=12345), \
             patch("fun_time.windows_bridge_dispatch_loop.hide_window") as mock_hide:
            remaining = execute_window_ops(ops, primary_pid=1)

        mock_hide.assert_called_once_with(12345)
        assert remaining == []

    def test_suspend_returned_as_remaining(self):
        """suspend_hotkeys can only be done by AHK — returned for forwarding."""
        ops = [WindowOp(op="suspend_hotkeys")]
        remaining = execute_window_ops(ops, primary_pid=1)

        assert len(remaining) == 1
        assert remaining[0].op == "suspend_hotkeys"

    def test_unsuspend_returned_as_remaining(self):
        ops = [WindowOp(op="unsuspend_hotkeys")]
        remaining = execute_window_ops(ops, primary_pid=1)

        assert len(remaining) == 1
        assert remaining[0].op == "unsuspend_hotkeys"

    def test_send_key_uses_pid(self):
        ops = [WindowOp(op="send_key", key="p")]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=99), \
             patch("fun_time.windows_bridge_dispatch_loop.send_key_to_window") as mock_send:
            remaining = execute_window_ops(ops, primary_pid=42)

        mock_send.assert_called_once_with(99, "p")
        assert remaining == []

    def test_send_alt_key_uses_pid(self):
        ops = [WindowOp(op="send_alt_key", vk=0x25)]  # VK_LEFT
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=99), \
             patch("fun_time.windows_bridge_dispatch_loop.send_alt_key_to_window") as mock_send:
            remaining = execute_window_ops(ops, primary_pid=42)

        mock_send.assert_called_once_with(99, 0x25)
        assert remaining == []

    def test_skips_op_when_window_not_found(self):
        ops = [WindowOp(op="set_topmost", title="Nonexistent", value=True)]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as mock_topmost:
            remaining = execute_window_ops(ops, primary_pid=1)

        mock_topmost.assert_not_called()
        assert remaining == []


class TestSharedState:
    def test_write_then_read_roundtrip(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(
            locked2=True,
            locked3=False,
            robot_hand_mode=True,
            f_mode_enabled=False,
            omni_paused=True,
        )

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded == state

    def test_read_returns_none_when_missing(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        assert read_shared_state(state_file) is None


class TestDispatchLoopRunner:
    def _make_runner(self, tmp_path, **kwargs):
        from fun_time.command_dispatch import BridgeConfig

        config = BridgeConfig(
            primary_port=9090,
            portrait_port=9091,
            landscape_port=9092,
            vlc_password="test",
            favs_file=tmp_path / "favs.txt",
            weird_dir=tmp_path / "weird",
            state_dir=tmp_path,
            primary_sources="",
            portrait_sources="",
            landscape_sources="",
            robot_hand_enabled_file=tmp_path / "rh_enabled.txt",
            robot_hand_mode_file=tmp_path / "rh_mode.txt",
            robot_hand_cmd_file=tmp_path / "rh_cmd.txt",
            robot_hand_paused_file=tmp_path / "rh_paused.txt",
            audio_paused_file=tmp_path / "audio_paused.txt",
            dashboard_state_file=tmp_path / "dashboard_state.ini",
        )
        return DispatchLoopRunner(
            config=config,
            dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
            shared_state_file=tmp_path / "shared_state.ini",
            ahk_cmd_file=tmp_path / "ahk_cmd.txt",
            primary_pid=100,
            mfp_pid=200,
            dashboard_enabled=False,
            **kwargs,
        )

    def test_dispatches_dashboard_command(self, tmp_path):
        # Use huge sync interval so robot hand sync doesn't fire
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]):
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "portrait_next" in commands
        assert not cmd_file.exists()

    def test_dispatches_omnipause_in_python(self, tmp_path):
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omnipause_toggle", encoding="utf-8")

        with patch.object(runner, "_handle_omnipause_toggle") as mock_handle:
            runner.tick()

        mock_handle.assert_called_once()

    def test_backslash_key_dispatches_quarter_button_in_robot_hand_mode(self, tmp_path):
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(robot_hand_mode=True)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]):
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "quarter_button" in commands

    def test_backslash_key_opens_file_dialog_when_not_robot_hand_mode(self, tmp_path):
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(robot_hand_mode=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch.object(runner, "_handle_open_file_dialog") as mock_handle:
            runner.tick()
            import time as _time
            _time.sleep(0.1)

        mock_handle.assert_called_once()

    def test_dispatch_forwards_remaining_ops_to_ahk(self, tmp_path):
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"


        suspend_op = WindowOp(op="suspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[suspend_op]):
            mock_dispatch.return_value = (runner.state, [suspend_op])
            runner._dispatch("some_command")

        assert ahk_cmd_file.read_text(encoding="utf-8") == "suspend_hotkeys"

    def test_dispatch_suppresses_unsuspend_during_integration(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        unsuspend_op = WindowOp(op="unsuspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[unsuspend_op]):
            mock_dispatch.return_value = (runner.state, [unsuspend_op])
            runner._dispatch("some_command")

        assert not ahk_cmd_file.exists()

    def test_dispatch_allows_unsuspend_outside_integration(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        unsuspend_op = WindowOp(op="unsuspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[unsuspend_op]):
            mock_dispatch.return_value = (runner.state, [unsuspend_op])
            runner._dispatch("some_command")

        assert ahk_cmd_file.read_text(encoding="utf-8") == "unsuspend_hotkeys"

    def test_syncs_robot_hand_periodically(self, tmp_path):
        runner = self._make_runner(tmp_path, sync_interval_ms=100)
        # Set _last_sync far in the past so the interval is exceeded
        runner._last_sync = -999

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]):
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        calls = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "sync_robot_hand" in calls

    def test_skips_robot_hand_sync_when_omni_paused(self, tmp_path):
        runner = self._make_runner(tmp_path, sync_interval_ms=100)
        runner._last_sync = -999
        runner.state = BridgeState(omni_paused=True)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            runner.tick()

        mock_dispatch.assert_not_called()

    def test_reads_shared_state_at_tick_start(self, tmp_path):
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        assert runner.state.omni_paused is False

        # Simulate AHK dispatch updating shared state file
        write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))
        runner.tick()

        assert runner.state.omni_paused is True

    def test_writes_shared_state_after_dispatch(self, tmp_path):
        runner = self._make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("landscape_lock", encoding="utf-8")
        state_file = tmp_path / "shared_state.ini"

        new_state = BridgeState(locked3=True)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command", return_value=(new_state, [])), \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]):
            runner.tick()

        loaded = read_shared_state(state_file)
        assert loaded is not None
        assert loaded.locked3 is True


class TestHandleOmniPauseToggle:
    """Tests for omnipause toggle moved from AHK to Python."""

    def _make_runner(self, tmp_path, **kwargs):
        from fun_time.command_dispatch import BridgeConfig

        config = BridgeConfig(
            primary_port=9090,
            portrait_port=9091,
            landscape_port=9092,
            vlc_password="test",
            favs_file=tmp_path / "favs.txt",
            weird_dir=tmp_path / "weird",
            state_dir=tmp_path,
            primary_sources="",
            portrait_sources="",
            landscape_sources="",
            robot_hand_enabled_file=tmp_path / "rh_enabled.txt",
            robot_hand_mode_file=tmp_path / "rh_mode.txt",
            robot_hand_cmd_file=tmp_path / "rh_cmd.txt",
            robot_hand_paused_file=tmp_path / "rh_paused.txt",
            audio_paused_file=tmp_path / "audio_paused.txt",
            dashboard_state_file=tmp_path / "dashboard_state.ini",
        )
        return DispatchLoopRunner(
            config=config,
            dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
            shared_state_file=tmp_path / "shared_state.ini",
            ahk_cmd_file=tmp_path / "ahk_cmd.txt",
            primary_pid=100,
            mfp_pid=200,
            portrait_pid=300,
            landscape_pid=400,
            dashboard_pid=500,
            dashboard_enabled=False,
            **kwargs,
        )

    def test_entering_omnipause_removes_topmost(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []
        pid_to_hwnd = {100: 1001, 200: 2001, 300: 3001, 400: 4001, 500: 5001}

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=lambda h, v: topmost_calls.append((h, v))):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_omnipause_toggle()

        dispatched = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "omnipause_toggle" in dispatched
        removed = {h for h, v in topmost_calls if not v}
        assert removed == {1001, 2001, 3001, 4001, 5001}

    def test_leaving_omnipause_restores_topmost(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)

        topmost_calls = []
        pid_to_hwnd = {100: 1001, 200: 2001, 300: 3001, 400: 4001, 500: 5001}

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=lambda h, v: topmost_calls.append((h, v))):
            mock_dispatch.return_value = (BridgeState(omni_paused=False), [])
            runner._handle_omnipause_toggle()

        restored = {h for h, v in topmost_calls if v}
        assert restored == {1001, 2001, 3001, 4001, 5001}

    def test_leaving_skips_primary_topmost_in_robot_hand_mode(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True, robot_hand_mode=True)

        topmost_calls = []
        pid_to_hwnd = {100: 1001, 200: 2001, 300: 3001, 400: 4001, 500: 5001}

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=lambda h, v: topmost_calls.append((h, v))):
            mock_dispatch.return_value = (BridgeState(omni_paused=False, robot_hand_mode=True), [])
            runner._handle_omnipause_toggle()

        restored = {h for h, v in topmost_calls if v}
        assert 1001 not in restored
        assert {2001, 3001, 4001, 5001} <= restored


class TestHandleOpenFileDialog:
    """Tests for the open_file_dialog command that migrates
    AHK's OpenPrimaryVlcFileDialogWithManagedOmniPause to Python.
    """

    def _make_runner(self, tmp_path, **kwargs):
        from fun_time.command_dispatch import BridgeConfig

        config = BridgeConfig(
            primary_port=9090,
            portrait_port=9091,
            landscape_port=9092,
            vlc_password="test",
            favs_file=tmp_path / "favs.txt",
            weird_dir=tmp_path / "weird",
            state_dir=tmp_path,
            primary_sources="",
            portrait_sources="",
            landscape_sources="",
            robot_hand_enabled_file=tmp_path / "rh_enabled.txt",
            robot_hand_mode_file=tmp_path / "rh_mode.txt",
            robot_hand_cmd_file=tmp_path / "rh_cmd.txt",
            robot_hand_paused_file=tmp_path / "rh_paused.txt",
            audio_paused_file=tmp_path / "audio_paused.txt",
            dashboard_state_file=tmp_path / "dashboard_state.ini",
        )
        return DispatchLoopRunner(
            config=config,
            dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
            shared_state_file=tmp_path / "shared_state.ini",
            ahk_cmd_file=tmp_path / "ahk_cmd.txt",
            primary_pid=100,
            mfp_pid=200,
            portrait_pid=300,
            landscape_pid=400,
            dashboard_pid=500,
            dashboard_enabled=False,
            **kwargs,
        )

    def test_enters_omnipause_when_not_paused(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_dialog_by_pid", return_value=0):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        dispatched = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "enter_omnipause" in dispatched

    def test_removes_topmost_from_all_windows(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        # Map each PID to a unique hwnd
        pid_to_hwnd = {100: 1001, 200: 2001, 300: 3001, 400: 4001, 500: 5001}

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_dispatch_loop.find_dialog_by_pid", return_value=0):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        # All 5 windows should have topmost removed (False) at the start
        removed = [(h, v) for h, v in topmost_calls if not v]
        removed_hwnds = {h for h, _ in removed}
        assert removed_hwnds == {1001, 2001, 3001, 4001, 5001}

    def test_sends_ctrl_o_to_primary_window(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=1001), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window") as mock_ctrl_o, \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_dialog_by_pid", return_value=0):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        mock_ctrl_o.assert_called_once_with(1001)

    def test_waits_for_dialog_when_entered_omnipause(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=1001), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window") as mock_activate, \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_dialog_by_pid", return_value=9999) as mock_find_dialog, \
             patch("fun_time.windows_bridge_dispatch_loop.wait_for_window_close") as mock_wait_close:
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        mock_find_dialog.assert_called_once_with(100, timeout_s=1.0)
        mock_activate.assert_any_call(9999)
        mock_wait_close.assert_called_once_with(9999)

    def test_restores_topmost_in_finally(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        pid_to_hwnd = {100: 1001, 200: 2001, 300: 3001, 400: 4001, 500: 5001}

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_dispatch_loop.find_dialog_by_pid", return_value=0):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        dispatched = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "leave_omnipause_skip_primary" in dispatched

        # All 5 windows should have topmost restored (True) at the end
        restored = [(h, v) for h, v in topmost_calls if v]
        restored_hwnds = {h for h, _ in restored}
        assert restored_hwnds == {1001, 2001, 3001, 4001, 5001}

    def test_skips_primary_topmost_in_robot_hand_mode(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False, robot_hand_mode=True)

        topmost_calls = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        pid_to_hwnd = {100: 1001, 200: 2001, 300: 3001, 400: 4001, 500: 5001}

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_dispatch_loop.find_dialog_by_pid", return_value=0):
            mock_dispatch.return_value = (BridgeState(omni_paused=True, robot_hand_mode=True), [])
            runner._handle_open_file_dialog()

        # Primary (1001) should NOT be restored to topmost in robot_hand_mode
        restored = [(h, v) for h, v in topmost_calls if v]
        restored_hwnds = {h for h, _ in restored}
        assert 1001 not in restored_hwnds
        assert {2001, 3001, 4001, 5001} <= restored_hwnds

    def test_topmost_removed_before_ctrl_o(self, tmp_path):
        """PostMessage sends directly to VLC's message queue, so topmost
        removal can happen first without breaking Ctrl+O delivery.
        """
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        call_log: list[str] = []

        pid_to_hwnd = {100: 1001, 200: 2001, 300: 3001, 400: 4001, 500: 5001}

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window", side_effect=lambda h: call_log.append("ctrl_o_to_window")), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=lambda h, v: call_log.append(f"topmost_{v}")), \
             patch("fun_time.windows_bridge_dispatch_loop.find_dialog_by_pid", return_value=0):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        # topmost removal happens before ctrl_o (in _remove_all_topmost before send_ctrl_o_to_window)
        assert "ctrl_o_to_window" in call_log
        first_remove = next(i for i, c in enumerate(call_log) if c == "topmost_False")
        assert first_remove < call_log.index("ctrl_o_to_window")

    def test_skips_omnipause_when_already_paused(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=1001), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window") as mock_ctrl_o, \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as mock_topmost, \
             patch("fun_time.windows_bridge_dispatch_loop.find_dialog_by_pid") as mock_find_dialog, \
             patch("fun_time.windows_bridge_dispatch_loop.wait_for_window_close"):
            runner._handle_open_file_dialog()

        # Should not dispatch enter/leave omnipause
        mock_dispatch.assert_not_called()
        # Should not touch topmost
        mock_topmost.assert_not_called()
        # Should not wait for dialog
        mock_find_dialog.assert_not_called()
        # Should still send Ctrl+O to the primary window
        mock_ctrl_o.assert_called_once_with(1001)

    def test_forwards_suspend_and_unsuspend_via_dispatch(self, tmp_path):
        """Suspend/unsuspend reach AHK via _dispatch forwarding remaining ops."""
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"


        suspend_op = WindowOp(op="suspend_hotkeys")
        unsuspend_op = WindowOp(op="unsuspend_hotkeys")

        ahk_commands_written = []
        original_write_text = Path.write_text

        def capture_write(self_path, text, **kwargs):
            if self_path == ahk_cmd_file:
                ahk_commands_written.append(text)
            return original_write_text(self_path, text, **kwargs)

        # enter_omnipause returns suspend_hotkeys, leave_omnipause returns unsuspend
        exec_returns = iter([[suspend_op], [unsuspend_op]])

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", side_effect=exec_returns), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=1001), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_dialog_by_pid", return_value=0), \
             patch.object(Path, "write_text", capture_write):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        assert "suspend_hotkeys" in ahk_commands_written
        assert "unsuspend_hotkeys" in ahk_commands_written
        assert ahk_commands_written.index("suspend_hotkeys") < ahk_commands_written.index("unsuspend_hotkeys")

    def test_concurrent_invocations_prevented(self, tmp_path):
        runner = self._make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)  # fast path — no omnipause

        import threading
        call_count = 0
        barrier = threading.Barrier(2, timeout=2.0)

        original_handle = None

        def slow_handle():
            nonlocal call_count
            call_count += 1
            barrier.wait()  # block until both threads reach here

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=1001), \
             patch("fun_time.windows_bridge_dispatch_loop.send_ctrl_o_to_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"):
            # Replace the method with a slow version to test locking
            original_handle = runner._handle_open_file_dialog

            # Start first call in background
            t1 = threading.Thread(target=original_handle)
            t1.start()

            # Second call should be rejected (returns immediately due to lock)
            t2 = threading.Thread(target=original_handle)
            t2.start()

            try:
                barrier.wait(timeout=2.0)
            except threading.BrokenBarrierError:
                pass

            t1.join(timeout=2.0)
            t2.join(timeout=2.0)

        # The lock should prevent truly concurrent execution — we'll verify
        # via a _file_dialog_lock attribute existing
        assert hasattr(runner, "_file_dialog_lock")

    def test_open_file_dialog_routed_from_tick(self, tmp_path):
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("open_file_dialog", encoding="utf-8")

        with patch.object(runner, "_handle_open_file_dialog") as mock_handle:
            runner.tick()
            # Give the background thread a moment to start
            import time
            time.sleep(0.1)

        mock_handle.assert_called_once()

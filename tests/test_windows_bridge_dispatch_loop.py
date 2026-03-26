from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from fun_time.bridge_command_dispatch import BridgeState, WindowOp


# These imports will fail until the module exists (red step)
from fun_time.windows_bridge_dispatch_loop import (
    poll_dashboard_command,
    execute_window_ops,
    write_shared_state,
    read_shared_state,
    DispatchLoopRunner,
)


class TestPollDashboardCommand:
    def test_reads_and_deletes_command_file(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_next", encoding="utf-8")

        result = poll_dashboard_command(cmd_file)

        assert result == "portrait_next"
        assert not cmd_file.exists()

    def test_returns_none_when_file_missing(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"

        result = poll_dashboard_command(cmd_file)

        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("", encoding="utf-8")

        result = poll_dashboard_command(cmd_file)

        assert result is None

    def test_strips_whitespace(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("  landscape_lock  \n", encoding="utf-8")

        result = poll_dashboard_command(cmd_file)

        assert result == "landscape_lock"


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
        from fun_time.bridge_command_dispatch import BridgeConfig

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

    def test_forwards_omnipause_to_ahk(self, tmp_path):
        runner = self._make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"
        cmd_file.write_text("omnipause_toggle", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            runner.tick()

        # Should NOT dispatch in Python — forwarded to AHK
        mock_dispatch.assert_not_called()
        assert ahk_cmd_file.read_text(encoding="utf-8") == "omnipause_toggle"

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

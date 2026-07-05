from __future__ import annotations

import logging
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

from fun_time.command_dispatch import BridgeConfig, BridgeState, WindowOp
from fun_time.windows_bridge_dispatch_loop import (
    poll_dashboard_commands,
    execute_window_ops,
    write_shared_state,
    read_shared_state,
    detect_sleep_gap,
    DispatchLoopRunner,
)


# HWNDs the runner's role lookups resolve to in these tests: portrait,
# landscape and dashboard by pid; Nau by pid (with an exact-title fallback);
# Genau by title; RFB from the hwnd captured at startup.
NAU_HWND = 2001
PORTRAIT_HWND = 3001
LANDSCAPE_HWND = 4001
DASHBOARD_HWND = 5001
GENAU_HWND = 6001
RFB_HWND = 7777

PID_TO_HWND = {
    200: NAU_HWND,
    300: PORTRAIT_HWND,
    400: LANDSCAPE_HWND,
    500: DASHBOARD_HWND,
}

# Every managed window carries a static topmost flag — True for all except Nau,
# which lives under Genau's HUD in hybrid mode.
TOPMOST_HWNDS = {
    RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, GENAU_HWND, DASHBOARD_HWND,
}


def lookup_pid(pid):
    return PID_TO_HWND.get(pid, 0)


def lookup_title(title, exact=False):
    return GENAU_HWND if title == "Genau" and not exact else 0


def make_config(tmp_path, **overrides) -> BridgeConfig:
    settings = dict(
        portrait_port=9091,
        landscape_port=9092,
        vlc_password="test",
        favs_file=tmp_path / "favs.txt",
        weird_dir=tmp_path / "weird",
        state_dir=tmp_path,
        primary_sources="",
        portrait_sources="",
        landscape_sources="",
        genau_mode_file=tmp_path / "rh_mode.txt",
        genau_cmd_file=tmp_path / "rh_cmd.txt",
        genau_paused_file=tmp_path / "rh_paused.txt",
        audio_paused_file=tmp_path / "audio_paused.txt",
        nau_cmd_file=tmp_path / "nau_cmd.txt",
        nau_paused_file=tmp_path / "nau_paused.txt",
        nau_status_file=tmp_path / "nau_status.txt",
        dashboard_state_file=tmp_path / "dashboard_state.ini",
        broker_heartbeat_file=tmp_path / "broker_heartbeat.txt",
    )
    settings.update(overrides)
    return BridgeConfig(**settings)


def make_runner(tmp_path, *, config=None, **kwargs) -> DispatchLoopRunner:
    settings = dict(
        nau_pid=200,
        portrait_pid=300,
        landscape_pid=400,
        dashboard_pid=500,
        dashboard_enabled=False,
    )
    settings.update(kwargs)
    return DispatchLoopRunner(
        config=config or make_config(tmp_path),
        dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
        shared_state_file=tmp_path / "shared_state.ini",
        ahk_cmd_file=tmp_path / "ahk_cmd.txt",
        **settings,
    )


class TestDetectSleepGap:
    def test_reports_gap_when_stall_exceeds_threshold(self):
        # The dispatch thread freezes during system sleep; a wall-clock jump
        # far above the tick cadence means we just woke.
        assert detect_sleep_gap(1000.0, 1000.0 + 300, threshold_s=90.0) == 300

    def test_no_gap_for_normal_iteration(self):
        assert detect_sleep_gap(1000.0, 1000.05) is None

    def test_default_threshold_ignores_a_slow_vlc_tick(self):
        # A wholly unresponsive VLC can stall one tick ~40s (8 HTTP calls at a
        # 5s timeout). The default threshold must clear that, not misread it as
        # a wake.
        assert detect_sleep_gap(1000.0, 1000.0 + 40) is None


class TestRunLoopWakeLogging:
    def test_run_logs_warning_after_wall_clock_jump(self, tmp_path, caplog):
        """A wall-clock jump between iterations is logged so the next
        resume-after-idle failure is anchored to a confirmed sleep/wake."""
        runner = make_runner(tmp_path)
        ticks = {"n": 0}

        def fake_tick():
            ticks["n"] += 1
            if ticks["n"] >= 2:
                runner.stop()

        # last_wall init, iter-1 now (no jump), iter-2 now (1000s jump).
        scripted = [1000.0, 1000.0, 2000.0]
        cursor = {"i": 0}

        def fake_time():
            i = cursor["i"]
            if i < len(scripted):
                cursor["i"] += 1
                return scripted[i]
            return scripted[-1]  # logging's own time.time() calls land here

        with patch.object(runner, "tick", side_effect=fake_tick), \
             patch("fun_time.windows_bridge_dispatch_loop.time.time", side_effect=fake_time), \
             caplog.at_level(logging.WARNING, logger="fun_time.windows_bridge_dispatch_loop"):
            runner.run()

        assert any(
            "sleep" in r.message.lower() or "stall" in r.message.lower()
            for r in caplog.records
        ), "expected a wake/stall warning after the wall-clock jump"


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
        ops = [WindowOp(op="set_topmost", title="Genau", value=True)]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=12345), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as mock_topmost:
            remaining = execute_window_ops(ops, nau_pid=1)

        mock_topmost.assert_called_once_with(12345, True)
        assert remaining == []

    def test_activate_calls_win32(self, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        ops = [WindowOp(op="activate", title="Genau")]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=12345), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window") as mock_activate:
            remaining = execute_window_ops(ops, nau_pid=1)

        mock_activate.assert_called_once_with(12345)
        assert remaining == []

    def test_show_calls_win32(self, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        ops = [WindowOp(op="show", title="Genau")]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=12345), \
             patch("fun_time.windows_bridge_dispatch_loop.show_window") as mock_show:
            remaining = execute_window_ops(ops, nau_pid=1)

        mock_show.assert_called_once_with(12345)
        assert remaining == []

    def test_hide_calls_win32(self):
        ops = [WindowOp(op="hide", title="Genau")]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=12345), \
             patch("fun_time.windows_bridge_dispatch_loop.hide_window") as mock_hide:
            remaining = execute_window_ops(ops, nau_pid=1)

        mock_hide.assert_called_once_with(12345)
        assert remaining == []

    def test_suspend_returned_as_remaining(self):
        """suspend_hotkeys can only be done by AHK — returned for forwarding."""
        ops = [WindowOp(op="suspend_hotkeys")]
        remaining = execute_window_ops(ops, nau_pid=1)

        assert len(remaining) == 1
        assert remaining[0].op == "suspend_hotkeys"

    def test_unsuspend_returned_as_remaining(self):
        ops = [WindowOp(op="unsuspend_hotkeys")]
        remaining = execute_window_ops(ops, nau_pid=1)

        assert len(remaining) == 1
        assert remaining[0].op == "unsuspend_hotkeys"

    def test_send_key_uses_pid(self):
        ops = [WindowOp(op="send_key", key="p")]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=99), \
             patch("fun_time.windows_bridge_dispatch_loop.send_key_to_window") as mock_send:
            remaining = execute_window_ops(ops, nau_pid=42)

        mock_send.assert_called_once_with(99, "p")
        assert remaining == []

    def test_send_vk_uses_pid(self):
        ops = [WindowOp(op="send_vk", vk=0x25)]  # VK_LEFT
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=99), \
             patch("fun_time.windows_bridge_dispatch_loop.send_vk_to_window") as mock_send:
            remaining = execute_window_ops(ops, nau_pid=42)

        mock_send.assert_called_once_with(99, 0x25)
        assert remaining == []

    def test_skips_op_when_window_not_found(self):
        ops = [WindowOp(op="set_topmost", title="Nonexistent", value=True)]
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as mock_topmost:
            remaining = execute_window_ops(ops, nau_pid=1)

        mock_topmost.assert_not_called()
        assert remaining == []

    def test_disable_all_topmost_returned_as_remaining(self):
        ops = [WindowOp(op="disable_all_topmost")]
        remaining = execute_window_ops(ops, nau_pid=1)

        assert len(remaining) == 1
        assert remaining[0].op == "disable_all_topmost"

    def test_restore_all_topmost_returned_as_remaining(self):
        ops = [WindowOp(op="restore_all_topmost")]
        remaining = execute_window_ops(ops, nau_pid=1)

        assert len(remaining) == 1
        assert remaining[0].op == "restore_all_topmost"

    def test_role_ops_returned_as_remaining(self):
        """show_role/hide_role/activate_role resolve against the runner's
        role cache, not window titles — execute_window_ops must hand them
        back untouched.  Dropping them here silently broke mode switches
        once (the title-less ops fell through the title branch)."""
        ops = [
            WindowOp(op="show_role", key="nau"),
            WindowOp(op="activate_role", key="nau"),
            WindowOp(op="hide_role", key="genau"),
        ]
        remaining = execute_window_ops(ops, nau_pid=1)

        assert remaining == ops

    def test_open_rfb_tab_returned_as_remaining(self):
        ops = [WindowOp(op="open_rfb_tab", key="https://example.com")]
        remaining = execute_window_ops(ops, nau_pid=1)

        assert len(remaining) == 1
        assert remaining[0].op == "open_rfb_tab"
        assert remaining[0].key == "https://example.com"


class TestSharedState:
    def test_write_then_read_roundtrip(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(
            locked2=True,
            locked3=False,
            primary_mode="genau",
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
    def test_dispatches_dashboard_command(self, tmp_path):
        # Use huge sync interval so genau sync doesn't fire
        runner = make_runner(tmp_path, sync_interval_ms=999999)
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

    def test_nudge_dispatches_to_command(self, tmp_path):
        """Nau owns the primary display in every mode it appears, so a nudge
        dispatches to Nau's SEEK command (which stacks against its live clock)."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        (tmp_path / "dashboard_cmd.txt").write_text("primary_nudge_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]):
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "primary_nudge_next" in commands

    def test_omnipause_enter_via_tick_drops_topmost_on_managed_windows(self, tmp_path):
        """Entering omnipause frees the desktop: every window with a True
        static topmost flag leaves the TOPMOST band; Nau (static False) is
        never touched."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        (tmp_path / "dashboard_cmd.txt").write_text("omnipause_toggle", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []

        with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert runner.state.omni_paused is True
        assert {h for h, v in topmost_calls if v is False} == TOPMOST_HWNDS
        assert NAU_HWND not in {h for h, _ in topmost_calls}

    def test_omnipause_leave_via_tick_restores_topmost_and_refocuses_primary_player(
        self, tmp_path, monkeypatch,
    ):
        """Leaving omnipause gives every True-flagged window its TOPMOST bit
        back (Nau, static-False, is never promoted) and re-activates the window
        that owns the primary display — Nau in nau mode."""
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        runner.state = BridgeState(omni_paused=True)
        (tmp_path / "dashboard_cmd.txt").write_text("omnipause_toggle", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []
        activated: list[int] = []

        with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.is_window_topmost", return_value=False), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window", side_effect=activated.append), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert runner.state.omni_paused is False
        assert {h for h, v in topmost_calls if v is True} == TOPMOST_HWNDS
        assert NAU_HWND not in {h for h, _ in topmost_calls}
        assert activated == [NAU_HWND]

    def test_omnipause_toggle_updates_state_and_writes_shared_state(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omnipause_toggle", encoding="utf-8")

        with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"):
            runner.tick()

        assert runner.state.omni_paused is True
        loaded = read_shared_state(tmp_path / "shared_state.ini")
        assert loaded is not None
        assert loaded.omni_paused is True

    def test_backslash_key_dispatches_quarter_button_in_genau_mode(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(primary_mode="genau")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]):
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "quarter_button" in commands

    def test_backslash_key_sends_quarter_button_press_in_genau_mode(self, tmp_path):
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.settimeout(1.0)
        port = recv_sock.getsockname()[1]
        port_file = tmp_path / "dashboard_press_port.txt"
        port_file.write_text(str(port), encoding="utf-8")

        runner = make_runner(tmp_path, sync_interval_ms=999999, dashboard_enabled=True)
        runner._last_sync = float("inf")
        runner.state = BridgeState(primary_mode="genau")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]):
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        # Collect all UDP messages
        messages = []
        while True:
            try:
                data, _ = recv_sock.recvfrom(256)
                messages.append(data.decode("utf-8"))
            except OSError:
                break
        recv_sock.close()
        assert "quarter_button" in messages

    def test_backslash_key_sends_open_file_dialog_press_in_nau_mode(self, tmp_path):
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.settimeout(1.0)
        port = recv_sock.getsockname()[1]
        port_file = tmp_path / "dashboard_press_port.txt"
        port_file.write_text(str(port), encoding="utf-8")

        runner = make_runner(tmp_path, sync_interval_ms=999999, dashboard_enabled=True)
        runner._last_sync = float("inf")
        runner.state = BridgeState(primary_mode="nau")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None):
            runner.tick()
            time.sleep(0.15)

        messages = []
        while True:
            try:
                data, _ = recv_sock.recvfrom(256)
                messages.append(data.decode("utf-8"))
            except OSError:
                break
        recv_sock.close()
        assert "open_file_dialog" in messages

    def test_backslash_key_enters_omnipause_when_not_in_genau_mode(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(primary_mode="nau")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None):
            runner.tick()
            time.sleep(0.15)  # background thread needs a moment

        assert runner.state.omni_paused is False  # leaves omnipause after dialog closes

    def test_dispatch_forwards_remaining_ops_to_ahk(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
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
        runner = make_runner(tmp_path, sync_interval_ms=999999)
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
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        unsuspend_op = WindowOp(op="unsuspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[unsuspend_op]):
            mock_dispatch.return_value = (runner.state, [unsuspend_op])
            runner._dispatch("some_command")

        assert ahk_cmd_file.read_text(encoding="utf-8") == "unsuspend_hotkeys"

    def test_dispatch_writes_tooltip_with_message_to_ahk_cmd_file(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        tooltip_op = WindowOp(op="tooltip", key="Clipper: MyVideo")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[tooltip_op]):
            mock_dispatch.return_value = (runner.state, [tooltip_op])
            runner._dispatch("some_command")

        assert ahk_cmd_file.read_text(encoding="utf-8") == "tooltip Clipper: MyVideo"

    def test_sync_tick_calls_update_dashboard_when_enabled(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=100, dashboard_enabled=True)
        runner._last_sync = -999

        with patch.object(runner, "_update_dashboard") as mock_update:
            runner.tick()

        mock_update.assert_called_once()

    def test_reads_shared_state_at_tick_start(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        assert runner.state.omni_paused is False

        # Simulate AHK dispatch updating shared state file
        write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))
        runner.tick()

        assert runner.state.omni_paused is True

    def test_writes_shared_state_after_dispatch(self, tmp_path):
        runner = make_runner(tmp_path)
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

    def test_quit_command_writes_exit_to_ahk(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("quit", encoding="utf-8")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        runner.tick()

        assert ahk_cmd_file.read_text(encoding="utf-8") == "exit"

    def test_omniminimize_minimizes_only_mode_visible_windows(self, tmp_path):
        """omniminimize minimizes the windows the current mode shows, without
        stealing focus.  In nau mode the hidden slot-mate (Genau) is NOT
        minimized — SW_MINIMIZE would drag a hidden window back into view."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omniminimize", encoding="utf-8")

        minimized: list[tuple[int, dict]] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window", side_effect=lambda h, **kw: minimized.append((h, kw))):
            runner.tick()

        assert {h for h, _ in minimized} == {
            RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND, NAU_HWND,
        }
        # Minimized without activation so focus isn't yanked between windows.
        assert all(kw.get("activate") is False for _, kw in minimized)

    def test_omniminimize_in_hybrid_includes_nau_and_genau(self, tmp_path):
        """Hybrid shows Nau under Genau's HUD (Genau drives the OSR2)."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        runner.state = BridgeState(primary_mode="hybrid")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omniminimize", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()

        assert set(minimized) == {
            RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND,
            NAU_HWND, GENAU_HWND,
        }

    def test_omniminimize_skips_windows_that_are_not_found(self, tmp_path):
        """Windows whose lookup returns 0 are skipped — no minimize call for them."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=0)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omniminimize", encoding="utf-8")

        minimized: list[int] = []
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()

        assert minimized == []

    def test_omnirestore_restores_exactly_the_minimized_windows(self, tmp_path):
        """omnirestore un-minimizes the windows omniminimize minimized — no
        more (a second omnirestore is a no-op), no less, never activating."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"

        restored: list[tuple[int, dict]] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window", side_effect=lambda h, **kw: restored.append((h, kw))):
            cmd_file.write_text("omniminimize", encoding="utf-8")
            runner.tick()
            minimized_hwnds = list(runner._minimized_hwnds)

            cmd_file.write_text("omnirestore", encoding="utf-8")
            runner.tick()

            assert [h for h, _ in restored] == minimized_hwnds
            assert all(kw.get("activate") is False for _, kw in restored)
            assert runner._minimized_hwnds == []

            # The minimized set was consumed: another omnirestore does nothing.
            cmd_file.write_text("omnirestore", encoding="utf-8")
            runner.tick()

        assert [h for h, _ in restored] == minimized_hwnds

    def test_sends_press_via_udp_on_button_command(self, tmp_path):
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.settimeout(1.0)
        port = recv_sock.getsockname()[1]
        port_file = tmp_path / "dashboard_press_port.txt"
        port_file.write_text(str(port), encoding="utf-8")

        runner = make_runner(tmp_path, sync_interval_ms=999999, dashboard_enabled=True)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_lock", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]):
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        data, _ = recv_sock.recvfrom(256)
        recv_sock.close()
        assert data.decode("utf-8") == "portrait_lock"

    def test_udp_press_skipped_when_no_port_file(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999, dashboard_enabled=True)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_lock", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]):
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()  # should not raise

    def test_voice_off_mutes_voice_controller(self, tmp_path):
        from fun_time.voice_control import VoiceController

        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        vc = VoiceController(cmd_file=tmp_path / "vc_cmd.txt", model_path="unused")
        runner.voice_controller = vc
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("voice_off", encoding="utf-8")

        runner.tick()

        assert vc.is_muted

    def test_voice_toggle_unmutes_when_muted(self, tmp_path):
        from fun_time.voice_control import VoiceController

        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        vc = VoiceController(cmd_file=tmp_path / "vc_cmd.txt", model_path="unused")
        vc.mute()
        runner.voice_controller = vc
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("voice_toggle", encoding="utf-8")

        runner.tick()

        assert not vc.is_muted

    def test_voice_toggle_mutes_when_not_muted(self, tmp_path):
        from fun_time.voice_control import VoiceController

        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        vc = VoiceController(cmd_file=tmp_path / "vc_cmd.txt", model_path="unused")
        runner.voice_controller = vc
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("voice_toggle", encoding="utf-8")

        runner.tick()

        assert vc.is_muted


class TestOpenRfbTab:
    def test_open_rfb_tab_op_calls_open_rfb_tab_when_rfb_running(self, tmp_path):
        runner = make_runner(
            tmp_path,
            sync_interval_ms=999999,
            rfb_hwnd=12345,
            rfb_shortcut_target=r"C:\Chrome\chrome.exe",
            rfb_shortcut_work_dir=r"C:\Chrome",
            rfb_shortcut_args='--profile-directory="Profile 2"',
        )
        runner._last_sync = float("inf")

        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[rfb_op]), \
             patch("fun_time.windows_bridge_dispatch_loop.open_rfb_tab") as mock_open:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch("portrait_lock")

        mock_open.assert_called_once_with(
            url="https://example.com",
            shortcut_target=r"C:\Chrome\chrome.exe",
            shortcut_work_dir=r"C:\Chrome",
            shortcut_args='--profile-directory="Profile 2"',
        )

    def test_open_rfb_tab_op_skipped_when_rfb_not_running(self, tmp_path):
        runner = make_runner(
            tmp_path,
            sync_interval_ms=999999,
            rfb_hwnd=0,
            rfb_shortcut_target=r"C:\Chrome\chrome.exe",
            rfb_shortcut_work_dir=r"C:\Chrome",
            rfb_shortcut_args='--profile-directory="Profile 2"',
        )
        runner._last_sync = float("inf")

        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[rfb_op]), \
             patch("fun_time.windows_bridge_dispatch_loop.open_rfb_tab") as mock_open:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch("portrait_lock")

        mock_open.assert_not_called()

    def test_open_rfb_tab_op_skipped_when_no_shortcut_target(self, tmp_path):
        runner = make_runner(
            tmp_path,
            sync_interval_ms=999999,
            rfb_hwnd=12345,
        )
        runner._last_sync = float("inf")

        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[rfb_op]), \
             patch("fun_time.windows_bridge_dispatch_loop.open_rfb_tab") as mock_open:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch("portrait_lock")

        mock_open.assert_not_called()


class TestModeSwitchVisibility:
    """The two primary-slot players (Nau and Genau) share one screen rect.
    A mode switch swaps window VISIBILITY: the incoming player is shown and
    activated BEFORE the outgoing one hides, so focus never falls through to
    another application.

    These tests run the real dispatch_command and the real
    execute_window_ops, pinning the whole path from command string to
    win32 call — including execute_window_ops' pass-through of the
    show_role/activate_role/hide_role ops, whose silent dropping broke
    mode switches once.
    """

    def _run_mode_switch(self, tmp_path, monkeypatch, *, from_mode, command,
                         integration_env=False):
        if integration_env:
            monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        else:
            monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        runner = make_runner(tmp_path)
        runner.state = BridgeState(primary_mode=from_mode)

        calls: list[tuple[str, int]] = []
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.show_window",
                   side_effect=lambda h: calls.append(("show", h))), \
             patch("fun_time.windows_bridge_dispatch_loop.hide_window",
                   side_effect=lambda h: calls.append(("hide", h))), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window",
                   side_effect=lambda h: calls.append(("activate", h))), \
             patch("fun_time.runtime_flow.ensure_playback_state", return_value=True):
            runner._dispatch(command)

        assert runner.state.primary_mode == {
            "genau_activate": "genau", "nau_activate": "nau", "hybrid_activate": "hybrid",
        }[command]
        return calls

    def test_genau_activate_shows_genau_before_hiding_nau(self, tmp_path, monkeypatch):
        calls = self._run_mode_switch(
            tmp_path, monkeypatch, from_mode="nau", command="genau_activate",
        )
        assert calls == [
            ("show", GENAU_HWND),
            ("activate", GENAU_HWND),
            ("hide", NAU_HWND),
        ]

    def test_nau_activate_shows_nau_before_hiding_genau(self, tmp_path, monkeypatch):
        calls = self._run_mode_switch(
            tmp_path, monkeypatch, from_mode="genau", command="nau_activate",
        )
        assert calls == [
            ("show", NAU_HWND),
            ("activate", NAU_HWND),
            ("hide", GENAU_HWND),
        ]

    def test_hybrid_activate_shows_nau_and_genau(self, tmp_path, monkeypatch):
        calls = self._run_mode_switch(
            tmp_path, monkeypatch, from_mode="nau", command="hybrid_activate",
        )
        assert calls == [
            ("show", NAU_HWND),
            ("show", GENAU_HWND),
            ("activate", GENAU_HWND),
        ]

    def test_hybrid_to_genau_hides_nau(self, tmp_path, monkeypatch):
        """Hybrid and Genau differ only in Nau's visibility, so the transition
        must still swap windows.  Regression — a guard that compared
        genau_active() instead of the mode missed this pair."""
        calls = self._run_mode_switch(
            tmp_path, monkeypatch, from_mode="hybrid", command="genau_activate",
        )
        assert calls == [
            ("show", GENAU_HWND),
            ("activate", GENAU_HWND),
            ("hide", NAU_HWND),
        ]

    def test_activation_suppressed_during_integration_runs(self, tmp_path, monkeypatch):
        """FUN_TIME_RUN_INTEGRATION=1 keeps mode switches from stealing the
        real desktop's focus; show/hide still happen."""
        calls = self._run_mode_switch(
            tmp_path, monkeypatch, from_mode="nau", command="genau_activate",
            integration_env=True,
        )
        assert calls == [
            ("show", GENAU_HWND),
            ("hide", NAU_HWND),
        ]


class TestResolveRole:
    def test_nau_falls_back_to_exact_title_when_pid_fails(self, tmp_path):
        """The venv pythonw launcher's PID differs from the interpreter that
        owns the SDL window, so resolution must fall back to an exact-title
        lookup — exact because 'Nau' is a substring of 'Genau'."""
        runner = make_runner(tmp_path)

        title_calls: list[tuple[str, bool]] = []

        def title_lookup(title, exact=False):
            title_calls.append((title, exact))
            return 2002 if (title == "Nau" and exact) else 0

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=title_lookup):
            hwnd = runner._resolve_role("nau")

        assert ("Nau", True) in title_calls, "must try the exact-title fallback"
        assert hwnd == 2002

    def test_dashboard_falls_back_to_title_when_pid_fails(self, tmp_path):
        """When find_window_by_pid cannot find the Dashboard (PID mismatch
        from the venv launcher), resolution falls back to its title."""
        runner = make_runner(tmp_path)

        def title_lookup(title, exact=False):
            return 9999 if title == "Fun Time" else 0

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=title_lookup):
            assert runner._resolve_role("dashboard") == 9999

    def test_cached_hwnd_survives_hiding_and_show_role_reaches_it(self, tmp_path):
        """Hidden windows are invisible to the pid/title lookups, so the
        HWND captured while a window was visible must be cached and reused —
        otherwise a hidden slot-mate could never be shown again."""
        runner = make_runner(tmp_path)

        # Nau is visible: the pid lookup finds it once, populating the cache.
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid",
                   side_effect=lookup_pid):
            assert runner._resolve_role("nau") == NAU_HWND

        # Nau is now hidden: every lookup fails, but the cache still answers,
        # and a show_role op dispatched for it reaches the cached hwnd.
        shown: list[int] = []
        show_op = WindowOp(op="show_role", key="nau")
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.show_window", side_effect=shown.append), \
             patch("fun_time.windows_bridge_dispatch_loop.dispatch_command",
                   return_value=(runner.state, [show_op])):
            assert runner._resolve_role("nau") == NAU_HWND
            runner._dispatch("nau_activate")

        assert shown == [NAU_HWND]


class TestStaticTopmost:
    """Windows never stack (every managed window has its own screen rect),
    so each role carries a STATIC topmost flag: True for everything except
    Nau, which lives under Genau's transparent HUD and must never rise above
    it — in any mode."""

    def _topmost_calls(self, runner, method_name):
        calls: list[tuple[int, bool]] = []
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda h, v: calls.append((h, v))):
            getattr(runner, method_name)()
        return calls

    def test_remove_all_topmost_drops_every_true_flagged_role(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)

        calls = self._topmost_calls(runner, "_remove_all_topmost")

        assert {h for h, v in calls if v is False} == TOPMOST_HWNDS
        assert NAU_HWND not in {h for h, _ in calls}

    def test_restore_all_topmost_is_mode_independent_and_never_touches_nau(self, tmp_path):
        """The flags do not vary with the mode: in hybrid Nau is VISIBLE yet
        still stays out of the TOPMOST band."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(primary_mode="hybrid")

        calls = self._topmost_calls(runner, "_restore_all_topmost")

        assert {h for h, v in calls if v is True} == TOPMOST_HWNDS
        assert NAU_HWND not in {h for h, _ in calls}


class TestHandleOpenFileDialog:
    """Tests for the open_file_dialog command that migrates
    AHK's OpenPrimaryVlcFileDialogWithManagedOmniPause to Python.
    """

    def test_enters_omnipause_when_not_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        dispatched = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "enter_omnipause" in dispatched

    def test_removes_topmost_from_true_flagged_windows(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        exec_returns = iter([
            [WindowOp(op="disable_all_topmost")],
            [WindowOp(op="restore_all_topmost")],
        ])

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", side_effect=exec_returns), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        removed = {h for h, v in topmost_calls if not v}
        assert removed == TOPMOST_HWNDS
        assert NAU_HWND not in {h for h, _ in topmost_calls}

    def test_shows_file_dialog_with_primary_sources_dir(self, tmp_path):
        """Shows our own file dialog with the first primary_sources directory."""
        config = make_config(tmp_path, primary_sources=r"C:\videos\2D\non_AI|C:\other")
        runner = make_runner(tmp_path, config=config)
        runner.state = BridgeState(omni_paused=False)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None) as mock_dialog:
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        mock_dialog.assert_called_once_with(r"C:\videos\2D\non_AI", owner_hwnd=NAU_HWND)

    def test_sends_selected_file_to_nau_by_default(self, tmp_path):
        """In nau mode (the default) a selected file becomes a Nau PLAY_FILE
        command, paired with its mirrored funscript when one exists."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        video = tmp_path / "videos" / "videos" / "movie.mp4"
        video.parent.mkdir(parents=True)
        video.write_text("x", encoding="utf-8")
        mirrored = tmp_path / "videos" / "scripts" / "scripts" / "movie.funscript"
        mirrored.parent.mkdir(parents=True)
        mirrored.write_text("{}", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=str(video)):
            mock_dispatch.side_effect = lambda cmd, state, config: (state, [])
            runner._handle_open_file_dialog()

        command = runner.config.nau_cmd_file.read_text(encoding="utf-8")
        assert command == f"PLAY_FILE {video}\t{mirrored}"

    def test_sends_selected_file_to_nau_in_hybrid(self, tmp_path):
        """Hybrid displays Nau, so a selected file becomes a Nau PLAY_FILE
        command there too (no funscript pairing when none exists)."""
        config = make_config(tmp_path, primary_sources=r"C:\videos")
        runner = make_runner(tmp_path, config=config)
        runner.state = BridgeState(omni_paused=False, primary_mode="hybrid")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=r"C:\videos\movie.mp4"):
            mock_dispatch.side_effect = lambda cmd, state, config: (state, [])
            runner._handle_open_file_dialog()

        assert runner.config.nau_cmd_file.read_text(encoding="utf-8") == r"PLAY_FILE C:\videos\movie.mp4"

    def test_does_not_play_anything_on_cancel(self, tmp_path):
        """When the user cancels the dialog, nothing is sent to Nau."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        assert not runner.config.nau_cmd_file.exists()

    def test_restores_topmost_in_finally(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        exec_returns = iter([
            [WindowOp(op="disable_all_topmost")],
            [WindowOp(op="restore_all_topmost")],
        ])

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", side_effect=exec_returns), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        dispatched = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "leave_omnipause" in dispatched

        # Every True-flagged window gets its TOPMOST bit back; Nau (static
        # False) is never touched.
        restored = {h for h, v in topmost_calls if v}
        assert restored == TOPMOST_HWNDS

    def test_never_restores_nau_topmost_even_in_genau_mode(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False, primary_mode="genau")

        topmost_calls = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        exec_returns = iter([
            [WindowOp(op="disable_all_topmost")],
            [WindowOp(op="restore_all_topmost")],
        ])

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", side_effect=exec_returns), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None):
            mock_dispatch.return_value = (BridgeState(omni_paused=True, primary_mode="genau"), [])
            runner._handle_open_file_dialog()

        # The static flags are mode-independent: Nau never gets a topmost flag.
        restored = {h for h, v in topmost_calls if v}
        assert restored == TOPMOST_HWNDS
        assert NAU_HWND not in {h for h, _ in topmost_calls}

    def test_topmost_removed_before_dialog(self, tmp_path):
        """Topmost removal happens before showing the file dialog."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        call_log: list[str] = []

        exec_returns = iter([
            [WindowOp(op="disable_all_topmost")],
            [WindowOp(op="restore_all_topmost")],
        ])

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", side_effect=exec_returns), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=lambda h, v: call_log.append(f"topmost_{v}")), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", side_effect=lambda d, **kw: (call_log.append("dialog"), None)[-1]):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        assert "dialog" in call_log
        first_remove = next(i for i, c in enumerate(call_log) if c == "topmost_False")
        assert first_remove < call_log.index("dialog")

    def test_skips_omnipause_when_already_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=NAU_HWND), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as mock_topmost, \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None) as mock_dialog:
            runner._handle_open_file_dialog()

        # Should not dispatch enter/leave omnipause
        mock_dispatch.assert_not_called()
        # Should not touch topmost
        mock_topmost.assert_not_called()
        # Should still show the file dialog owned by the Nau window
        mock_dialog.assert_called_once_with("", owner_hwnd=NAU_HWND)

    def test_shows_dialog_with_empty_dir_when_no_primary_sources(self, tmp_path):
        """When primary_sources is empty, dialog opens with empty initial dir."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.execute_window_ops", return_value=[]), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None) as mock_dialog:
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        mock_dialog.assert_called_once_with("", owner_hwnd=0)

    def test_forwards_suspend_and_unsuspend_via_dispatch(self, tmp_path, monkeypatch):
        """Suspend/unsuspend reach AHK via _dispatch forwarding remaining ops."""
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        runner = make_runner(tmp_path)
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
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None), \
             patch.object(Path, "write_text", capture_write):
            mock_dispatch.return_value = (BridgeState(omni_paused=True), [])
            runner._handle_open_file_dialog()

        assert "suspend_hotkeys" in ahk_commands_written
        assert "unsuspend_hotkeys" in ahk_commands_written
        assert ahk_commands_written.index("suspend_hotkeys") < ahk_commands_written.index("unsuspend_hotkeys")

    def test_concurrent_invocations_prevented(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)  # fast path — no omnipause

        with patch("fun_time.windows_bridge_dispatch_loop.show_open_file_dialog", return_value=None):
            original_handle = runner._handle_open_file_dialog

            barrier = threading.Barrier(2, timeout=2.0)

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

        # The lock should prevent truly concurrent execution
        assert hasattr(runner, "_file_dialog_lock")

    def test_open_file_dialog_routed_from_tick(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("open_file_dialog", encoding="utf-8")

        with patch.object(runner, "_handle_open_file_dialog") as mock_handle:
            runner.tick()
            # Give the background thread a moment to start
            time.sleep(0.1)

        mock_handle.assert_called_once()


class TestUpdateDashboardOsr2Off:
    """_update_dashboard should write osr2_mode='off' when the device is off."""

    def _read_osr2_mode(self, tmp_path):
        import configparser
        ini = tmp_path / "dashboard_state.ini"
        parser = configparser.ConfigParser()
        parser.read_string(ini.read_text(encoding="utf-16"))
        return parser.get("osr2", "mode")

    def test_osr2_mode_off_when_rx_file_missing(self, tmp_path):
        runner = make_runner(tmp_path)
        # No osr2_serial_rx.txt exists

        runner._update_dashboard()

        assert self._read_osr2_mode(tmp_path) == "off"

    def test_osr2_mode_off_when_rx_timestamp_stale(self, tmp_path):
        runner = make_runner(tmp_path)
        rx_file = tmp_path / "osr2_serial_rx.txt"
        rx_file.write_text("100.0", encoding="utf-8")

        with patch("fun_time.dashboard_runtime.time") as mock_time:
            mock_time.time.return_value = 200.0  # 100s stale
            runner._update_dashboard()

        assert self._read_osr2_mode(tmp_path) == "off"

    def test_osr2_mode_controlled_when_device_on(self, tmp_path):
        runner = make_runner(tmp_path)
        rx_file = tmp_path / "osr2_serial_rx.txt"
        rx_file.write_text("100.0", encoding="utf-8")

        with patch("fun_time.dashboard_runtime.time") as mock_time:
            mock_time.time.return_value = 110.0  # 10s ago — fresh
            runner._update_dashboard()

        assert self._read_osr2_mode(tmp_path) == "controlled"

    def test_osr2_mode_auto_when_device_on_and_genau(self, tmp_path):
        runner = make_runner(tmp_path)
        rx_file = tmp_path / "osr2_serial_rx.txt"
        rx_file.write_text("100.0", encoding="utf-8")
        (tmp_path / "rh_mode.txt").write_text("1", encoding="utf-8")

        with patch("fun_time.dashboard_runtime.time") as mock_time:
            mock_time.time.return_value = 110.0
            runner._update_dashboard()

        assert self._read_osr2_mode(tmp_path) == "auto"


# ---------------------------------------------------------------------------
# Idempotent voice commands
# ---------------------------------------------------------------------------

class TestIdempotentVoiceCommands:
    """Tests for idempotent command variants used by voice control.

    Each command checks current state before acting — saying "pause" while
    already paused is a no-op, not an unpause.
    """

    # -- pause / play --

    def test_pause_enters_omnipause_when_not_paused(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(omni_paused=False)
        with patch.object(runner, "_handle_omnipause_toggle") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("pause", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_toggle.assert_called_once()

    def test_pause_noop_when_already_paused(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(omni_paused=True)
        with patch.object(runner, "_handle_omnipause_toggle") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("pause", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_toggle.assert_not_called()

    def test_play_leaves_omnipause_when_paused(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(omni_paused=True)
        with patch.object(runner, "_handle_omnipause_toggle") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("play", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_toggle.assert_called_once()

    def test_play_noop_when_not_paused(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(omni_paused=False)
        with patch.object(runner, "_handle_omnipause_toggle") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("play", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_toggle.assert_not_called()

    # -- enter_omnipause (Space hotkey) --

    def test_enter_omnipause_enters_when_not_paused(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(omni_paused=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("enter_omnipause", encoding="utf-8")

        with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"):
            runner.tick()

        assert runner.state.omni_paused is True

    def test_enter_omnipause_removes_topmost(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        runner.state = BridgeState(omni_paused=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("enter_omnipause", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []

        with patch("fun_time.runtime_flow.ensure_playback_state", return_value=True), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert {h for h, v in topmost_calls if v is False} == TOPMOST_HWNDS
        assert NAU_HWND not in {h for h, _ in topmost_calls}

    def test_enter_omnipause_noop_when_already_paused(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(omni_paused=True)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("enter_omnipause", encoding="utf-8")

        with patch.object(runner, "_dispatch") as mock_d:
            runner.tick()

        mock_d.assert_not_called()

    # -- lock portrait / lock landscape --

    def test_portrait_lock_on_dispatches_when_unlocked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked2=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_on", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("portrait_lock")

    def test_portrait_lock_on_noop_when_locked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked2=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_on", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_not_called()

    def test_landscape_lock_on_dispatches_when_unlocked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked3=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("landscape_lock_on", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("landscape_lock")

    def test_landscape_lock_on_noop_when_locked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked3=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("landscape_lock_on", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_not_called()

    # -- fmode on / fmode off --

    def test_fmode_on_dispatches_when_disabled(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(f_mode_enabled=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("fmode_on", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("fmode_toggle")

    def test_fmode_on_noop_when_enabled(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(f_mode_enabled=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("fmode_on", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_not_called()

    def test_fmode_off_dispatches_when_enabled(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(f_mode_enabled=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("fmode_off", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("fmode_toggle")

    def test_fmode_off_noop_when_disabled(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(f_mode_enabled=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("fmode_off", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_not_called()

    # -- genau activate --

    def test_genau_activate_dispatches_when_not_in_genau_mode(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(primary_mode="nau")
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("genau_activate")

    def test_genau_activate_dispatches_in_hybrid_mode(self, tmp_path):
        """Hybrid mode is genau-active but is NOT genau mode: the Genau-mode
        button must still switch to full Genau.  Regression — the old guard
        used genau_active(), which is True for hybrid, so it swallowed this."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(primary_mode="hybrid")
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("genau_activate")

    def test_genau_activate_dispatches_when_already_in_genau_mode(self, tmp_path):
        """The loop forwards genau_activate unconditionally — switching to the
        mode you are already in is a no-op at the planner level (see
        test_mode_plan.test_same_mode_is_noop), not a special case here."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(primary_mode="genau")
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("genau_activate")

    # -- lock off (idempotent unlock) --

    def test_portrait_lock_off_unlocks_when_locked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked2=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_off", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("portrait_lock")

    def test_portrait_lock_off_noop_when_already_unlocked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked2=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_off", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_not_called()

    def test_landscape_lock_off_unlocks_when_locked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked3=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("landscape_lock_off", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("landscape_lock")

    def test_landscape_lock_off_noop_when_already_unlocked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked3=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("landscape_lock_off", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_not_called()

    # -- broker start / broker stop --

    def test_broker_start_starts_when_not_running(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        # No heartbeat file → broker not running
        with patch("fun_time.windows_bridge_dispatch_loop.restart_broker") as mock_restart:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_start", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
            time.sleep(0.2)  # daemon thread
        mock_restart.assert_called_once()

    def test_broker_start_noop_when_already_running(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        # Fresh heartbeat → broker running
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")
        with patch("fun_time.windows_bridge_dispatch_loop.restart_broker") as mock_restart:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_start", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_restart.assert_not_called()

    def test_broker_stop_stops_when_running(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")
        with patch("fun_time.windows_bridge_dispatch_loop.stop_broker_processes") as mock_stop:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_stop", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
            time.sleep(0.2)  # daemon thread
        mock_stop.assert_called_once()

    def test_broker_stop_noop_when_not_running(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        # No heartbeat file → broker not running
        with patch("fun_time.windows_bridge_dispatch_loop.stop_broker_processes") as mock_stop:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_stop", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_stop.assert_not_called()


class TestWatchTracking:
    """The runner samples both satellites ~1 Hz and turns transitions into
    watch-stats events, with user nav commands marking skips."""

    def _run_samples(self, runner, monkeypatch, timeline):
        """Drive tick() through (t, portrait_path, portrait_fraction) samples."""
        current = {"value": ("", None)}
        fake_now = {"t": 0.0}
        monkeypatch.setattr(
            "fun_time.windows_bridge_dispatch_loop.time.monotonic", lambda: fake_now["t"]
        )

        def fake_path(port, pw):
            return current["value"][0] if port == 9091 else ""

        def fake_fraction(port, pw):
            return current["value"][1] if port == 9091 else None

        monkeypatch.setattr(
            "fun_time.windows_bridge_dispatch_loop.get_current_file_path", fake_path
        )
        monkeypatch.setattr(
            "fun_time.windows_bridge_dispatch_loop.get_playback_fraction", fake_fraction
        )
        for t, path, fraction in timeline:
            fake_now["t"] = t
            current["value"] = (path, fraction)
            runner.tick()

    def test_tick_records_a_completion_for_a_fully_watched_video(self, tmp_path, monkeypatch):
        from fun_time.media_metadata import normalize_path_key
        from fun_time.watch_stats import load_watch_stats

        runner = make_runner(tmp_path)
        a = tmp_path / "a.mp4"
        a.write_text("x", encoding="utf-8")

        self._run_samples(runner, monkeypatch, [
            (100.0, str(a), 0.1),
            (101.1, str(a), 0.9),
            (102.2, "", None),          # unreadable sample is ignored
            (103.3, str(tmp_path / "b.mp4"), 0.0),
        ])

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(a))]["completions"] == 1

    def test_user_next_marks_an_early_departed_video_as_skipped(self, tmp_path, monkeypatch):
        from fun_time.media_metadata import normalize_path_key
        from fun_time.watch_stats import load_watch_stats

        runner = make_runner(tmp_path)
        a = tmp_path / "a.mp4"
        a.write_text("x", encoding="utf-8")

        with (
            patch("fun_time.command_dispatch.vlc_nav_step", return_value=True),
            patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
        ):
            current = {"value": (str(a), 0.2)}
            fake_now = {"t": 100.0}
            monkeypatch.setattr(
                "fun_time.windows_bridge_dispatch_loop.time.monotonic", lambda: fake_now["t"]
            )
            monkeypatch.setattr(
                "fun_time.windows_bridge_dispatch_loop.get_current_file_path",
                lambda port, pw: current["value"][0] if port == 9091 else "",
            )
            monkeypatch.setattr(
                "fun_time.windows_bridge_dispatch_loop.get_playback_fraction",
                lambda port, pw: current["value"][1] if port == 9091 else None,
            )
            runner.tick()                       # samples a.mp4 at 20%
            runner._dispatch("portrait_next")   # user skips
            fake_now["t"] = 101.1
            current["value"] = (str(tmp_path / "b.mp4"), 0.0)
            runner.tick()

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(a))]["skips"] == 1

    def test_trash_suppresses_classifying_the_discarded_video(self, tmp_path, monkeypatch):
        from fun_time.watch_stats import load_watch_stats

        config = make_config(tmp_path, weird_dir=tmp_path / "weird")
        runner = make_runner(tmp_path, config=config)
        a = tmp_path / "a.mp4"
        a.write_text("x", encoding="utf-8")

        with (
            patch("fun_time.command_dispatch.get_current_file_path", return_value=str(a)),
            patch("fun_time.command_dispatch.vlc_advance_and_remove", return_value=True),
            patch("fun_time.command_dispatch.ensure_playback_state", return_value=True),
        ):
            current = {"value": (str(a), 0.2)}
            fake_now = {"t": 100.0}
            monkeypatch.setattr(
                "fun_time.windows_bridge_dispatch_loop.time.monotonic", lambda: fake_now["t"]
            )
            monkeypatch.setattr(
                "fun_time.windows_bridge_dispatch_loop.get_current_file_path",
                lambda port, pw: current["value"][0] if port == 9091 else "",
            )
            monkeypatch.setattr(
                "fun_time.windows_bridge_dispatch_loop.get_playback_fraction",
                lambda port, pw: current["value"][1] if port == 9091 else None,
            )
            runner.tick()
            runner._dispatch("portrait_trash")  # moves the file to weird
            fake_now["t"] = 101.1
            current["value"] = (str(tmp_path / "b.mp4"), 0.0)
            runner.tick()

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}


class TestSeededRoleHwnds:
    def test_startup_seed_lets_hidden_windows_be_shown_again(self, tmp_path):
        """Startup hides the primary-slot windows (Nau, Genau) BEFORE the
        dispatch loop ever resolves them, and hidden windows are invisible to
        the pid/title lookups — so the runner must be seeded with the hwnds the
        startup sequencer resolved while everything was still visible, or genau/
        hybrid modes could never bring their windows back."""
        runner = make_runner(
            tmp_path,
            role_hwnds={"genau": 6001, "nau": 2001},
        )
        shown: list[int] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0),              patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0),              patch("fun_time.windows_bridge_dispatch_loop.show_window", side_effect=shown.append):
            assert runner._resolve_role("genau") == 6001
            assert runner._resolve_role("nau") == 2001
            runner._dispatch("hybrid_activate")

        assert shown == [2001, 6001]  # hybrid shows Nau then the Genau HUD

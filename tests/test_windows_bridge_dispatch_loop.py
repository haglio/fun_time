from __future__ import annotations

import contextlib
import json
import logging
import socket
import threading
import time
from dataclasses import replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from app_support.file_channel import read_flag
from app_support.state_files import OSR2_SERIAL_RX
from app_support.threading_utils import wait_until
from funestra.file_channel import publish_whole
from funestra.funestra_status import status_fields
from funestra.hud_title import F_MODE_LABEL
from funestra.main_hud import OSR2_PARKED
from funestra.modes import MainMode, SatellitesMode
from funestra.playlist import PlaylistItem, write_playlist
from funestra.satellite_hud import HudModel, hud_text, parse_hud
from funestra.status import StatusWriter
from funestra.video_groups import normalize_path_key
from voice_core.commands import Recognition
from voice_core.listening import Heard

from fun_time import load_config, windows_bridge_dispatch_loop
from fun_time.arrivals import keep_looking
from fun_time.bridge_records import BridgeConfig, Op, WindowOp
from fun_time.command_value import parse_command
from fun_time.config import SatelliteChannel, VoiceControlConfig
from fun_time.content import load_web_providers
from fun_time.crown import Crown
from fun_time.dashboard_actions import HEADSET_OFF, HEADSET_ON, LIBRARY_OPEN_FILENAME
from fun_time.dashboard_runtime import load_dashboard_snapshot
from fun_time.dispatch_loop_setup import Publishing, SessionFiles, SessionStart
from fun_time.event_log import FAVORITE
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.fun_time_windows import (
    MAIN_BLANK_SETTLE_S,
    ChildPids,
    FunTimeWindows,
)
from fun_time.funestra_handover import STALLED_APP_LET_GO_CEILING_S, keep_aside
from fun_time.hud_transport import HudPublisher
from fun_time.library_browser import BrowseOpening
from fun_time.manifest import (
    LaunchManifest,
    build_windows_bridge_manifest,
    write_manifest_data,
    write_windows_bridge_manifest,
)
from fun_time.media_metadata import (
    is_favorite,
    metadata_path_for,
)
from fun_time.origenerator_switches import RfbMode
from fun_time.regen import build_regen_url
from fun_time.rfb_slideshow import RfbSlideshow
from fun_time.session_environment import SessionEnvironment
from fun_time.session_handoff import DESKTOP, VR, take_handoff_request
from fun_time.session_resume import resume_shared_state
from fun_time.shared_state import BridgeState, SatelliteState, read_shared_state, write_shared_state
from fun_time.shortcuts import Shortcut
from fun_time.text_field_clicks import Seen, Sighting
from fun_time.voice_commands import format_spoken_command, parse_command_line
from fun_time.voice_control import VoiceController
from fun_time.watch_stats import load_watch_stats
from fun_time.window_layout import MonitorRect, secondary_monitor_rects
from fun_time.windows_bridge_dispatch_loop import (
    _AHK_PASSTHROUGH_OPS,
    _OP_HANDLERS,
    DispatchLoopRunner,
    build_bridge_config_from_manifest,
    detect_sleep_gap,
    poll_dashboard_commands,
)
from tests.fun_time_window_fakes import (
    DASHBOARD_HWND,
    DASHBOARD_PID,
    HOSTED_HWND,
    HOSTED_PID,
    LANDSCAPE_HWND,
    LANDSCAPE_PID,
    MAIN_FUNESTRA_HWND,
    MAIN_FUNESTRA_PID,
    PORTRAIT_HWND,
    PORTRAIT_PID,
    RFB_HWND,
    TOPMOST_HWNDS,
    HandDrivenClock,
    lookup_hosted,
    lookup_pid,
    lookup_title,
)
from tests.hosted_shows import publish_the_show
from tests.made_up_state import remembered_values, shifted_state

BROWSE_PID = 424242
BROWSE_HWND = 909090

LINK_CARRYING_A_PROMPT = build_regen_url(
    {"video": {"prompt": "a made-up prompt"}},
    video_url="https://example.com/video", image_url="https://example.com/create",
)


THE_ONE_READER_STUBBED_BESIDE_THE_CONFTESTS_REAL_ONES = "fun_time.fun_time_windows.is_window_topmost"


@pytest.fixture(autouse=True)
def _no_topmost_read_answers_from_this_machines_own_windows():
    with patch(THE_ONE_READER_STUBBED_BESIDE_THE_CONFTESTS_REAL_ONES, return_value=False):
        yield


def make_config(tmp_path, **overrides) -> BridgeConfig:
    settings = {
        "portrait": SatelliteChannel.in_state_dir(tmp_path, FunTimeFunestra.PORTRAIT),
        "landscape": SatelliteChannel.in_state_dir(tmp_path, FunTimeFunestra.LANDSCAPE),
        "trash_dir": tmp_path / "trash",
        "state_dir": tmp_path,
        "main_sources": "",
        "broker_mode_file": tmp_path / "rh_mode.txt",
        "genau_cmd_file": tmp_path / "rh_cmd.txt",
        "genau_paused_file": tmp_path / "rh_paused.txt",
        "genau_status_file": tmp_path / "genau_status.txt",
        "audio_paused_file": tmp_path / "audio_paused.txt",
        "audio_volume_file": tmp_path / "audio_volume.txt",
        "main_funestra_cmd_file": tmp_path / "main_player_cmd.txt",
        "main_funestra_paused_file": tmp_path / "main_player_paused.txt",
        "main_funestra_status_file": tmp_path / "main_player_status.txt",
        "dashboard_state_file": tmp_path / "dashboard_state.ini",
    }
    settings.update(overrides)
    return BridgeConfig(**settings)


def _write_satellite_status(path: Path, video, *, fraction: float | None = None,
                            paused: bool = False, locked: bool = False) -> None:
    duration_ms = 0 if fraction is None else 1000
    position_ms = 0 if fraction is None else round(fraction * duration_ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"video={video}\nposition_ms={position_ms}\nduration_ms={duration_ms}\n"
        f"paused={'1' if paused else '0'}\nlocked={'1' if locked else '0'}\n",
        encoding="utf-8",
    )


def make_runner(tmp_path, *, config=None, **kwargs) -> DispatchLoopRunner:
    windows = FunTimeWindows(
        pids=ChildPids(
            main_funestra=kwargs.pop("main_funestra_pid", MAIN_FUNESTRA_PID),
            portrait=kwargs.pop("portrait_pid", PORTRAIT_PID),
            landscape=kwargs.pop("landscape_pid", LANDSCAPE_PID),
            dashboard=kwargs.pop("dashboard_pid", DASHBOARD_PID),
            origenerator=kwargs.pop("origenerator_pid", 0),
        ),
        rfb_hwnd=kwargs.pop("rfb_hwnd", 0),
        hwnds_seen_while_visible=kwargs.pop("hwnds_by_window", None),
        **({"clock": kwargs.pop("clock")} if "clock" in kwargs else {}),
    )
    start = {name: kwargs.pop(name) for name in (
        "rfb_shortcut", "rfb_slideshow", "press_and_caret_watch", "origenerator_already_open",
        "origenerator_slideshow_on") if name in kwargs}
    publishing = {name: kwargs.pop(name) for name in (
        "dashboard_enabled", "hud_publisher", "sync_interval_ms", "secondary_rects")
        if name in kwargs}
    runner = DispatchLoopRunner(
        config or make_config(tmp_path),
        SessionFiles(
            dashboard_cmd=tmp_path / "dashboard_cmd.txt",
            shared_state=tmp_path / "shared_state.ini",
            ahk_cmd=tmp_path / "ahk_cmd.txt",
            manifest=kwargs.pop("manifest_path", None)),
        SessionStart(windows=windows, **start),
        Publishing(**{"dashboard_enabled": False, **publishing}),
        **kwargs,
    )
    _leave_the_periodic_sync_to_the_test_about_it(runner)
    return runner


def _leave_the_periodic_sync_to_the_test_about_it(runner: DispatchLoopRunner) -> None:
    runner._last_sync = float("inf")


def _make_the_periodic_sync_due(runner: DispatchLoopRunner) -> None:
    runner._last_sync = float("-inf")


@contextlib.contextmanager
def _finding_no_window_not_even_a_real_fun_times():
    with patch("fun_time.fun_time_windows.find_window_by_pid", return_value=0), \
         patch("fun_time.fun_time_windows.find_window_by_title", return_value=0):
        yield


def _mailbox(runner: DispatchLoopRunner) -> list[str]:
    return runner.ahk_cmd_file.read_text(encoding="utf-8").split()


def test_the_loop_holds_genaus_image_out_of_a_park_while_the_osr2_is_quiet(tmp_path):
    runner = make_runner(tmp_path)

    runner.arbiter.sync("genau", paused=False, control=OSR2_PARKED)
    quiet = runner.config.genau_cmd_file.read_text(encoding="utf-8").split("\n")[:-1]
    (runner.config.broker_state / OSR2_SERIAL_RX).write_text(str(time.time()), encoding="utf-8")
    runner.arbiter.sync("genau", paused=False, control=OSR2_PARKED)
    on = runner.config.genau_cmd_file.read_text(encoding="utf-8").split("\n")[len(quiet):-1]

    assert "PARK" not in quiet
    assert on[-1] == "PARK"


def test_the_bridge_config_carries_the_port_the_session_serves_on(cfg_factory, tmp_path):
    config = load_config(cfg_factory({"loopback_port": 8771}))
    manifest = LaunchManifest.read(write_windows_bridge_manifest(config, tmp_path / "manifest.ini"))

    assert build_bridge_config_from_manifest(manifest).loopback_port == 8771


def test_either_session_hosts_the_origenerator_its_config_names(cfg_factory, tmp_path):
    origenerator_dir = tmp_path / "origenerator"
    origenerator_dir.mkdir()
    config = load_config(cfg_factory({"paths": {"origenerator_dir": str(origenerator_dir)}}))
    manifest = LaunchManifest.read(write_windows_bridge_manifest(config, tmp_path / "manifest.ini"))

    assert build_bridge_config_from_manifest(manifest).origenerator_enabled
    assert build_bridge_config_from_manifest(
        manifest, vr_main_funestra=True).origenerator_enabled


def test_the_session_ends_a_line_said_aloud_the_way_its_origenerator_publishes(
        cfg_factory, tmp_path):
    origenerator_dir = tmp_path / "origenerator"
    origenerator_dir.mkdir()
    (origenerator_dir / "origenerator_contract.json").write_text(
        json.dumps({"said_at_suffix": " @{said_at_epoch_seconds}"}), encoding="utf-8")
    config = load_config(cfg_factory({"paths": {"origenerator_dir": str(origenerator_dir)}}))
    manifest = LaunchManifest.read(write_windows_bridge_manifest(config, tmp_path / "manifest.ini"))

    assert build_bridge_config_from_manifest(manifest).origenerator_said_at_suffix == (
        " @{said_at_epoch_seconds}")


def test_each_satellite_reads_the_hosted_hud_where_the_manifest_names_it(cfg_factory, tmp_path):
    config = load_config(cfg_factory({}))
    manifest = LaunchManifest.read(write_windows_bridge_manifest(config, tmp_path / "manifest.ini"))

    bridge = build_bridge_config_from_manifest(manifest)

    for funestra in FunTimeFunestra.SATELLITES:
        assert bridge.satellite(funestra).origenerator_hud_file == (
            config.satellite(funestra).origenerator_hud_file)


def _join_the_browse_threads() -> None:
    for thread in threading.enumerate():
        if thread.name == "library-browser":
            thread.join(timeout=10.0)


def _wait_until_the_browse_thread_ends(mock_browse) -> None:
    wait_until(lambda: mock_browse.call_count >= 1, timeout=10.0)
    _join_the_browse_threads()


@contextlib.contextmanager
def _press_channel(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as recv_sock:
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.settimeout(1.0)
        (tmp_path / "dashboard_press_port.txt").write_text(
            str(recv_sock.getsockname()[1]), encoding="utf-8")
        yield recv_sock


def _presses_until(recv_sock, expected: str, *, timeout: float = 10.0) -> list[str]:
    received: list[str] = []

    def _arrived() -> bool:
        with contextlib.suppress(OSError):
            received.append(recv_sock.recvfrom(256)[0].decode("utf-8"))
        return expected in received

    recv_sock.settimeout(0.02)
    with contextlib.suppress(TimeoutError):
        wait_until(_arrived, timeout=timeout, interval=0)
    return received


class TestDetectSleepGap:
    def test_reports_gap_when_stall_exceeds_threshold(self):
        assert detect_sleep_gap(1000.0, 1000.0 + 300, threshold_s=90.0) == 300

    def test_no_gap_for_normal_iteration(self):
        assert detect_sleep_gap(1000.0, 1000.05) is None

    def test_a_tick_a_stalled_disk_held_for_tens_of_seconds_is_no_wake(self):
        assert detect_sleep_gap(1000.0, 1000.0 + 40) is None


class TestRunLoopWakeLogging:
    def test_a_wake_is_logged_to_date_a_later_resume_failure(self, tmp_path, caplog):
        runner = make_runner(tmp_path)
        ticks = {"n": 0}

        def fake_tick():
            ticks["n"] += 1
            if ticks["n"] >= 2:
                runner.stop()

        scripted = [1000.0, 1000.0, 2000.0]
        cursor = {"i": 0}

        def fake_time():
            i = cursor["i"]
            if i < len(scripted):
                cursor["i"] += 1
                return scripted[i]
            return scripted[-1]

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

    def test_a_verb_carrying_a_path_keeps_the_paths_case(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("play_file C:/Videos/Alpha/Scene One.MP4", encoding="utf-8")

        assert poll_dashboard_commands(cmd_file) == ["play_file C:/Videos/Alpha/Scene One.MP4"]

    def test_a_command_file_that_is_not_there_holds_no_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"

        result = poll_dashboard_commands(cmd_file)

        assert result == []

    def test_a_command_file_nobody_has_written_to_holds_no_commands(self, tmp_path):
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
        cmd_file.write_text("main_next\nmain_next\nportrait_prev\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["main_next", "main_next", "portrait_prev"]

    def test_concurrent_write_does_not_lose_new_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("first_command\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["first_command"]
        cmd_file.write_text("second_command\n", encoding="utf-8")
        assert cmd_file.exists()

    def test_a_stale_claim_file_does_not_block_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        stale = cmd_file.with_suffix(cmd_file.suffix + ".consuming")
        stale.write_text("old_command\n", encoding="utf-8")
        cmd_file.write_text("new_command\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["new_command"]
        assert not cmd_file.exists()
        assert not stale.exists()

    def test_strips_utf8_bom_from_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_bytes(b"\xef\xbb\xbfmain_prev\n")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["main_prev"]


class TestDispatchLoopRunner:
    def test_dispatches_dashboard_command(self, tmp_path):
        runner = make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0].text for c in mock_dispatch.call_args_list]
        assert "portrait_next" in commands
        assert not cmd_file.exists()

    def test_bare_active_lock_targets_the_landscape_when_it_is_active(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(landscape=SatelliteState(locked=False), active_funestra=3)
        (tmp_path / "dashboard_cmd.txt").write_text("active_lock_on", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0].text for c in mock_dispatch.call_args_list]
        assert "landscape_lock_on" in commands
        assert "portrait_lock_on" not in commands

    def test_bare_active_next_targets_the_active_funestra(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(active_funestra=2)
        (tmp_path / "dashboard_cmd.txt").write_text("active_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0].text for c in mock_dispatch.call_args_list]
        assert "portrait_next" in commands

    def test_a_spoken_command_carries_the_video_the_sampler_back_dates_it_to(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(portrait=SatelliteState(locked=False), active_funestra=2)
        (tmp_path / "dashboard_cmd.txt").write_text("portrait_lock_on @100.200", encoding="utf-8")

        with patch.object(runner.watch, "video_at", return_value="C:\\clips\\meant.mp4"), \
             patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        assert mock_dispatch.call_args[0][0].text == "portrait_lock_on"
        assert mock_dispatch.call_args.kwargs["target_path"] == "C:\\clips\\meant.mp4"

    def test_a_spoken_command_carries_when_it_was_said_to_the_dispatch(self, tmp_path):
        runner = make_runner(tmp_path)
        (tmp_path / "dashboard_cmd.txt").write_text("portrait_lock_on @100.200", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        assert mock_dispatch.call_args.kwargs["spoken_at"] == 100.2

    def test_the_tick_samples_every_funestra_on_the_watch_cadence(self, tmp_path):
        runner = make_runner(tmp_path)

        with patch.object(runner.watch, "sample_due") as sample:
            runner.tick()
            assert sample.call_args.kwargs["omni_paused"] is False

            runner.state = BridgeState(omni_paused=True)
            write_shared_state(tmp_path / "shared_state.ini", runner.state)
            runner.tick()
            assert sample.call_args.kwargs["omni_paused"] is True

    def test_nudge_dispatches_to_command(self, tmp_path):
        runner = make_runner(tmp_path)
        (tmp_path / "dashboard_cmd.txt").write_text("main_nudge_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0].text for c in mock_dispatch.call_args_list]
        assert "main_nudge_next" in commands

    def test_omnipause_enter_via_tick_drops_topmost_on_all_managed_windows(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        (tmp_path / "dashboard_cmd.txt").write_text("omnipause_toggle", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert runner.state.omni_paused is True
        assert {h for h, v in topmost_calls if v is False} == TOPMOST_HWNDS

    def test_omnipause_leave_via_tick_restores_topmost_and_refocuses_primary_funestra(
        self, tmp_path,
    ):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=True)
        (tmp_path / "dashboard_cmd.txt").write_text("omnipause_toggle", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []
        activated: list[int] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.is_window_topmost", return_value=False), \
             patch("fun_time.fun_time_windows.activate_window", side_effect=activated.append), \
             patch("fun_time.fun_time_windows.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert runner.state.omni_paused is False
        assert {h for h, v in topmost_calls if v is True} == TOPMOST_HWNDS
        assert activated == [MAIN_FUNESTRA_HWND]

    def test_omnipause_toggle_updates_state_and_writes_shared_state(self, tmp_path):
        runner = make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omnipause_toggle", encoding="utf-8")

        with _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.fun_time_windows.set_always_on_top"):
            runner.tick()

        assert runner.state.omni_paused is True
        loaded = read_shared_state(tmp_path / "shared_state.ini")
        assert loaded is not None
        assert loaded.omni_paused is True

    def test_browse_library_sends_its_press_and_browses_with_the_app_playing(self, tmp_path):
        with _press_channel(tmp_path) as recv_sock:
            runner = make_runner(tmp_path, dashboard_enabled=True)
            runner.state = BridgeState(main_mode=MainMode.KINO)
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("browse_library", encoding="utf-8")

            with _finding_no_window_not_even_a_real_fun_times(), \
                 patch("fun_time.fun_time_windows.set_always_on_top"), \
                 patch("fun_time.windows_bridge_dispatch_loop.browse_library",
                       return_value=None) as mock_browse:
                runner.tick()

                messages = _presses_until(recv_sock, "browse_library")
                _wait_until_the_browse_thread_ends(mock_browse)

        assert "browse_library" in messages
        assert runner.state.omni_paused is False

    def test_dispatch_forwards_remaining_ops_to_ahk(self, tmp_path):
        runner = make_runner(tmp_path)

        suspend_op = WindowOp(op="suspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [suspend_op])
            runner._dispatch(parse_command("some_command"))

        assert _mailbox(runner) == ["suspend_hotkeys"]

    def test_dispatch_suppresses_unsuspend_during_integration(self, tmp_path):
        runner = make_runner(tmp_path, env=SessionEnvironment(integration=True))
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        unsuspend_op = WindowOp(op="unsuspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [unsuspend_op])
            runner._dispatch(parse_command("some_command"))

        assert not ahk_cmd_file.exists()

    def test_dispatch_allows_unsuspend_outside_integration(self, tmp_path):
        runner = make_runner(tmp_path)

        unsuspend_op = WindowOp(op="unsuspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [unsuspend_op])
            runner._dispatch(parse_command("some_command"))

        assert _mailbox(runner) == ["unsuspend_hotkeys"]

    def test_dispatch_sends_a_message_to_the_event_log_not_to_ahk(self, tmp_path):
        runner = make_runner(tmp_path)
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        message_op = WindowOp(op="message", key="Genaumacher: MyVideo", source="main")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.flash_message") as mock_message:
            mock_dispatch.return_value = (runner.state, [message_op])
            runner._dispatch(parse_command("some_command"))

        assert not ahk_cmd_file.exists()
        mock_message.assert_called_once()
        assert mock_message.call_args[0][1] == "Genaumacher: MyVideo"
        assert mock_message.call_args[1] == {"source": "main", "level": message_op.level}

    def test_a_dead_end_message_is_logged_at_its_warning_level(self, tmp_path):
        runner = make_runner(tmp_path)

        message_op = WindowOp(op="message", key="No other seeds", source="portrait", level=logging.WARNING)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.flash_message") as mock_message:
            mock_dispatch.return_value = (runner.state, [message_op])
            runner._dispatch(parse_command("portrait_cycle_seed"))

        assert mock_message.call_args[1] == {"source": "portrait", "level": logging.WARNING}

    def test_sync_tick_calls_update_dashboard_when_enabled(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=100, dashboard_enabled=True)
        _make_the_periodic_sync_due(runner)

        with patch.object(runner, "_update_dashboard") as mock_update:
            runner.tick()

        mock_update.assert_called_once()

    def test_reads_shared_state_at_tick_start(self, tmp_path):
        runner = make_runner(tmp_path)
        assert runner.state.omni_paused is False

        write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))
        runner.tick()

        assert runner.state.omni_paused is True

    def test_a_command_reaches_the_file_the_dashboard_and_satellite_huds_draw_from(self, tmp_path):
        runner = make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("landscape_lock", encoding="utf-8")
        state_file = tmp_path / "shared_state.ini"

        new_state = BridgeState(landscape=SatelliteState(locked=True))
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command", return_value=(new_state, [])):
            runner.tick()

        loaded = read_shared_state(state_file)
        assert loaded is not None
        assert loaded.satellite(FunTimeFunestra.LANDSCAPE).locked is True

    def test_quit_asks_the_hotkey_script_to_end_the_session_rather_than_exit(self, tmp_path):
        runner = make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("quit", encoding="utf-8")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        runner.tick()

        assert ahk_cmd_file.read_text(encoding="utf-8") == "end_session"

    def test_entering_vr_ends_this_session_with_word_of_where_to_go(self, tmp_path):
        runner = make_runner(tmp_path)
        (tmp_path / "dashboard_cmd.txt").write_text("enter_vr", encoding="utf-8")

        runner.tick()

        assert (tmp_path / "ahk_cmd.txt").read_text(encoding="utf-8") == "end_session"
        taken = take_handoff_request(tmp_path)
        assert taken.target is VR
        assert taken.cancelable, "a spoken crossing is one Esc may call off"

    def test_exiting_vr_from_a_vr_session_asks_for_the_desktop(self, tmp_path):
        runner = make_runner(tmp_path, config=make_config(tmp_path, vr_main_funestra=True))
        (tmp_path / "dashboard_cmd.txt").write_text("exit_vr", encoding="utf-8")

        runner.tick()

        assert (tmp_path / "ahk_cmd.txt").read_text(encoding="utf-8") == "end_session"
        assert take_handoff_request(tmp_path).target is DESKTOP

    @pytest.mark.parametrize(
        ("command", "vr_main_funestra"), [("exit_vr", False), ("enter_vr", True)],
    )
    def test_a_crossing_heard_twice_never_ends_the_session_it_reached(
        self, tmp_path, caplog, command, vr_main_funestra,
    ):
        runner = make_runner(tmp_path, config=make_config(
            tmp_path, vr_main_funestra=vr_main_funestra,
        ))
        (tmp_path / "dashboard_cmd.txt").write_text(command, encoding="utf-8")

        with caplog.at_level(logging.DEBUG, logger="fun_time.windows_bridge_dispatch_loop"):
            runner.tick()

        assert not (tmp_path / "ahk_cmd.txt").exists()
        assert take_handoff_request(tmp_path) is None
        assert [r.levelno for r in caplog.records
                if r.getMessage().startswith("Already running")] == [logging.WARNING]

    def test_a_crossing_repeated_while_speech_settles_starts_nothing_more(self, tmp_path, caplog):
        runner = make_runner(tmp_path)
        (tmp_path / "dashboard_cmd.txt").write_text("enter_vr", encoding="utf-8")
        runner.tick()
        assert take_handoff_request(tmp_path).target is VR
        (tmp_path / "ahk_cmd.txt").unlink()
        (tmp_path / "dashboard_cmd.txt").write_text("enter_vr", encoding="utf-8")

        with caplog.at_level(logging.DEBUG, logger="fun_time.windows_bridge_dispatch_loop"):
            runner.tick()

        assert not (tmp_path / "ahk_cmd.txt").exists()
        assert take_handoff_request(tmp_path) is None
        assert [r.levelno for r in caplog.records
                if r.getMessage().startswith("Already crossing")] == [logging.WARNING]

    def test_enter_vr_said_during_omnipause_still_crosses(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = replace(runner.state, omni_paused=True)
        (tmp_path / "dashboard_cmd.txt").write_text(
            format_spoken_command("enter_vr", spoken_at=123.0, said="enter VR"), encoding="utf-8")

        runner.tick()

        assert (tmp_path / "ahk_cmd.txt").read_text(encoding="utf-8") == "end_session"
        assert take_handoff_request(tmp_path).target is VR

    def test_omniminimize_minimizes_only_mode_visible_windows(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omniminimize", encoding="utf-8")

        minimized: list[tuple[int, dict]] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.minimize_window", side_effect=lambda h, **kw: minimized.append((h, kw))):
            runner.tick()

        assert {h for h, _ in minimized} == {
            RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND, MAIN_FUNESTRA_HWND,
        }
        assert all(kw.get("activate_the_next_window") is False for _, kw in minimized)

    def test_omniminimize_in_genau_mode_includes_the_main_funestra_too(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(main_mode=MainMode.GENAU)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omniminimize", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()

        assert set(minimized) == {
            RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND, MAIN_FUNESTRA_HWND,
        }

    def test_omniminimize_skips_windows_that_are_not_found(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=0)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omniminimize", encoding="utf-8")

        minimized: list[int] = []
        with _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.fun_time_windows.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()

        assert minimized == []

    def test_omnirestore_restores_exactly_the_minimized_windows(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        cmd_file = tmp_path / "dashboard_cmd.txt"

        minimized_hwnds: list[int] = []
        restored: list[tuple[int, dict]] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.minimize_window",
                   side_effect=lambda h, **kw: minimized_hwnds.append(h)), \
             patch("fun_time.fun_time_windows.restore_window", side_effect=lambda h, **kw: restored.append((h, kw))):
            cmd_file.write_text("omniminimize", encoding="utf-8")
            runner.tick()
            assert minimized_hwnds, "omniminimize put nothing down"

            cmd_file.write_text("omnirestore", encoding="utf-8")
            runner.tick()

            assert [h for h, _ in restored] == minimized_hwnds
            assert all(kw.get("activate") is False for _, kw in restored)

            cmd_file.write_text("omnirestore", encoding="utf-8")
            runner.tick()

        assert [h for h, _ in restored] == minimized_hwnds

    def test_a_hud_button_is_how_a_borderless_satellite_is_parked_alone(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_minimize", encoding="utf-8")

        minimized: list[tuple[int, dict]] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.minimize_window", side_effect=lambda h, **kw: minimized.append((h, kw))):
            runner.tick()

        assert [h for h, _ in minimized] == [PORTRAIT_HWND]
        assert all(kw.get("activate_the_next_window") is False for _, kw in minimized), (
            "parking one Funestra must not hand the foreground to the next")

    def test_a_huds_minimize_button_parks_the_window_still_showing_its_video(self, tmp_path):
        clock = HandDrivenClock()
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND, clock=clock)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("landscape_minimize", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()
            assert minimized == [LANDSCAPE_HWND]

            clock.advance(MAIN_BLANK_SETTLE_S)
            runner.tick()
            assert minimized == [LANDSCAPE_HWND]

    def test_the_main_funestras_main_hud_button_parks_the_main_funestras_window_in_either_mode(self, tmp_path):
        for mode, wanted in (("genau", [MAIN_FUNESTRA_HWND]), ("kino", [MAIN_FUNESTRA_HWND])):
            runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
            write_shared_state(tmp_path / "shared_state.ini", BridgeState(main_mode=mode))
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("main_minimize", encoding="utf-8")

            minimized: list[int] = []

            with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
                 patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
                 patch("fun_time.fun_time_windows.minimize_window", side_effect=lambda h, minimized=minimized, **kw: minimized.append(h)):
                runner.tick()

            assert minimized == wanted, mode

    def test_leaving_omnipause_brings_back_every_window_a_button_parked(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))
        cmd_file = tmp_path / "dashboard_cmd.txt"

        restored: list[tuple[int, dict]] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.minimize_window"), \
             patch("fun_time.fun_time_windows.activate_window"), \
             patch("fun_time.fun_time_windows.restore_window", side_effect=lambda h, **kw: restored.append((h, kw))), \
             patch.object(runner.windows, "restore_all_topmost"):
            cmd_file.write_text("portrait_minimize\nlandscape_minimize", encoding="utf-8")
            runner.tick()
            assert restored == [], "parking a Funestra restores nothing"

            cmd_file.write_text("omnipause_toggle", encoding="utf-8")
            runner.tick()

            assert [h for h, _ in restored] == [PORTRAIT_HWND, LANDSCAPE_HWND]
            assert all(kw.get("activate") is False for _, kw in restored)

            write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))
            cmd_file.write_text("omnipause_toggle", encoding="utf-8")
            runner.tick()

        assert [h for h, _ in restored] == [PORTRAIT_HWND, LANDSCAPE_HWND]

    def test_a_huds_minimize_button_says_nothing_to_ahk(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_minimize", encoding="utf-8")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.minimize_window"):
            runner.tick()

        assert not ahk_cmd_file.exists()

    def test_sends_press_via_udp_on_button_command(self, tmp_path):
        with _press_channel(tmp_path) as recv_sock:
            runner = make_runner(tmp_path, dashboard_enabled=True)
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock", encoding="utf-8")

            with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
                mock_dispatch.return_value = (runner.state, [])
                runner.tick()

            data, _ = recv_sock.recvfrom(256)
        assert data.decode("utf-8") == "portrait_lock"

    def test_help_reference_commands_send_press_but_do_not_dispatch(self, tmp_path):
        for command in ("help_reference", "help_reference_close"):
            with _press_channel(tmp_path) as recv_sock:
                runner = make_runner(tmp_path, dashboard_enabled=True)
                (tmp_path / "dashboard_cmd.txt").write_text(command, encoding="utf-8")

                with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
                    runner.tick()

                mock_dispatch.assert_not_called()
                data, _ = recv_sock.recvfrom(256)
            assert data.decode("utf-8") == command

    def test_a_missing_press_port_file_does_not_cost_the_command(self, tmp_path):
        runner = make_runner(tmp_path, dashboard_enabled=True)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_lock", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args[0][0].text == "portrait_lock"

    def test_voice_off_mutes_voice_controller(self, tmp_path):
        runner = make_runner(tmp_path)
        vc = VoiceController(
            cmd_file=tmp_path / "vc_cmd.txt",
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
        )
        runner.voice.controller = vc
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("voice_off", encoding="utf-8")

        runner.tick()

        assert vc.is_muted

    def test_voice_toggle_unmutes_when_muted(self, tmp_path):
        runner = make_runner(tmp_path)
        vc = VoiceController(
            cmd_file=tmp_path / "vc_cmd.txt",
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
        )
        vc.mute()
        runner.voice.controller = vc
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("voice_toggle", encoding="utf-8")

        runner.tick()

        assert not vc.is_muted

    def test_voice_toggle_mutes_when_not_muted(self, tmp_path):
        runner = make_runner(tmp_path)
        vc = VoiceController(
            cmd_file=tmp_path / "vc_cmd.txt",
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
        )
        runner.voice.controller = vc
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("voice_toggle", encoding="utf-8")

        runner.tick()

        assert vc.is_muted

    def test_the_mic_being_turned_off_is_kept_in_the_apps_state(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.voice.controller = VoiceController(
            cmd_file=tmp_path / "vc_cmd.txt",
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
        )
        (tmp_path / "dashboard_cmd.txt").write_text("voice_off", encoding="utf-8")

        runner.tick()

        assert read_shared_state(tmp_path / "shared_state.ini").mic_muted is True

    def test_turning_it_back_on_is_written_down_too(self, tmp_path):
        runner = make_runner(tmp_path)
        vc = VoiceController(
            cmd_file=tmp_path / "vc_cmd.txt",
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
        )
        vc.mute()
        runner.voice.controller = vc
        (tmp_path / "dashboard_cmd.txt").write_text("voice_toggle", encoding="utf-8")

        runner.tick()

        assert read_shared_state(tmp_path / "shared_state.ini").mic_muted is False

    def test_a_request_the_hosted_app_says_opened_on_a_satellite_is_passed_to_the_voice(
            self, tmp_path):
        runner = make_runner(tmp_path)
        heard = []
        runner.voice.controller = SimpleNamespace(
            request_opened=lambda satellite: heard.append(("opened", satellite)),
            request_closed=lambda satellite: heard.append(("closed", satellite)),
            is_muted=False, unsuspend=lambda: None, suspend=lambda hold: None)
        (tmp_path / "dashboard_cmd.txt").write_text(
            "landscape_request_open\nlandscape_request_closed\n", encoding="utf-8")

        runner.tick()

        assert heard == [("opened", "landscape"), ("closed", "landscape")]

    def test_a_lock_the_hosted_app_tells_of_is_held_as_a_kino_lock_is_and_never_sent_back(
            self, tmp_path):
        metadata_root = tmp_path / "videos" / "metadata"
        video = tmp_path / "videos" / "videos" / "portrait" / "scene one.mp4"
        record = metadata_path_for(video, metadata_root)
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"video": {"action": "alpha"}}), encoding="utf-8")
        hosted_app = tmp_path / "origenerator_cmd.txt"
        runner = make_runner(tmp_path, config=make_config(
            tmp_path, regen_metadata_root=metadata_root, origenerator_enabled=True,
            origenerator_cmd_file=hosted_app))
        runner.state = _in_origenerator_mode(
            replace(runner.state, origenerator_ready=True), FunTimeFunestra.PORTRAIT)
        (tmp_path / "dashboard_cmd.txt").write_text(
            f"portrait_lock_held|{video}\n", encoding="utf-8")

        runner.tick()

        assert is_favorite(video, metadata_root)
        assert load_watch_stats(tmp_path / "watch_stats.json")[
            normalize_path_key(str(video))]["locks"] == 1
        assert not hosted_app.exists()

    def test_omnipause_suspends_the_voice_controller(self, tmp_path):
        runner = make_runner(tmp_path)
        vc_cmd = tmp_path / "vc_cmd.txt"
        vc = VoiceController(
            cmd_file=vc_cmd,
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
            clock=lambda: 0.0,
        )
        runner.voice.controller = vc
        runner.state = replace(runner.state, omni_paused=True)

        runner.tick()

        vc._write_spoken("landscape next", spoken_at=1.0)
        vc._write_spoken("play", spoken_at=2.0)
        written = vc_cmd.read_text(encoding="utf-8").splitlines()
        assert [parse_command_line(line).command for line in written] == ["play"]

    def test_leaving_omnipause_unsuspends_the_voice_controller(self, tmp_path):
        runner = make_runner(tmp_path)
        vc_cmd = tmp_path / "vc_cmd.txt"
        vc = VoiceController(
            cmd_file=vc_cmd,
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
        )
        vc.suspend()
        runner.voice.controller = vc
        runner.state = replace(runner.state, omni_paused=False)

        runner.tick()

        vc._write_spoken("landscape next", spoken_at=1.0)
        written = vc_cmd.read_text(encoding="utf-8").splitlines()
        assert [parse_command_line(line).command for line in written] == ["landscape_next"]

    def test_enter_carries_out_the_command_voice_was_not_sure_of(self, tmp_path):
        runner = make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        runner.voice.controller = VoiceController(
            cmd_file=cmd_file,
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
        )
        runner.voice.controller.handle_heard(Heard(
            Recognition(unconfirmed_phrase="landscape next"),
            spoken_at=1.0, peak=2000, audio=b"", candidates={}, words_formed=True))
        cmd_file.write_text("voice_accept", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command",
                   return_value=(runner.state, [])) as dispatched:
            runner.tick()
            runner.tick()

        assert [call.args[0].text for call in dispatched.call_args_list] == ["landscape_next"]

    @pytest.mark.parametrize("listening", [True, False])
    def test_enter_with_nothing_voice_was_not_sure_of_says_there_is_nothing_to_accept(
        self, tmp_path, listening,
    ):
        runner = make_runner(tmp_path)
        if listening:
            runner.voice.controller = VoiceController(
                cmd_file=tmp_path / "dashboard_cmd.txt",
                settings=VoiceControlConfig(enabled=True, model_path="unused"),
            )
        (tmp_path / "dashboard_cmd.txt").write_text("voice_accept", encoding="utf-8")
        flashed = []

        with patch("fun_time.windows_bridge_dispatch_loop.flash_message",
                   side_effect=lambda _log, msg, *, source, level=25: flashed.append(
                       (msg, source, level))):
            runner.tick()

        assert flashed == [("Nothing to accept", "system", logging.WARNING)]


class TestABareHudPhraseAfterANamedOne:
    @staticmethod
    def _say(runner, tmp_path, command, said):
        (tmp_path / "dashboard_cmd.txt").write_text(
            format_spoken_command(command, spoken_at=1.0, said=said), encoding="utf-8")
        with patch("fun_time.windows_bridge_dispatch_loop.flash_message"):
            runner.tick()

    def test_hud_expand_after_portrait_hud_collapse_opens_the_portrait_hud(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = replace(runner.state, active_funestra=FunTimeFunestra.MAIN)

        self._say(runner, tmp_path, "portrait_hud_minimize", "portrait hud collapse")
        assert runner.state.satellite(FunTimeFunestra.PORTRAIT).hud_minimized is True
        self._say(runner, tmp_path, "active_hud_restore", "hud expand")

        assert runner.state.satellite(FunTimeFunestra.PORTRAIT).hud_minimized is False


class TestWhatASpokenCommandFlashes:
    @staticmethod
    def _flashed(tmp_path, line, ops=(), **state):
        runner = make_runner(tmp_path)
        runner.state = replace(runner.state, **state)
        (tmp_path / "dashboard_cmd.txt").write_text(line, encoding="utf-8")
        flashed = []
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command",
                   return_value=(runner.state, list(ops))), \
             patch("fun_time.windows_bridge_dispatch_loop.flash_message",
                   side_effect=lambda _log, msg, *, source, level=25: flashed.append(
                       (msg, source, level))):
            runner.tick()
        return flashed

    def test_a_command_that_says_nothing_itself_flashes_what_was_heard_over_its_funestra(
        self, tmp_path,
    ):
        spoken = format_spoken_command("landscape_next", spoken_at=1.0, said="landscape next")

        assert self._flashed(tmp_path, spoken) == [("landscape next", "landscape", 25)]

    def test_a_command_its_mode_has_nothing_for_says_it_was_ignored_and_why(self, tmp_path):
        spoken = format_spoken_command(
            "landscape_say_experiments", spoken_at=1.0, said="landscape experiments")
        ignored = WindowOp(op=Op.IGNORED, key="in Kino mode")

        assert self._flashed(tmp_path, spoken, [ignored]) == [
            ("ignored in Kino mode: landscape experiments", "landscape", logging.WARNING)]

    @pytest.mark.parametrize(("active_funestra", "source"), [(2, "portrait"), (1, "main")])
    def test_a_bare_command_flashes_over_the_funestra_it_reached(
        self, tmp_path, active_funestra, source,
    ):
        spoken = format_spoken_command("active_next", spoken_at=1.0, said="next")

        flashed = self._flashed(tmp_path, spoken, active_funestra=active_funestra)

        assert flashed == [("next", source, 25)]

    def test_a_command_that_says_what_it_did_is_the_one_entry(self, tmp_path):
        spoken = format_spoken_command("portrait_more_seeds", spoken_at=1.0, said="more seeds")
        said_itself = WindowOp(op="message", key="More seeds", source="portrait")

        assert self._flashed(tmp_path, spoken, [said_itself]) == [("More seeds", "portrait", 25)]

    @staticmethod
    def _run_for_real(tmp_path, command, said, *, config=None, **state):
        runner = make_runner(tmp_path, config=config)
        runner.state = replace(runner.state, **state)
        (tmp_path / "dashboard_cmd.txt").write_text(
            format_spoken_command(command, spoken_at=1.0, said=said), encoding="utf-8")
        flashed = []
        with patch("fun_time.windows_bridge_dispatch_loop.flash_message",
                   side_effect=lambda _log, msg, *, source, level=25: flashed.append(
                       (msg, source, level))):
            runner.tick()
        return flashed

    @pytest.mark.parametrize(("command", "said"), [
        ("portrait_more_seeds", "more seeds"),
        ("portrait_latest", "portrait latest"),
        ("landscape_fmode", "landscape f mode"),
    ])
    def test_a_spoken_command_run_for_real_flashes_one_entry(self, tmp_path, command, said):
        flashed = self._run_for_real(tmp_path, command, said)

        assert len(flashed) == 1, flashed

    def test_favorites_said_to_a_satellite_in_kino_mode_flashes_its_f_mode_going_on(self, tmp_path):
        flashed = self._run_for_real(tmp_path, "landscape_say_favorites", "landscape favorites")

        assert flashed == [(f"{F_MODE_LABEL} enabled", "landscape", FAVORITE)]

    def test_a_hosted_shows_words_in_kino_mode_say_kino_mode_ignored_them(self, tmp_path):
        flashed = self._run_for_real(
            tmp_path, "landscape_say_experiments", "landscape experiments")

        assert flashed == [
            ("ignored in Kino mode: landscape experiments", "landscape", logging.WARNING)]

    def test_a_jump_the_main_funestra_is_not_there_to_answer_says_genau_mode_ignored_it(
        self, tmp_path,
    ):
        flashed = self._run_for_real(
            tmp_path, "main_funestra_compilation", "compilation", main_mode=MainMode.GENAU)

        assert flashed == [("ignored in Genau mode: compilation", "main", logging.WARNING)]

    def test_a_video_save_with_no_main_funestra_to_save_from_says_genau_mode_ignored_it(
        self, tmp_path,
    ):
        flashed = self._run_for_real(
            tmp_path, "genaumacher_save", "save clip", main_mode=MainMode.GENAU)

        assert flashed == [("ignored in Genau mode: save clip", "system", logging.WARNING)]

    @pytest.mark.parametrize(("command", "said"), [
        ("landscape_trash", "landscape trash"),
        ("landscape_wrong_action", "landscape wrong action"),
    ])
    def test_a_judgement_on_a_satellite_that_has_named_no_video_says_what_was_heard(
        self, tmp_path, command, said,
    ):
        flashed = self._run_for_real(tmp_path, command, said)

        assert flashed == [(said, "landscape", 25)]

    def test_a_crossing_to_the_session_already_running_says_only_that(self, tmp_path):
        flashed = self._run_for_real(tmp_path, "exit_vr", "exit VR")

        assert flashed == [(f"Already running {DESKTOP.app_name}", "system", logging.WARNING)]

    def test_a_jump_the_headsets_main_funestra_cannot_answer_says_what_was_heard(self, tmp_path):
        flashed = self._run_for_real(
            tmp_path, "main_funestra_compilation", "compilation",
            config=make_config(tmp_path, vr_main_funestra=True), main_mode=MainMode.KINO)

        assert flashed == [("compilation", "main", 25)]

    @pytest.mark.parametrize("command", [
        "main_funestra_compilation", "main_funestra_full_vid", "main_funestra_clip_jump",
        "main_funestra_funscript_jump", "main_funestra_next_funscripted",
    ])
    def test_a_jump_the_main_funestra_answers_itself_says_nothing_now(self, tmp_path, command):
        flashed = self._run_for_real(
            tmp_path, command, "whatever was said", main_mode=MainMode.KINO)

        assert flashed == []

    def test_a_video_save_that_answers_once_it_lands_says_nothing_now(self, tmp_path):
        spoken = format_spoken_command("genaumacher_save", spoken_at=1.0, said="save clip")

        with patch.object(windows_bridge_dispatch_loop, "save_and_say"):
            assert self._flashed(tmp_path, spoken, [WindowOp(op="save_clip")]) == []

    def test_a_narrowing_that_says_its_words_where_it_is_done_says_nothing_now(self, tmp_path):
        spoken = format_spoken_command("main_funestra_length_clips", spoken_at=1.0, said="clips")

        assert self._flashed(tmp_path, spoken, [WindowOp(op=Op.NARROWING_ANSWERS)]) == []

    def test_a_pressed_command_flashes_only_what_it_says_itself(self, tmp_path):
        assert self._flashed(tmp_path, "landscape_next") == []

    def test_a_spoken_satellites_command_says_what_was_heard_once(self, tmp_path):
        spoken = format_spoken_command("satellites_next", spoken_at=1.0, said="satellites next")

        assert self._flashed(tmp_path, spoken) == [("satellites next", "system", 25)]

    def test_a_named_command_flashes_over_its_funestra_whichever_is_active(self, tmp_path):
        spoken = format_spoken_command("landscape_next", spoken_at=1.0, said="landscape next")

        assert self._flashed(tmp_path, spoken, active_funestra=2) == [
            ("landscape next", "landscape", 25)]

    @pytest.mark.parametrize(("command", "said"), [
        ("landscape_fmode_on", "landscape f mode on"),
        ("active_fmode_on", "f mode on"),
    ])
    def test_words_the_hosted_apps_show_takes_flash_what_was_heard(
        self, tmp_path, command, said,
    ):
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        _the_hosted_app_answers(tmp_path)
        write_shared_state(tmp_path / "shared_state.ini", _in_origenerator_mode(
            BridgeState(active_funestra=3)))
        (tmp_path / "dashboard_cmd.txt").write_text(
            format_spoken_command(command, spoken_at=1.0, said=said), encoding="utf-8")
        flashed = []
        with patch("fun_time.windows_bridge_dispatch_loop.flash_message",
                   side_effect=lambda _log, msg, *, source, level=25: flashed.append(
                       (msg, source))):
            runner.tick()

        assert flashed == [(said, "landscape")]


def test_a_word_the_app_hears_as_a_lock_locks_a_satellite_showing_the_hosted_app(tmp_path):
    runner = make_runner(tmp_path, config=_hosting(tmp_path))
    _the_hosted_app_answers(tmp_path)
    write_shared_state(tmp_path / "shared_state.ini", _in_origenerator_mode(BridgeState()))
    runner.hear_as_a_lock(frozenset({"hold"}))
    (tmp_path / "dashboard_cmd.txt").write_text(
        format_spoken_command("portrait_say_hold", spoken_at=1.0, said="portrait hold"),
        encoding="utf-8")

    runner.tick()

    assert (tmp_path / "portrait_cmd.txt").read_text(encoding="utf-8").split() == ["LOCK_ON"]
    assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8").split() == [
        "portrait_lock_on"]


class TestOpenRfbTab:
    @staticmethod
    def _rfb_patches(calls: list[tuple[str, object]], *, alive: bool = True):
        return (
            patch(
                "fun_time.windows_bridge_dispatch_loop.window_exists",
                side_effect=lambda hwnd: bool(hwnd) and alive,
            ),
            patch(
                "fun_time.windows_bridge_dispatch_loop.force_foreground_window",
                side_effect=lambda hwnd: calls.append(("activate", hwnd)) or True,
            ),
            patch(
                "fun_time.windows_bridge_dispatch_loop.open_rfb_tab",
                side_effect=lambda **kwargs: calls.append(("open", kwargs)),
            ),
        )

    def test_open_rfb_tab_op_calls_open_rfb_tab_when_rfb_running(self, tmp_path):
        runner = make_runner(
            tmp_path,
                        rfb_hwnd=12345,
            rfb_shortcut=Shortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                arguments='--profile-directory="Profile 2"'),
        )

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch(parse_command("portrait_lock"))

        assert calls == [
            ("activate", 12345),
            ("open", {
                "urls": ["https://example.com"],
                "shortcut": Shortcut(
                    target=r"C:\Chrome\chrome.exe",
                    work_dir=r"C:\Chrome",
                    arguments='--profile-directory="Profile 2"'),
            }),
        ]

    def test_rfb_window_is_activated_before_the_handoff(self, tmp_path):
        runner = make_runner(
            tmp_path,
                        rfb_hwnd=777,
            rfb_shortcut=Shortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                arguments='--profile-directory="Profile 2"'),
        )

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch(parse_command("portrait_lock"))

        assert [name for name, _ in calls] == ["activate", "open"]

    def test_a_tab_chrome_would_open_in_a_window_of_his_own_is_dropped(self, tmp_path):
        runner = make_runner(
            tmp_path,
                        rfb_hwnd=12345,
            rfb_shortcut=Shortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                arguments='--profile-directory="Profile 2"'),
        )

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls, alive=False)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch(parse_command("portrait_lock"))

        assert calls == []

    def test_open_rfb_tab_op_skipped_when_rfb_not_running(self, tmp_path):
        runner = make_runner(
            tmp_path,
                        rfb_hwnd=0,
            rfb_shortcut=Shortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                arguments='--profile-directory="Profile 2"'),
        )

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch(parse_command("portrait_lock"))

        assert calls == []

    def test_open_rfb_tab_op_skipped_when_no_shortcut_target(self, tmp_path):
        runner = make_runner(
            tmp_path,
                        rfb_hwnd=12345,
        )

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch(parse_command("portrait_lock"))

        assert calls == []

    def test_two_locks_never_make_two_launches_racing_for_chromes_one_instance(self, tmp_path):
        runner = make_runner(
            tmp_path,
                        rfb_hwnd=12345,
            rfb_shortcut=Shortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                arguments='--profile-directory="Profile 2"'),
        )
        runner.state = BridgeState(landscape=SatelliteState(locked=False), portrait=SatelliteState(locked=False))
        (tmp_path / "dashboard_cmd.txt").write_text("satellites_lock_on", encoding="utf-8")

        def fake_dispatch(cmd, state, config, target_path="", spoken_at=None, hosted=None):
            if cmd.text == "portrait_lock_on":
                return replace(state, portrait=SatelliteState(locked=True)), [WindowOp(op="open_rfb_tab", key="http://p")]
            if cmd.text == "landscape_lock_on":
                return replace(state, landscape=SatelliteState(locked=True)), [WindowOp(op="open_rfb_tab", key="http://l")]
            return state, []

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command", side_effect=fake_dispatch), \
             exists, activate, open_tab:
            runner.tick()

        assert calls == [
            ("activate", 12345),
            ("open", {
                "urls": ["http://p", "http://l"],
                "shortcut": Shortcut(
                    target=r"C:\Chrome\chrome.exe",
                    work_dir=r"C:\Chrome",
                    arguments='--profile-directory="Profile 2"'),
            }),
        ]

    @pytest.mark.parametrize(("window_alive", "line"), [
        (True, "Opened 2 RFB tab(s)"),
        (False, "2 RFB tab(s) skipped: no Random Favs Browser window to open into"),
    ])
    def test_the_log_counts_a_locks_tabs_and_never_names_their_links(
            self, tmp_path, caplog, window_alive, line):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND,
                             rfb_shortcut=Shortcut(target="chrome.exe", work_dir="", arguments=""))
        runner._pending_rfb_urls = [LINK_CARRYING_A_PROMPT, LINK_CARRYING_A_PROMPT]

        exists, activate, open_tab = self._rfb_patches([], alive=window_alive)
        with exists, activate, open_tab, \
             caplog.at_level(logging.DEBUG, logger="fun_time.windows_bridge_dispatch_loop"):
            runner._flush_rfb_tabs()

        assert line in caplog.messages
        assert not [message for message in caplog.messages if "#ft=" in message]


class TestTheRfbSlideshow:
    def test_each_tick_moves_the_slideshow_on_while_the_app_plays(self, tmp_path):
        slideshow = Mock(spec=RfbSlideshow)
        runner = make_runner(tmp_path, rfb_slideshow=slideshow)

        runner.tick()

        assert slideshow.tick.call_args.kwargs["held"] is False

    def test_omnipause_holds_the_slideshow(self, tmp_path):
        slideshow = Mock(spec=RfbSlideshow)
        runner = make_runner(tmp_path, rfb_slideshow=slideshow)
        write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))

        runner.tick()

        assert slideshow.tick.call_args.kwargs["held"] is True

    def test_origenerator_in_the_browsers_place_holds_the_slideshow_under_it(self, tmp_path):
        slideshow = Mock(spec=RfbSlideshow)
        config = make_config(tmp_path, origenerator_enabled=True,
                             origenerator_cmd_file=tmp_path / "origenerator_cmd.txt",
                             origenerator_paused_file=tmp_path / "origenerator_paused.txt")
        runner = make_runner(tmp_path, config=config, origenerator_pid=700,
                             origenerator_already_open=True, rfb_slideshow=slideshow)
        write_shared_state(tmp_path / "shared_state.ini",
                           BridgeState(rfb_mode=RfbMode.ORIGENERATOR))

        runner.tick()

        assert slideshow.tick.call_args.kwargs["held"] is True

    def test_a_tab_a_lock_opened_gets_the_slideshows_full_interval(self, tmp_path):
        slideshow = Mock(spec=RfbSlideshow)
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND, rfb_slideshow=slideshow,
                             rfb_shortcut=Shortcut(target="chrome.exe", work_dir="", arguments=""))
        runner._pending_rfb_urls = ["file:///tab.html"]

        with (
            patch("fun_time.windows_bridge_dispatch_loop.window_exists", return_value=True),
            patch("fun_time.windows_bridge_dispatch_loop.force_foreground_window", return_value=True),
            patch("fun_time.windows_bridge_dispatch_loop.open_rfb_tab"),
        ):
            runner._flush_rfb_tabs()

        slideshow.restart.assert_called_once()

    def test_the_slideshow_ends_with_the_session(self, tmp_path):
        slideshow = Mock(spec=RfbSlideshow)
        runner = make_runner(tmp_path, rfb_slideshow=slideshow)

        runner.close()

        slideshow.stop.assert_called_once()


class _Sightings:
    def __init__(self, *seen: Seen) -> None:
        self._seen = list(seen)
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def take(self) -> list[Seen]:
        taken, self._seen = self._seen, []
        return taken

    def stop(self) -> None:
        self.stopped = True


def _a_click_into_a_text_field(window: int, process: int = 0) -> _Sightings:
    pressed_at = time.monotonic() - 1.0
    return _Sightings(
        Seen(Sighting.PRESS, window, process, pressed_at, with_the_text_pointer=True),
        Seen(Sighting.CARET_SHOWN, window, process, pressed_at + 0.002),
        Seen(Sighting.RELEASE, window, process, pressed_at + 0.08),
    )


class TestAClickIntoATextField:
    def test_one_in_the_rfb_enters_omnipause(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND,
                             press_and_caret_watch=_a_click_into_a_text_field(RFB_HWND))

        runner.tick()

        assert runner.state.omni_paused
        assert "suspend_hotkeys" in _mailbox(runner)

    def test_one_in_any_window_of_the_hosted_origenerator_enters_omnipause(self, tmp_path):
        origenerator_process, its_other_window = 9009, 8002
        config = make_config(tmp_path, origenerator_enabled=True,
                             origenerator_paused_file=tmp_path / "origenerator_paused.txt")
        runner = make_runner(
            tmp_path, config=config, origenerator_pid=HOSTED_PID,
            press_and_caret_watch=_a_click_into_a_text_field(its_other_window,
                                                              origenerator_process))

        with (
            patch("fun_time.fun_time_windows.find_window_for_process", side_effect=lookup_hosted),
            patch("fun_time.fun_time_windows.window_process",
                  side_effect={HOSTED_HWND: origenerator_process}.get),
        ):
            runner.tick()

        assert runner.state.omni_paused

    def test_one_in_a_chrome_window_of_his_own_leaves_the_app_playing(self, tmp_path):
        his_own_window = 55555
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND,
                             press_and_caret_watch=_a_click_into_a_text_field(his_own_window))

        runner.tick()

        assert not runner.state.omni_paused

    def test_one_while_the_app_is_paused_asks_the_dashboard_nothing(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND,
                             press_and_caret_watch=_a_click_into_a_text_field(RFB_HWND))
        write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))

        with patch.object(runner, "_echo_the_press_best_effort") as press:
            runner.tick()

        press.assert_not_called()
        assert runner.state.omni_paused

    def test_the_log_says_what_paused_the_app(self, tmp_path, caplog):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND,
                             press_and_caret_watch=_a_click_into_a_text_field(RFB_HWND))

        with caplog.at_level(logging.INFO):
            runner.tick()

        assert "A click into a text field enters OmniPause" in caplog.messages

    def test_the_watch_starts_with_the_loop(self, tmp_path):
        watch = _Sightings()
        runner = make_runner(tmp_path, press_and_caret_watch=watch)
        runner.stop()

        runner.run()

        assert watch.started

    def test_the_watch_ends_with_the_session(self, tmp_path):
        watch = _Sightings()
        runner = make_runner(tmp_path, press_and_caret_watch=watch)

        runner.close()

        assert watch.stopped


class TestWhatArrivesInTheLibrary:
    def test_the_libraries_are_looked_at_from_a_thread_of_their_own_for_as_long_as_the_loop_runs(
            self, tmp_path):
        runner = make_runner(tmp_path)
        runner.stop()

        with patch("fun_time.windows_bridge_dispatch_loop.start_daemon_thread") as start:
            runner.run()

        looking = start.call_args.kwargs
        assert looking["target"] is keep_looking
        assert looking["args"] == (runner.latest_arrivals.look, runner._stop)

    def test_each_tick_hands_it_to_the_app_holding_the_satellites_on_their_way_home(
            self, tmp_path):
        runner = make_runner(tmp_path)
        runner.homecoming.expect((FunTimeFunestra.PORTRAIT,), runner.config, now=time.monotonic() + 3600)

        with patch("fun_time.funestra_handover.let_go_since", return_value=False), \
             patch.object(runner.latest_arrivals, "deliver") as deliver:
            runner.tick()

        assert deliver.call_args.args == (runner.state,)
        assert set(deliver.call_args.kwargs["coming_home"]) == {FunTimeFunestra.PORTRAIT}


class TestModeSwitchVisibility:
    def _run_mode_switch(self, tmp_path, *, from_mode, command, integration=False):
        runner = make_runner(tmp_path, env=SessionEnvironment(integration=integration))
        runner.state = BridgeState(main_mode=from_mode)

        calls: list[tuple[str, int]] = []
        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.restore_window",
                   side_effect=lambda h, **kw: calls.append(("show", h))), \
             patch("fun_time.fun_time_windows.minimize_window",
                   side_effect=lambda h, **kw: calls.append(("hide", h))), \
             patch("fun_time.fun_time_windows.activate_window",
                   side_effect=lambda h: calls.append(("activate", h))), \
             patch("fun_time.fun_time_windows.set_always_on_top"):
            runner._dispatch(parse_command(command))
            runner.tick()

        assert runner.state.main_mode == {
            "genau_activate": "genau", "main_kino_activate": "kino",
        }[command]
        return calls, runner.config.main_funestra_cmd_file.read_text(encoding="utf-8").split()

    def test_genau_activate_tells_the_main_funestras_window_to_show_genau(self, tmp_path):
        calls, told = self._run_mode_switch(
            tmp_path, from_mode="kino", command="genau_activate",
        )
        assert calls == [
            ("show", MAIN_FUNESTRA_HWND),
            ("activate", MAIN_FUNESTRA_HWND),
        ]
        assert told[:2] == ["SHOW", "genau"]

    def test_main_kino_activate_tells_the_main_funestras_window_to_show_kino(self, tmp_path):
        calls, told = self._run_mode_switch(
            tmp_path, from_mode="genau", command="main_kino_activate",
        )
        assert calls == [
            ("show", MAIN_FUNESTRA_HWND),
            ("activate", MAIN_FUNESTRA_HWND),
        ]
        assert told[:2] == ["SHOW", "kino"]

    def test_activation_suppressed_during_integration_runs(self, tmp_path):
        calls, _told = self._run_mode_switch(
            tmp_path, from_mode="kino", command="genau_activate",
            integration=True,
        )
        assert calls == [("show", MAIN_FUNESTRA_HWND)]


class TestResolveRole:
    def test_a_hidden_window_no_lookup_finds_is_shown_by_its_kept_hwnd(self, tmp_path):
        runner = make_runner(tmp_path)

        with patch("fun_time.fun_time_windows.find_window_by_pid",
                   side_effect=lookup_pid):
            assert runner.windows.hwnd("main_funestra") == MAIN_FUNESTRA_HWND

        shown: list[int] = []
        show_op = WindowOp(op="show_window", key="main_funestra")
        with _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.fun_time_windows.restore_window", side_effect=lambda h, **kw: shown.append(h)), \
             patch("fun_time.windows_bridge_dispatch_loop.dispatch_command",
                   return_value=(runner.state, [show_op])):
            assert runner.windows.hwnd("main_funestra") == MAIN_FUNESTRA_HWND
            runner._dispatch(parse_command("main_kino_activate"))

        assert shown == [MAIN_FUNESTRA_HWND]


class TestBrowseLibrary:
    def test_a_browse_leaves_the_satellites_and_voice_playing(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        dispatched = [c[0][0].text for c in mock_dispatch.call_args_list]
        assert "enter_omnipause" not in dispatched
        assert "omnipause_toggle" not in dispatched
        assert runner.state.omni_paused is False

    def test_removes_topmost_from_all_managed_windows(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.set_always_on_top",
                   side_effect=lambda hwnd, on_top: topmost_calls.append((hwnd, on_top))), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        removed = {h for h, v in topmost_calls if not v}
        assert removed == TOPMOST_HWNDS

    def test_the_browser_stands_over_the_window_its_pick_will_play_in(self, tmp_path):
        config = make_config(tmp_path, python_exe=r"C:\python.exe")
        runner = make_runner(tmp_path, config=config, manifest_path=tmp_path / "launch.ini")
        runner.state = BridgeState(omni_paused=False)

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.window_rect", return_value=(0, 400, 1080, 1520)), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None) as mock_browse:
            runner._handle_browse_library()

        mock_browse.assert_called_once_with(
            tmp_path / "launch.ini", r"C:\python.exe",
            BrowseOpening(over=(0, 400, 1080, 1520), playing="", flicks=False),
            runner=runner._run_browser,
        )

    def test_browses_from_the_folder_the_main_funestra_is_playing_in(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        runner.config.main_funestra_status_file.write_text(
            "video=C:/videos/big_batch/beta.mp4\n", encoding="utf-8",
        )

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library",
                   return_value=None) as mock_browse:
            runner._handle_browse_library()

        assert mock_browse.call_args.args[2].playing == "C:/videos/big_batch/beta.mp4"

    def test_the_restack_on_leaving_omnipause_does_not_bury_an_open_browse(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        runner._browser_process = Mock(pid=BROWSE_PID, **{"poll.return_value": None})

        with patch.object(runner.windows, "restore_parked"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_for_process",
                   return_value=BROWSE_HWND) as lookup, \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as on_top:
            runner._dispatch(parse_command("omnipause_toggle"))

        assert on_top.call_args_list == [((BROWSE_HWND, True), {})]
        assert lookup.call_args_list == [((BROWSE_PID,), {})]

    def test_nothing_is_promoted_when_no_browse_is_open(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)

        with patch.object(runner.windows, "restore_parked"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as on_top:
            runner._dispatch(parse_command("omnipause_toggle"))

        assert on_top.call_args_list == []

    def test_an_exited_browses_pid_is_never_looked_up_for_a_window(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        runner._browser_process = Mock(pid=BROWSE_PID, **{"poll.return_value": 0})

        with patch.object(runner.windows, "restore_parked"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_for_process",
                   return_value=BROWSE_HWND) as lookup, \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as on_top:
            runner._dispatch(parse_command("omnipause_toggle"))

        assert lookup.call_args_list == [], "the pid may be another program's by now"
        assert on_top.call_args_list == []

    def test_sends_selected_file_to_main_funestra_by_default(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        video = tmp_path / "videos" / "videos" / "movie.mp4"
        video.parent.mkdir(parents=True)
        video.write_text("x", encoding="utf-8")
        mirrored = tmp_path / "videos" / "scripts" / "scripts" / "movie.funscript"
        mirrored.parent.mkdir(parents=True)
        mirrored.write_text("{}", encoding="utf-8")

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=str(video)):
            runner._handle_browse_library()

        command = runner.config.main_funestra_cmd_file.read_text(encoding="utf-8")
        assert command == f"PLAY_FILE {video}\t{mirrored}\n"

    def test_sends_selected_file_to_main_funestra_in_kino_mode(self, tmp_path):
        config = make_config(tmp_path, main_sources=r"C:\videos")
        runner = make_runner(tmp_path, config=config)
        runner.state = BridgeState(omni_paused=False, main_mode=MainMode.KINO)

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=r"C:\videos\movie.mp4"):
            runner._handle_browse_library()

        assert runner.config.main_funestra_cmd_file.read_text(
            encoding="utf-8") == "PLAY_FILE C:\\videos\\movie.mp4\n"

    def test_genau_browses_its_videos_over_the_main_funestras_window_from_the_video_it_has_up(self, tmp_path):
        config = make_config(tmp_path, python_exe=r"C:\python.exe")
        runner = make_runner(tmp_path, config=config, manifest_path=tmp_path / "launch.ini")
        runner.state = BridgeState(omni_paused=False, main_mode=MainMode.GENAU)
        runner.config.genau_status_file.write_text(
            "flick=C:/library/genau/flicks/Scene One.mp4\n", encoding="utf-8")

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch.object(runner.windows, "hwnd", side_effect=lambda window_name: {"main_funestra": MAIN_FUNESTRA_HWND}.get(window_name, 0)), \
             patch("fun_time.windows_bridge_dispatch_loop.window_rect",
                   side_effect=lambda hwnd: (0, 400, 1080, 1520) if hwnd == MAIN_FUNESTRA_HWND else None), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None) as mock_browse:
            runner._handle_browse_library()

        mock_browse.assert_called_once_with(
            tmp_path / "launch.ini", r"C:\python.exe",
            BrowseOpening(over=(0, 400, 1080, 1520), playing="C:/library/genau/flicks/Scene One.mp4",
                          flicks=True),
            runner=runner._run_browser,
        )

    def test_a_video_picked_in_genau_mode_goes_to_genau_not_the_main_funestra(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False, main_mode=MainMode.GENAU)

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library",
                   return_value=r"C:\library\genau\flicks\Scene One.mp4"):
            runner._handle_browse_library()

        assert runner.config.genau_cmd_file.read_text(
            encoding="utf-8") == "PLAY_FILE C:\\library\\genau\\flicks\\Scene One.mp4\n"
        assert not runner.config.main_funestra_cmd_file.exists()

    def test_does_not_play_anything_on_cancel(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        assert not runner.config.main_funestra_cmd_file.exists()

    def test_restores_topmost_after_the_pick(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.set_always_on_top",
                   side_effect=lambda hwnd, on_top: topmost_calls.append((hwnd, on_top))), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        restored = {h for h, v in topmost_calls if v}
        assert restored == TOPMOST_HWNDS

    def test_restores_the_main_funestras_band_in_genau_mode_too(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False, main_mode=MainMode.GENAU)

        topmost_calls = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.set_always_on_top",
                   side_effect=lambda hwnd, on_top: topmost_calls.append((hwnd, on_top))), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        restored = {h for h, v in topmost_calls if v}
        assert restored == TOPMOST_HWNDS

    def test_the_hotkeys_that_would_eat_the_browsers_keys_are_suspended_for_it(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        suspends: list[str] = []

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library",
                   side_effect=lambda *a, **kw: suspends.append(
                       _mailbox(runner))):
            runner._handle_browse_library()

        assert suspends == [["suspend_hotkeys"]]
        assert _mailbox(runner) == ["suspend_hotkeys", "unsuspend_hotkeys"]

    def test_leaves_the_suspended_hotkeys_alone_when_already_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)

        with _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        assert not runner.ahk_cmd_file.exists()

    def test_topmost_removed_before_the_browser_opens(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        call_log: list[str] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.set_always_on_top", side_effect=lambda h, v: call_log.append(f"topmost_{v}")), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", side_effect=lambda *a, **kw: (call_log.append("browse"), None)[-1]):
            runner._handle_browse_library()

        assert "browse" in call_log
        first_remove = next(i for i, c in enumerate(call_log) if c == "topmost_False")
        assert first_remove < call_log.index("browse")

    def test_a_browse_under_omnipause_never_puts_the_lowered_windows_back_on_top(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)

        with patch("fun_time.fun_time_windows.find_window_by_pid", return_value=MAIN_FUNESTRA_HWND), \
             patch("fun_time.windows_bridge_dispatch_loop.window_rect", return_value=(0, 0, 800, 600)), \
             patch("fun_time.fun_time_windows.set_always_on_top") as mock_topmost, \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None) as mock_browse:
            runner._handle_browse_library()

        mock_topmost.assert_not_called()
        mock_browse.assert_called_once()

    def test_browses_unplaced_when_the_primary_display_cannot_be_found(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None) as mock_browse:
            runner._handle_browse_library()

        assert mock_browse.call_args.args[2].over is None

    def test_a_second_browse_while_one_is_open_is_dropped(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)

        browses: list[str] = []
        first_browse_open = threading.Event()
        let_the_browse_close = threading.Event()

        def a_browser_the_user_is_sitting_in(*_args, **_kwargs):
            browses.append("open")
            first_browse_open.set()
            let_the_browse_close.wait(timeout=10.0)

        with patch("fun_time.windows_bridge_dispatch_loop.browse_library",
                   side_effect=a_browser_the_user_is_sitting_in):
            first = threading.Thread(target=runner._handle_browse_library)
            first.start()
            assert first_browse_open.wait(timeout=10.0)

            runner._handle_browse_library()

            let_the_browse_close.set()
            first.join(timeout=10.0)

        assert browses == ["open"]

    def test_browse_library_routed_from_tick(self, tmp_path):
        runner = make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("browse_library", encoding="utf-8")

        with patch.object(runner, "_handle_browse_library") as mock_handle:
            runner.tick()
            wait_until(lambda: mock_handle.call_count >= 1, timeout=10.0)

        mock_handle.assert_called_once()


class TestBrowsingFromTheHeadset:
    def _runner(self, tmp_path):
        return make_runner(
            tmp_path, config=make_config(tmp_path, vr_main_funestra=True),
            main_funestra_pid=0, portrait_pid=0, landscape_pid=0, dashboard_pid=0,
        )

    def _press(self, runner, *commands):
        with _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.fun_time_windows.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library",
                   return_value=None) as desktop:
            for command in commands:
                runner._handle_command(command)
            _join_the_browse_threads()
        return desktop

    def test_a_headset_browse_opens_the_panel_the_funestra_hangs(self, tmp_path):
        runner = self._runner(tmp_path)

        self._press(runner, "browse_library")

        assert read_flag(tmp_path / LIBRARY_OPEN_FILENAME, default=False)

    def test_a_headset_browse_launches_no_desktop_window_and_takes_no_keys(self, tmp_path):
        runner = self._runner(tmp_path)

        desktop = self._press(runner, "browse_library")

        desktop.assert_not_called()
        assert not runner.ahk_cmd_file.exists()

    def test_a_second_browse_puts_the_panel_away(self, tmp_path):
        runner = self._runner(tmp_path)

        self._press(runner, "browse_library", "browse_library")

        assert not read_flag(tmp_path / LIBRARY_OPEN_FILENAME, default=True)

    def test_the_panels_own_close_puts_it_away_and_never_opens_it(self, tmp_path):
        runner = self._runner(tmp_path)

        self._press(runner, "browse_library", "browse_library_close", "browse_library_close")

        assert not read_flag(tmp_path / LIBRARY_OPEN_FILENAME, default=True)


class TestWhatAPausedRoomCarriesOut:
    def test_a_command_said_before_the_pause_is_carried_out_when_it_arrives_during_it(
        self, tmp_path,
    ):
        runner = make_runner(tmp_path)
        now = [5.0]
        vc = VoiceController(
            cmd_file=tmp_path / "dashboard_cmd.txt",
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
            clock=lambda: now[0],
        )
        runner.voice.controller = vc
        runner.tick()
        now[0] = 10.0
        runner.state = replace(runner.state, omni_paused=True)
        runner.tick()

        vc.handle_heard(Heard(Recognition(phrase="landscape next"), spoken_at=7.0, peak=2000,
                              audio=b"", candidates={}))
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        assert [call.args[0].text for call in mock_dispatch.call_args_list] == ["landscape_next"]

    def test_keeps_the_mouse_live_under_omnipause(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner._handle_command("portrait_lock_video|C:/clip.mp4")
        assert [call.args[0].text for call in mock_dispatch.call_args_list] == [
            "portrait_lock_video|C:/clip.mp4"], (
            "a press cannot misfire the way a heard phrase can, so no pause holds one back")

    def test_a_spoken_resume_still_un_pauses(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        with patch.object(windows_bridge_dispatch_loop, "_toggle_omnipause") as mock_toggle:
            runner._handle_command("play", spoken_at=123.0)
        mock_toggle.assert_called_once()

    def test_a_spoken_quit_still_quits(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        runner._handle_command("quit", spoken_at=123.0)
        assert (tmp_path / "ahk_cmd.txt").read_text(encoding="utf-8") == "end_session"

    @pytest.mark.parametrize(("command", "said", "verb"), [
        ("recenter_view", "recenter", "RECENTER"), ("vr_reset", "VR reset", "LAYOUT_RESET")])
    def test_a_spoken_recenter_or_vr_reset_still_reaches_the_headset(
        self, tmp_path, command, said, verb,
    ):
        runner = make_runner(tmp_path, config=make_config(tmp_path, vr_main_funestra=True))
        runner.state = BridgeState(omni_paused=True)
        (tmp_path / "dashboard_cmd.txt").write_text(
            format_spoken_command(command, spoken_at=123.0, said=said), encoding="utf-8")

        runner.tick()

        assert verb in (tmp_path / "main_player_cmd.txt").read_text(encoding="utf-8").split()

    def test_outside_omnipause_a_spoken_command_dispatches(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner._handle_command("landscape_next", spoken_at=123.0)
        assert mock_dispatch.call_args[0][0].text == "landscape_next"


class TestWithTheHeadsetOff:
    def test_the_headset_coming_off_holds_the_keys(self, tmp_path):
        runner = make_runner(tmp_path)

        runner._handle_command(HEADSET_OFF)

        assert _mailbox(runner) == ["headset_off"]

    def test_the_headset_going_back_on_lets_them_go(self, tmp_path):
        runner = make_runner(tmp_path)

        runner._handle_command(HEADSET_OFF)
        runner._handle_command(HEADSET_ON)

        assert _mailbox(runner) == ["headset_off", "headset_on"]

    def test_the_headset_saying_it_again_changes_nothing(self, tmp_path):
        runner = make_runner(tmp_path)

        runner._handle_command(HEADSET_OFF)
        runner._handle_command(HEADSET_OFF)

        assert _mailbox(runner) == ["headset_off"]

    def test_what_the_headset_says_is_never_dispatched_as_a_command(self, tmp_path):
        runner = make_runner(tmp_path)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            runner._handle_command(HEADSET_OFF)
            runner._handle_command(HEADSET_ON)
        mock_dispatch.assert_not_called()

    def test_a_spoken_quit_with_the_headset_off_still_quits(self, tmp_path):
        runner = make_runner(tmp_path)
        runner._handle_command(HEADSET_OFF)

        runner._handle_command("quit", spoken_at=123.0)

        assert _mailbox(runner)[-1] == "end_session"

    def test_back_on_a_spoken_command_is_acted_on_again(self, tmp_path):
        runner = make_runner(tmp_path)
        runner._handle_command(HEADSET_OFF)
        runner._handle_command(HEADSET_ON)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner._handle_command("landscape_next", spoken_at=123.0)
        assert mock_dispatch.call_args[0][0].text == "landscape_next"

    def test_the_microphone_hears_only_the_ways_out_while_the_headset_is_off(self, tmp_path):
        runner = make_runner(tmp_path)
        runner._last_watch_sample = float("inf")
        vc_cmd = tmp_path / "vc_cmd.txt"
        runner.voice.controller = VoiceController(
            cmd_file=vc_cmd,
            settings=VoiceControlConfig(enabled=True, model_path="unused"),
            clock=lambda: 0.0,
        )

        runner._handle_command(HEADSET_OFF)
        runner.voice.controller._write_spoken("landscape next", spoken_at=1.0)

        assert not vc_cmd.exists()


class TestIdempotentVoiceCommands:
    def test_pause_enters_omnipause_when_not_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        with patch.object(windows_bridge_dispatch_loop, "_toggle_omnipause") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("pause", encoding="utf-8")
            runner.tick()
        mock_toggle.assert_called_once()

    def test_pause_noop_when_already_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        with patch.object(windows_bridge_dispatch_loop, "_toggle_omnipause") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("pause", encoding="utf-8")
            runner.tick()
        mock_toggle.assert_not_called()

    def test_play_leaves_omnipause_when_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        with patch.object(windows_bridge_dispatch_loop, "_toggle_omnipause") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("play", encoding="utf-8")
            runner.tick()
        mock_toggle.assert_called_once()

    def test_play_noop_when_not_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        with patch.object(windows_bridge_dispatch_loop, "_toggle_omnipause") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("play", encoding="utf-8")
            runner.tick()
        mock_toggle.assert_not_called()


    def test_enter_omnipause_enters_when_not_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("enter_omnipause", encoding="utf-8")

        with _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.fun_time_windows.set_always_on_top"):
            runner.tick()

        assert runner.state.omni_paused is True

    def test_enter_omnipause_removes_topmost(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("enter_omnipause", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []

        with patch("fun_time.fun_time_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.fun_time_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.fun_time_windows.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert {h for h, v in topmost_calls if v is False} == TOPMOST_HWNDS

    def test_enter_omnipause_noop_when_already_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("enter_omnipause", encoding="utf-8")

        with patch.object(runner, "_dispatch") as mock_d:
            runner.tick()

        mock_d.assert_not_called()


    def test_a_paused_session_whose_osr2_is_still_out_gets_its_relief(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("relief_omnipause", encoding="utf-8")

        with patch.object(runner, "_dispatch") as mock_d:
            runner.tick()

        assert [call.args[0].text for call in mock_d.call_args_list] == ["relief_omnipause"]

    def test_relief_omnipause_logs_the_topmost_state_like_any_other_entry(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("relief_omnipause", encoding="utf-8")

        with patch.object(runner, "_dispatch"), \
             patch.object(runner, "_log_topmost_state") as mock_log:
            runner.tick()

        mock_log.assert_called_once_with("post-enter")


    def test_which_funestras_a_sided_fmode_names_is_left_to_the_dispatch(self, tmp_path):
        runner = make_runner(tmp_path)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_fmode_on", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with(parse_command("portrait_fmode_on"), None)

    def test_satellites_fmode_reaches_the_f_mode_table_as_its_portrait_and_landscape_halves(self, tmp_path):
        runner = make_runner(tmp_path)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("satellites_fmode", encoding="utf-8")
            runner.tick()
        assert [call.args[0].text for call in mock_d.call_args_list] == [
            "portrait_fmode", "landscape_fmode",
        ]


    def test_the_robot_hand_running_under_kino_swallows_no_switch_to_genau(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode=MainMode.KINO)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with(parse_command("genau_activate"), None)

    def test_a_switch_to_the_current_mode_is_left_for_the_planner_to_drop(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode=MainMode.GENAU)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with(parse_command("genau_activate"), None)


    def test_a_locked_dashboard_state_file_warns_once_a_minute_not_never(self, tmp_path, caplog):
        runner = make_runner(tmp_path, dashboard_enabled=True)
        (tmp_path / "dashboard_state.ini").mkdir()
        with caplog.at_level(logging.WARNING, logger="fun_time.windows_bridge_dispatch_loop"):
            runner._update_dashboard()
            runner._update_dashboard()
        warnings = [r for r in caplog.records if "dashboard" in r.message]
        assert len(warnings) == 1

    def test_a_locked_dashboard_state_file_warns_again_a_minute_later(self, tmp_path, caplog):
        runner = make_runner(tmp_path, dashboard_enabled=True)
        (tmp_path / "dashboard_state.ini").mkdir()
        with caplog.at_level(logging.WARNING, logger="fun_time.windows_bridge_dispatch_loop"):
            runner._update_dashboard()
            runner._last_dashboard_warning -= 61.0
            runner._update_dashboard()
        warnings = [r for r in caplog.records if "dashboard" in r.message]
        assert len(warnings) == 2

    def test_a_bug_in_the_snapshot_writer_is_not_swallowed(self, tmp_path):
        runner = make_runner(tmp_path, dashboard_enabled=True)
        with patch(
            "fun_time.windows_bridge_dispatch_loop.write_dashboard_snapshot",
            side_effect=TypeError("renamed keyword"),
        ), pytest.raises(TypeError):
            runner._update_dashboard()

    def test_the_snapshot_says_whether_every_funestra_has_nothing_to_reset(self, tmp_path):
        runner = make_runner(tmp_path, dashboard_enabled=True)
        runner.config.main_funestra_status_file.write_text(
            "video=C:/v/n.mp4\nlocked=0\nspeed=1.0\nlength_mode=mixed\n", encoding="utf-8")

        runner._update_dashboard()
        assert load_dashboard_snapshot(runner.config.dashboard_state_file).nothing_to_reset is True

        runner.state = replace(runner.state, portrait=SatelliteState(locked=True))
        runner._update_dashboard()
        assert load_dashboard_snapshot(runner.config.dashboard_state_file).nothing_to_reset is False

    def test_a_satellite_off_normal_speed_leaves_the_app_something_to_reset(self, tmp_path):
        runner = make_runner(tmp_path, dashboard_enabled=True)
        runner.config.main_funestra_status_file.write_text(
            "video=C:/v/n.mp4\nlocked=0\nspeed=1.0\nlength_mode=mixed\n", encoding="utf-8")
        runner.config.landscape.status_file.write_text(
            "video=C:/v/l.mp4\nspeed=0.5\n", encoding="utf-8")

        runner._update_dashboard()

        assert load_dashboard_snapshot(runner.config.dashboard_state_file).nothing_to_reset is False

    def test_the_dashboard_is_told_what_holds_the_browsers_place_and_whether_origenerator_is_up(
            self, tmp_path):
        runner = make_runner(tmp_path, dashboard_enabled=True, config=_hosting(tmp_path))
        runner.state = replace(runner.state, rfb_mode=RfbMode.ORIGENERATOR,
                               origenerator_ready=True)

        runner._update_dashboard()

        snapshot = load_dashboard_snapshot(runner.config.dashboard_state_file)
        assert (snapshot.rfb_mode, snapshot.origenerator_ready) == (RfbMode.ORIGENERATOR, True)

    def test_an_app_hosting_origenerator_never_says_it_has_nothing_to_reset(self, tmp_path):
        runner = make_runner(tmp_path, dashboard_enabled=True, config=make_config(
            tmp_path, origenerator_enabled=True,
            origenerator_cmd_file=tmp_path / "origenerator_cmd.txt"))
        runner.config.main_funestra_status_file.write_text(
            "video=C:/v/n.mp4\nlocked=0\nspeed=1.0\nlength_mode=mixed\n", encoding="utf-8")
        runner.state = _in_origenerator_mode(runner.state, FunTimeFunestra.LANDSCAPE)

        runner._update_dashboard()

        assert load_dashboard_snapshot(runner.config.dashboard_state_file).nothing_to_reset is False

    def test_the_dashboards_f_mode_lights_off_the_hosted_shows_in_origenerator_mode(
        self, tmp_path,
    ):
        runner = make_runner(tmp_path, dashboard_enabled=True, config=_hosting(tmp_path))
        runner.state = _in_origenerator_mode(replace(runner.state, main_scripted_filter=True))
        for funestra in FunTimeFunestra.SATELLITES:
            publish_the_show(runner.config, funestra, f_mode=True)

        runner._update_dashboard()

        assert load_dashboard_snapshot(runner.config.dashboard_state_file).f_mode is True

    def test_the_bars_one_f_mode_light_stays_dark_while_any_funestra_is_out_of_it(self, tmp_path):
        runner = make_runner(tmp_path, dashboard_enabled=True)
        runner.state = replace(runner.state, main_scripted_filter=True).with_satellite(
            FunTimeFunestra.PORTRAIT, favorites_filter=True)

        runner._update_dashboard()

        assert load_dashboard_snapshot(runner.config.dashboard_state_file).f_mode is False


    def test_an_unknown_op_is_an_error_not_an_ahk_verb(self, tmp_path, caplog):
        runner = make_runner(tmp_path)
        with patch(
            "fun_time.windows_bridge_dispatch_loop.dispatch_command",
            return_value=(runner.state, [WindowOp(op="frobnicate")]),
        ), caplog.at_level(logging.ERROR, logger="fun_time.windows_bridge_dispatch_loop"):
            (tmp_path / "dashboard_cmd.txt").write_text("portrait_next", encoding="utf-8")
            runner.tick()
        assert not runner.ahk_cmd_file.exists()
        assert any("frobnicate" in record.message for record in caplog.records)

    def test_the_op_interpreter_covers_the_whole_vocabulary(self):
        assert set(_OP_HANDLERS) == set(Op)
        assert {Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS} == _AHK_PASSTHROUGH_OPS


    def test_save_video_runs_on_a_worker_thread_and_flashes_the_result(self, tmp_path, caplog):
        runner = make_runner(tmp_path)
        with patch(
            "fun_time.genaumacher_save.save_clip_session",
            return_value="Genaumacher: fabricated-session",
        ) as mock_save, caplog.at_level(
            logging.INFO, logger="fun_time.genaumacher_save"
        ):
            (tmp_path / "dashboard_cmd.txt").write_text("genaumacher_save", encoding="utf-8")
            runner.tick()
            wait_until(lambda: mock_save.call_count >= 1, timeout=10.0)
            wait_until(
                lambda: any(r.message == "Genaumacher: fabricated-session" for r in caplog.records),
                timeout=10.0,
            )
        mock_save.assert_called_once_with(runner.config)
        flashed = [r for r in caplog.records if r.message == "Genaumacher: fabricated-session"]
        assert flashed[0].source == "main"
        assert not runner.ahk_cmd_file.exists()

    START = "fun_time.windows_bridge_dispatch_loop.broker.start"
    STOP = "fun_time.windows_bridge_dispatch_loop.broker.stop"

    @staticmethod
    def _say(runner, tmp_path, command: str) -> None:
        (tmp_path / "dashboard_cmd.txt").write_text(command, encoding="utf-8")
        runner.tick()

    def test_broker_start_starts_when_not_running(self, tmp_path):
        runner = make_runner(tmp_path)
        with patch(self.START) as mock_start:
            self._say(runner, tmp_path, "broker_start")
        mock_start.assert_called_once_with(runner.config.broker_tray_launcher)

    def test_broker_start_noop_when_already_running(self, tmp_path):
        runner = make_runner(tmp_path)
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")
        with patch(self.START) as mock_start:
            self._say(runner, tmp_path, "broker_start")
        mock_start.assert_not_called()

    def test_starting_never_stops_a_broker_other_programs_may_be_using(self, tmp_path):
        runner = make_runner(tmp_path)
        with patch(self.STOP) as mock_stop, patch(self.START) as mock_start:
            self._say(runner, tmp_path, "broker_start")
        mock_start.assert_called_once()
        mock_stop.assert_not_called()

    def test_broker_panel_starts_a_broker_that_reads_as_dead_without_stopping_it(self, tmp_path):
        runner = make_runner(tmp_path)
        with patch(self.STOP) as mock_stop, patch(self.START) as mock_start:
            self._say(runner, tmp_path, "broker_panel")
        mock_start.assert_called_once_with(runner.config.broker_tray_launcher)
        mock_stop.assert_not_called()

    def test_broker_panel_stops_a_broker_waiting_for_an_unplugged_osr2(self, tmp_path):
        runner = make_runner(tmp_path)
        (tmp_path / "osr2_not_found.txt").write_text(str(time.time()), encoding="utf-8")
        with patch(self.STOP) as mock_stop, patch(self.START) as mock_start:
            self._say(runner, tmp_path, "broker_panel")
        mock_stop.assert_called_once_with(runner.config.broker_state,
                                          runner.config.broker_tray_launcher)
        mock_start.assert_not_called()

    def test_broker_stop_stops_when_running(self, tmp_path):
        runner = make_runner(tmp_path)
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")
        with patch(self.STOP) as mock_stop:
            self._say(runner, tmp_path, "broker_stop")
        mock_stop.assert_called_once()

    def test_broker_stop_noop_when_not_running(self, tmp_path):
        runner = make_runner(tmp_path)
        with patch(self.STOP) as mock_stop:
            self._say(runner, tmp_path, "broker_stop")
        mock_stop.assert_not_called()


class TestWatchTracking:
    def _a_made_up_clock(self, monkeypatch, starting_at: float = 0.0) -> dict:
        fake_now = {"t": starting_at}
        monkeypatch.setattr(
            "fun_time.windows_bridge_dispatch_loop.time.monotonic", lambda: fake_now["t"]
        )
        return fake_now

    def _run_samples(self, runner, monkeypatch, timeline):
        fake_now = self._a_made_up_clock(monkeypatch)
        for t, path, fraction in timeline:
            fake_now["t"] = t
            _write_satellite_status(runner.config.portrait.status_file, path, fraction=fraction)
            runner.tick()

    def test_tick_records_a_completion_for_a_fully_watched_video(self, tmp_path, monkeypatch):
        runner = make_runner(tmp_path)
        a = tmp_path / "a.mp4"
        a.write_text("x", encoding="utf-8")

        self._run_samples(runner, monkeypatch, [
            (100.0, str(a), 0.1),
            (101.1, str(a), 0.9),
            (102.2, "", None),
            (103.3, str(tmp_path / "b.mp4"), 0.0),
        ])

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(a))]["completions"] == 1

    def test_user_next_marks_an_early_departed_video_as_skipped(self, tmp_path, monkeypatch):
        runner = make_runner(tmp_path)
        a = tmp_path / "a.mp4"
        a.write_text("x", encoding="utf-8")

        fake_now = self._a_made_up_clock(monkeypatch, 100.0)
        _write_satellite_status(runner.config.portrait.status_file, str(a), fraction=0.2)
        runner.tick()
        runner._dispatch(parse_command("portrait_next"))
        fake_now["t"] = 101.1
        _write_satellite_status(runner.config.portrait.status_file, str(tmp_path / "b.mp4"), fraction=0.0)
        runner.tick()

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(a))]["skips"] == 1

    def test_trash_suppresses_classifying_the_discarded_video(self, tmp_path, monkeypatch):
        config = make_config(tmp_path, trash_dir=tmp_path / "trash")
        runner = make_runner(tmp_path, config=config)
        a = tmp_path / "a.mp4"
        a.write_text("x", encoding="utf-8")

        fake_now = self._a_made_up_clock(monkeypatch, 100.0)
        _write_satellite_status(runner.config.portrait.status_file, str(a), fraction=0.2)
        runner.tick()
        runner._dispatch(parse_command("portrait_trash"))
        fake_now["t"] = 101.1
        _write_satellite_status(runner.config.portrait.status_file, str(tmp_path / "b.mp4"), fraction=0.0)
        runner.tick()

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}


    def _holding_a_slideshow(self, tmp_path):
        runner = make_runner(tmp_path, config=make_config(
            tmp_path, origenerator_enabled=True,
            origenerator_cmd_file=tmp_path / "origenerator_cmd.txt"))
        runner.state = _in_origenerator_mode(
            replace(runner.state, origenerator_ready=True), FunTimeFunestra.PORTRAIT)
        return runner

    def test_a_video_watched_to_the_end_in_a_slideshow_is_a_completion(
            self, tmp_path, monkeypatch):
        runner = self._holding_a_slideshow(tmp_path)
        a = tmp_path / "a.mp4"
        a.write_text("x", encoding="utf-8")

        self._run_samples(runner, monkeypatch, [
            (100.0, str(a), 0.1),
            (101.1, str(a), 0.9),
            (102.2, str(tmp_path / "b.mp4"), 0.0),
        ])

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(a))]["completions"] == 1

    def test_next_pressed_early_in_a_slideshows_video_is_a_skip(self, tmp_path, monkeypatch):
        runner = self._holding_a_slideshow(tmp_path)
        a = tmp_path / "a.mp4"
        a.write_text("x", encoding="utf-8")
        fake_now = self._a_made_up_clock(monkeypatch, 100.0)
        _write_satellite_status(runner.config.portrait.status_file, str(a), fraction=0.2)
        runner.tick()

        runner._dispatch(parse_command("portrait_next"))
        fake_now["t"] = 101.1
        _write_satellite_status(runner.config.portrait.status_file, str(tmp_path / "b.mp4"),
                                fraction=0.0)
        runner.tick()

        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8").split() == [
            "portrait_next"]
        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(a))]["skips"] == 1


class TestSeededRoleHwnds:
    def test_startup_seed_lets_a_parked_window_be_shown_again(self, tmp_path):
        runner = make_runner(
            tmp_path,
            hwnds_by_window={"main_funestra": 2001},
        )
        runner.state = BridgeState(main_mode=MainMode.GENAU)
        shown: list[int] = []

        with _finding_no_window_not_even_a_real_fun_times(), \
             patch("fun_time.fun_time_windows.restore_window", side_effect=lambda h, **kw: shown.append(h)):
            assert runner.windows.hwnd("main_funestra") == 2001
            runner._dispatch(parse_command("main_kino_activate"))

        assert shown == [2001]


class TestKinoModeFunscriptHandoff:
    def test_the_tick_arbitrates_for_the_mode_the_session_is_in(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode=MainMode.KINO, omni_paused=True)

        with patch.object(runner.arbiter, "sync") as sync:
            runner.tick()

        sync.assert_called_once_with("kino", paused=True, control="driving",
                                     holder=FunTimeFunestra.MAIN)

    def test_the_tick_tells_the_arbiter_when_a_satellite_funestra_has_the_osr2(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode=MainMode.KINO, osr2_funestra=FunTimeFunestra.LANDSCAPE)

        with patch.object(runner.arbiter, "sync") as sync:
            runner.tick()

        assert sync.call_args.kwargs["holder"] is FunTimeFunestra.LANDSCAPE

    def test_a_command_that_changes_who_drives_is_stated_before_its_window_ops_run(
            self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode=MainMode.GENAU)
        happened = []

        with patch.object(runner.arbiter, "sync",
                          side_effect=lambda *_a, **kw: happened.append(("stated", kw["paused"]))), \
             patch.object(runner.windows, "remove_all_topmost",
                          side_effect=lambda: happened.append(("window ops", None))):
            runner._handle_command("enter_omnipause")

        assert happened[:2] == [("stated", True), ("window ops", None)]

    @pytest.mark.parametrize("device_on", [True, False])
    def test_entering_omnipause_stops_the_hand_and_walks_genaus_image_home_only_with_the_osr2_on(
            self, tmp_path, device_on):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode=MainMode.GENAU)
        if device_on:
            (runner.config.broker_state / OSR2_SERIAL_RX).write_text(str(time.time()),
                                                                     encoding="utf-8")

        with patch.object(runner.windows, "remove_all_topmost"):
            runner._handle_command("enter_omnipause")

        verbs = runner.config.genau_cmd_file.read_text(encoding="utf-8").split()
        assert [verb for verb in verbs if verb in ("PAUSE", "RESUME")] == ["PAUSE"]
        assert ("PARK" in verbs) is device_on

    def test_a_command_that_leaves_who_drives_alone_waits_for_the_tick(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode=MainMode.KINO)

        with patch.object(runner.arbiter, "sync") as sync:
            runner._handle_command("main_next")

        sync.assert_not_called()


class TestTheSatellitesTakeTheMainFunestrasRate:
    def test_a_rate_the_main_funestra_moves_to_reaches_both_satellites_on_the_periodic_sync(
            self, tmp_path):
        runner = make_runner(tmp_path)
        status = tmp_path / "main_player_status.txt"
        status.write_text("video=C:\\clip.mp4\nspeed=1\n", encoding="utf-8")
        _make_the_periodic_sync_due(runner)
        runner.tick()

        status.write_text("video=C:\\clip.mp4\nspeed=0.5\n", encoding="utf-8")
        _make_the_periodic_sync_due(runner)
        runner.tick()

        for satellite in ("portrait", "landscape"):
            sent = (tmp_path / f"{satellite}_cmd.txt").read_text(encoding="utf-8").splitlines()
            assert [verb for verb in sent if verb.startswith("SET_SPEED")] == ["SET_SPEED 0.5"], satellite


class TestSatellitesCommands:
    def test_satellites_next_dispatches_to_each_satellite(self, tmp_path):
        runner = make_runner(tmp_path)
        (tmp_path / "dashboard_cmd.txt").write_text("satellites_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.side_effect = lambda cmd, state, config, target_path="", spoken_at=None, hosted=None: (state, [])
            runner.tick()

        commands = [c[0][0].text for c in mock_dispatch.call_args_list]
        assert commands == ["portrait_next", "landscape_next"]

    def test_lock_satellites_locks_each_unlocked_satellite(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(landscape=SatelliteState(locked=False), portrait=SatelliteState(locked=True))
        (tmp_path / "dashboard_cmd.txt").write_text("satellites_lock_on", encoding="utf-8")

        runner.tick()

        assert [_told(runner, funestra) for funestra in FunTimeFunestra.SATELLITES] == [[], ["LOCK_ON"]]
        assert runner.state.satellite(FunTimeFunestra.LANDSCAPE).locked is True

    def test_unlock_satellites_unlocks_each_locked_satellite(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(landscape=SatelliteState(locked=True), portrait=SatelliteState(locked=True))
        (tmp_path / "dashboard_cmd.txt").write_text("satellites_lock_off", encoding="utf-8")

        runner.tick()

        assert [_told(runner, funestra) for funestra in FunTimeFunestra.SATELLITES] == [
            ["LOCK_OFF", "NEXT"], ["LOCK_OFF", "NEXT"]]


def _told(runner, funestra) -> list[str]:
    cmd_file = runner.config.satellite(funestra).cmd_file
    return cmd_file.read_text(encoding="utf-8").split() if cmd_file.exists() else []


class TestHudPublishing:
    def test_the_tick_feeds_the_huds_the_state_it_is_holding(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(portrait=SatelliteState(locked=True), main_mode=MainMode.KINO)

        with patch.object(runner.hud, "publish_due") as publish:
            runner.tick()

        assert publish.call_args[0][0] == runner.state
        assert "now" in publish.call_args.kwargs


def _a_browse_that_stays_up_until_terminated() -> tuple[Mock, threading.Event]:
    opened = threading.Event()
    terminated = threading.Event()
    process = Mock()
    process.wait.side_effect = lambda: (opened.set(), terminated.wait(10.0))
    process.terminate.side_effect = terminated.set
    return process, opened


class TestBrowserOutlivesNothing:
    def test_quitting_ends_a_browse_launched_after_the_startup_children(self, tmp_path):
        runner = make_runner(tmp_path)
        process, opened = _a_browse_that_stays_up_until_terminated()

        with patch("fun_time.windows_bridge_dispatch_loop.subprocess.Popen",
                   return_value=process) as mock_popen:
            browsing = threading.Thread(target=runner._run_browser, args=(["python"],))
            browsing.start()
            assert opened.wait(2.0), "the browser never started"
            runner.close()
            browsing.join(timeout=2.0)

        mock_popen.assert_called_once()
        process.terminate.assert_called_once()
        assert runner._browser_process is None, "the finished browse is not still held"

    def test_closing_with_no_browse_open_terminates_nothing(self, tmp_path):
        runner = make_runner(tmp_path)

        runner.close()

        assert runner._browser_process is None


class TestOrigeneratorShows:
    def _hosting_runner(self, tmp_path):
        config = make_config(tmp_path, origenerator_enabled=True,
                             origenerator_cmd_file=tmp_path / "origenerator_cmd.txt",
                             origenerator_paused_file=tmp_path / "origenerator_paused.txt")
        runner = make_runner(tmp_path, config=config, origenerator_pid=700)
        runner.state = replace(runner.state, rfb_mode=RfbMode.ORIGENERATOR)
        return runner

    def test_rfb_tabs_hold_while_origenerator_covers_the_browser(self, tmp_path):
        runner = self._hosting_runner(tmp_path)
        runner.rfb_shortcut = Shortcut(target="chrome.exe", work_dir="", arguments="")
        runner._pending_rfb_urls = ["file:///tab.html"]
        runner._flush_rfb_tabs()
        assert runner._pending_rfb_urls == ["file:///tab.html"]


def _genau_publishes(tmp_path, *, locked: bool, flick: str) -> None:
    (tmp_path / "genau_status.txt").write_text(
        f"locked={int(locked)}\nflick={flick}\n", encoding="utf-8")


def _what_the_gallery_heard(tmp_path) -> list[str]:
    heard = tmp_path / "origenerator_cmd.txt"
    return heard.read_text(encoding="utf-8").splitlines() if heard.exists() else []


class TestAGenauLockTakesTheGalleryToTheFlick:
    FIRST = r"C:\library\genau\flicks\wan22_i2v_00007__topaz.mp4"
    NEXT = r"C:\library\genau\flicks\wan22_i2v_00008__topaz.mp4"

    def _genau_beside_the_gallery(self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path), origenerator_pid=700)
        _the_hosted_app_answers(tmp_path)
        runner.state = replace(runner.state, main_mode=MainMode.GENAU,
                               rfb_mode=RfbMode.ORIGENERATOR, origenerator_ready=True)
        return runner

    def test_the_gallery_goes_to_the_flick_genau_moved_on_to_before_locking(self, tmp_path):
        runner = self._genau_beside_the_gallery(tmp_path)
        _genau_publishes(tmp_path, locked=False, flick=self.FIRST)

        runner._dispatch(parse_command("main_lock"))
        runner.tick()
        assert _what_the_gallery_heard(tmp_path) == []

        _genau_publishes(tmp_path, locked=True, flick=self.NEXT)
        runner.tick()
        assert _what_the_gallery_heard(tmp_path) == [f"GO_TO|{self.NEXT}"]

    def test_an_unlock_is_not_followed_though_genau_still_reports_the_lock(self, tmp_path):
        runner = self._genau_beside_the_gallery(tmp_path)
        _genau_publishes(tmp_path, locked=True, flick=self.FIRST)

        runner._dispatch(parse_command("main_lock"))
        runner.tick()

        assert _what_the_gallery_heard(tmp_path) == []

    def test_saying_lock_takes_the_gallery_there_though_genau_already_holds_the_flick(
            self, tmp_path):
        runner = self._genau_beside_the_gallery(tmp_path)
        _genau_publishes(tmp_path, locked=True, flick=self.FIRST)

        runner._dispatch(parse_command("main_lock_on"))
        runner.tick()

        assert _what_the_gallery_heard(tmp_path) == [f"GO_TO|{self.FIRST}"]

    def test_a_status_caught_half_written_is_waited_past(self, tmp_path):
        runner = self._genau_beside_the_gallery(tmp_path)
        _genau_publishes(tmp_path, locked=False, flick=self.FIRST)
        runner._dispatch(parse_command("main_lock"))

        (tmp_path / "genau_status.txt").write_text("", encoding="utf-8")
        runner.tick()
        _genau_publishes(tmp_path, locked=True, flick=self.FIRST)
        runner.tick()

        assert _what_the_gallery_heard(tmp_path) == [f"GO_TO|{self.FIRST}"]

    def test_a_lock_asks_origenerator_to_go_to_the_flick_with_the_browser_in_its_place(
            self, tmp_path):
        runner = self._genau_beside_the_gallery(tmp_path)
        runner.state = replace(runner.state, rfb_mode=RfbMode.RFB)
        _genau_publishes(tmp_path, locked=True, flick=self.FIRST)

        runner._dispatch(parse_command("main_lock_on"))
        runner.tick()

        assert _what_the_gallery_heard(tmp_path) == [f"GO_TO|{self.FIRST}"]

    def test_the_flick_genau_holds_goes_in_the_favorites_as_a_satellites_lock_does(
            self, tmp_path):
        runner = self._genau_beside_the_gallery(tmp_path)
        _genau_publishes(tmp_path, locked=False, flick=self.FIRST)

        with patch("fun_time.lock.mark_favorite") as marked:
            runner._dispatch(parse_command("main_lock"))
            _genau_publishes(tmp_path, locked=True, flick=self.NEXT)
            runner.tick()

        assert [call.args[0] for call in marked.call_args_list] == [self.NEXT]


def _the_main_funestra_publishes(tmp_path, *, locked: bool, video: str) -> None:
    (tmp_path / "main_player_status.txt").write_text(
        f"video={video}\nlocked={int(locked)}\nspeed=1\n", encoding="utf-8")


class TestAMainFunestraLockInKinoOpensWhereMoreOfItIsMade:
    MADE_HERE = r"C:\library\videos\made here\gen_00042_.mp4"

    def _kino_beside_origenerator(self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path), origenerator_pid=700)
        _the_hosted_app_answers(tmp_path)
        runner.state = replace(runner.state, main_mode=MainMode.KINO,
                               rfb_mode=RfbMode.RFB, origenerator_ready=True)
        return runner

    def test_origenerator_is_asked_to_go_to_the_video_once_the_main_funestra_holds_it(
            self, tmp_path):
        runner = self._kino_beside_origenerator(tmp_path)
        _the_main_funestra_publishes(tmp_path, locked=False, video=self.MADE_HERE)

        runner._dispatch(parse_command("main_lock"))
        runner.tick()
        assert _what_the_gallery_heard(tmp_path) == []

        _the_main_funestra_publishes(tmp_path, locked=True, video=self.MADE_HERE)
        runner.tick()
        assert _what_the_gallery_heard(tmp_path) == [f"GO_TO|{self.MADE_HERE}"]

    def test_the_video_goes_in_the_favorites_and_counts_as_a_lock_once_it_is_held(
            self, tmp_path):
        runner = self._kino_beside_origenerator(tmp_path)
        _the_main_funestra_publishes(tmp_path, locked=False, video=self.MADE_HERE)

        with patch("fun_time.lock.mark_favorite") as marked:
            runner._dispatch(parse_command("main_lock"))
            runner.tick()
            assert marked.call_count == 0

            _the_main_funestra_publishes(tmp_path, locked=True, video=self.MADE_HERE)
            runner.tick()
        assert [call.args[:3] for call in marked.call_args_list] == [
            (self.MADE_HERE, runner.config.regen_metadata_root, True)]
        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(self.MADE_HERE)]["locks"] == 1

    def test_a_video_a_website_made_opens_its_page_with_the_browser_put_back_in_its_place(
            self, tmp_path):
        runner = make_runner(
            tmp_path, config=_hosting(tmp_path), origenerator_pid=700, rfb_hwnd=12345,
            rfb_shortcut=Shortcut(target=r"C:\Chrome\chrome.exe", work_dir=r"C:\Chrome",
                                  arguments=""))
        _the_hosted_app_answers(tmp_path)
        runner.state = replace(runner.state, main_mode=MainMode.KINO,
                               rfb_mode=RfbMode.ORIGENERATOR, origenerator_ready=True)
        made_by_a_website = rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4"
        _the_main_funestra_publishes(tmp_path, locked=False, video=made_by_a_website)
        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = TestOpenRfbTab._rfb_patches(calls)

        with exists, activate, open_tab:
            runner._dispatch(parse_command("main_lock"))
            _the_main_funestra_publishes(tmp_path, locked=True, video=made_by_a_website)
            runner.tick()

        assert runner.state.rfb_mode is RfbMode.RFB
        assert [what for what, _ in calls] == ["activate", "open"]
        assert _what_the_gallery_heard(tmp_path) == []


class TestOrigeneratorWindowConverger:
    def test_no_converge_undoes_where_omnipause_put_the_hosted_window(self, tmp_path):
        runner = make_runner(tmp_path, origenerator_pid=700)
        runner.state = replace(runner.state, rfb_mode=RfbMode.ORIGENERATOR,
                               omni_paused=True)

        with patch.object(runner.windows, "converge_origenerator_window") as converge:
            runner._converge_origenerator_window()

        converge.assert_not_called()

    def test_outside_omnipause_the_windows_object_is_asked_for_the_browsers_place(self, tmp_path):
        runner = make_runner(tmp_path, origenerator_pid=700)
        runner.state = replace(runner.state, main_mode=MainMode.KINO,
                               rfb_mode=RfbMode.ORIGENERATOR)

        with patch.object(runner.windows, "converge_origenerator_window") as converge:
            runner._converge_origenerator_window()

        converge.assert_called_once_with(RfbMode.ORIGENERATOR)


def test_a_press_hint_leaves_no_socket_open_behind_it(tmp_path, monkeypatch):
    opened = []
    real_socket = socket.socket

    def recording_socket(*args, **kwargs):
        opened.append(real_socket(*args, **kwargs))
        return opened[-1]

    with _press_channel(tmp_path) as recv_sock:
        runner = make_runner(tmp_path, dashboard_enabled=True)
        monkeypatch.setattr(socket, "socket", recording_socket)
        runner._echo_the_press_best_effort("pause")
        monkeypatch.undo()

        assert _presses_until(recv_sock, "pause") == ["pause"]
    assert opened
    assert all(sender.fileno() == -1 for sender in opened)


class TestASessionThatHostsNoOrigenerator:
    def test_a_resumed_origenerator_mode_reads_as_kino(self, tmp_path):
        runner = make_runner(tmp_path)
        write_shared_state(tmp_path / "shared_state.ini", _in_origenerator_mode(
            BridgeState(rfb_mode=RfbMode.ORIGENERATOR)))

        runner.tick()

        assert _modes(runner.state) == [SatellitesMode.KINO, SatellitesMode.KINO]
        assert runner.state.rfb_mode is RfbMode.RFB

    def test_with_an_origenerator_that_is_up_the_mode_stands(self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        _the_hosted_app_answers(tmp_path)
        write_shared_state(tmp_path / "shared_state.ini", _in_origenerator_mode(
            BridgeState(rfb_mode=RfbMode.ORIGENERATOR), FunTimeFunestra.LANDSCAPE))

        runner.tick()

        assert _modes(runner.state) == [SatellitesMode.KINO, SatellitesMode.ORIGENERATOR]
        assert runner.state.rfb_mode is RfbMode.ORIGENERATOR


def _hosting(tmp_path, **overrides):
    return replace(make_config(tmp_path, **overrides), origenerator_enabled=True,
                   origenerator_cmd_file=tmp_path / "origenerator_cmd.txt",
                   origenerator_paused_file=tmp_path / "origenerator_paused.txt",
                   origenerator_status_file=tmp_path / "origenerator_status.txt")


def _follow_the_satellites(runner, *, now: float) -> None:
    runner.state = runner.changing_hands.follow(runner.state, runner.config, now=now)


def _in_origenerator_mode(state: BridgeState, *funestras: FunTimeFunestra) -> BridgeState:
    for funestra in funestras or FunTimeFunestra.SATELLITES:
        state = state.with_satellite(funestra, mode=SatellitesMode.ORIGENERATOR,
                                     handed_over=True)
    return state


def _modes(state: BridgeState) -> list[SatellitesMode]:
    return [state.satellite(funestra).mode for funestra in FunTimeFunestra.SATELLITES]


def _the_hosted_app_answers(tmp_path):
    (tmp_path / "origenerator_status.txt").write_text(
        "portrait_active=0\nlandscape_active=0\n", encoding="utf-8")


class TestOrigeneratorModeOpensWhenTheAppDoes:
    def test_the_mode_is_closed_until_the_app_publishes_a_status(self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path))

        runner.tick()
        assert runner.state.origenerator_ready is False

        _the_hosted_app_answers(tmp_path)
        runner.tick()
        assert runner.state.origenerator_ready is True

    def test_an_app_already_open_when_the_session_began_opens_the_mode_at_once(
            self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path),
                             origenerator_already_open=True)

        runner.tick()

        assert runner.state.origenerator_ready is True

    def test_a_status_file_unreadable_for_a_moment_closes_no_mode_in_use(self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        _the_hosted_app_answers(tmp_path)
        write_shared_state(tmp_path / "shared_state.ini", _in_origenerator_mode(
            BridgeState(rfb_mode=RfbMode.ORIGENERATOR), FunTimeFunestra.LANDSCAPE))
        runner.tick()

        (tmp_path / "origenerator_status.txt").unlink()
        runner.tick()

        assert runner.state.origenerator_ready is True
        assert _modes(runner.state) == [SatellitesMode.KINO, SatellitesMode.ORIGENERATOR]

    def test_a_session_hosting_none_is_never_waiting_on_one(self, tmp_path):
        runner = make_runner(tmp_path)

        runner.tick()

        assert runner.state.origenerator_ready is False

    def test_the_apps_boot_is_noticed_on_a_tick_that_read_no_state_file(self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        _the_hosted_app_answers(tmp_path)
        assert not (tmp_path / "shared_state.ini").exists()

        runner.tick()

        assert runner.state.origenerator_ready is True

    def test_the_answer_reaches_the_file_on_a_session_nobody_touches(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        write_shared_state(state_file, BridgeState())

        runner.tick()
        assert read_shared_state(state_file).origenerator_ready is False

        _the_hosted_app_answers(tmp_path)
        runner.tick()
        assert read_shared_state(state_file).origenerator_ready is True


def _publish_status(path: Path, *, speed: float) -> None:
    playback = SimpleNamespace(
        current_video="C:/v/n.mp4", showing="C:/v/n.mp4", position_ms=0, duration_ms=0,
        is_paused=False,
        is_locked=False, speed=speed, showing_image=False, playlist_length=1,
        has_funscript=False, funscript_resting=False, portrait=None, loads=1, idle=False)
    StatusWriter(path, lambda played: status_fields(played, None)).write(playback)


class TestTheRoomKeepsWhatItsFunestrasHold:
    def test_what_a_funestra_reports_is_written_into_the_apps_state(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        write_shared_state(state_file, BridgeState())
        runner = make_runner(tmp_path)
        _publish_status(runner.config.main_funestra_status_file, speed=1.5)

        runner.tick()

        assert read_shared_state(state_file).main_speed == 1.5

    def test_a_funestra_that_has_reported_nothing_leaves_what_the_app_remembers(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        remembered = replace(shifted_state(BridgeState()), rfb_mode=RfbMode.RFB)
        for funestra in FunTimeFunestra.SATELLITES:
            remembered = remembered.with_satellite(funestra, mode=SatellitesMode.KINO)
        write_shared_state(state_file, remembered)
        runner = make_runner(tmp_path)
        for status_file in (runner.config.main_funestra_status_file,
                            runner.config.portrait.status_file, runner.config.landscape.status_file,
                            runner.config.genau_status_file, runner.config.genau_drive_file):
            status_file.write_text("", encoding="utf-8")

        runner.tick()

        assert remembered_values(read_shared_state(state_file)) == remembered_values(
            replace(remembered, origenerator_ready=False))


class TestOpeningInOrigeneratorMode:
    @staticmethod
    def _the_app_answers(has_answered):
        return has_answered()

    def test_it_puts_origenerator_back_on_what_it_held_when_the_last_session_was_left(
            self, tmp_path):
        write_shared_state(tmp_path / "shared_state.ini", _in_origenerator_mode(
            BridgeState(rfb_mode=RfbMode.ORIGENERATOR), FunTimeFunestra.LANDSCAPE))
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        _the_hosted_app_answers(tmp_path)

        runner.activate_origenerator(
            wait_for_the_app=self._the_app_answers)

        assert _modes(runner.state) == [SatellitesMode.KINO, SatellitesMode.ORIGENERATOR]
        assert runner.state.rfb_mode is RfbMode.ORIGENERATOR
        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8").split() == [
            "landscape_open_slideshow"]

    def test_every_setting_the_app_was_left_in_comes_back_with_it(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        write_shared_state(state_file, _in_origenerator_mode(
            replace(shifted_state(BridgeState()), rfb_mode=RfbMode.ORIGENERATOR)))
        left_in = resume_shared_state(state_file, resumed=True)
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        _the_hosted_app_answers(tmp_path)

        runner.activate_origenerator(wait_for_the_app=self._the_app_answers)

        assert remembered_values(read_shared_state(state_file)) == remembered_values(left_in)

    def test_an_app_left_with_only_the_browsers_place_to_origenerator_waits_for_it_too(
            self, tmp_path):
        write_shared_state(tmp_path / "shared_state.ini",
                           BridgeState(rfb_mode=RfbMode.ORIGENERATOR))
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        _the_hosted_app_answers(tmp_path)

        assert runner.opening.activates
        runner.activate_origenerator(wait_for_the_app=self._the_app_answers)

        assert runner.state.rfb_mode is RfbMode.ORIGENERATOR
        assert _modes(runner.state) == [SatellitesMode.KINO, SatellitesMode.KINO]

    def test_a_slideshow_taken_over_opens_on_the_satellite_of_its_monitor_beside_the_app(
            self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path),
                             origenerator_already_open=True,
                             origenerator_slideshow_on=(FunTimeFunestra.PORTRAIT,))

        runner.activate_origenerator(
            wait_for_the_app=self._the_app_answers)

        assert _modes(runner.state) == [SatellitesMode.ORIGENERATOR, SatellitesMode.KINO]
        assert runner.state.rfb_mode is RfbMode.ORIGENERATOR
        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8").split() == [
            "portrait_open_slideshow"]

    def test_the_satellite_a_slideshow_taken_over_is_not_on_opens_as_it_was_left(
            self, tmp_path):
        write_shared_state(tmp_path / "shared_state.ini", _in_origenerator_mode(
            BridgeState(), FunTimeFunestra.PORTRAIT))
        runner = make_runner(tmp_path, config=_hosting(tmp_path),
                             origenerator_already_open=True,
                             origenerator_slideshow_on=(FunTimeFunestra.LANDSCAPE,))

        runner.activate_origenerator(
            wait_for_the_app=self._the_app_answers)

        assert _modes(runner.state) == [SatellitesMode.ORIGENERATOR, SatellitesMode.ORIGENERATOR]
        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8").split() == [
            "portrait_open_slideshow", "landscape_open_slideshow"]

    def test_a_slideshow_from_an_app_that_names_no_monitor_puts_origenerator_on_all_of_it(
            self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path),
                             origenerator_already_open=True,
                             origenerator_slideshow_on=FunTimeFunestra.SATELLITES)

        runner.activate_origenerator(
            wait_for_the_app=self._the_app_answers)

        assert _modes(runner.state) == [SatellitesMode.ORIGENERATOR, SatellitesMode.ORIGENERATOR]
        assert runner.state.rfb_mode is RfbMode.ORIGENERATOR

    def test_an_app_left_in_kino_mode_holds_its_loading_screen_up_for_nothing(self, tmp_path):
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        _the_hosted_app_answers(tmp_path)
        waits: list[bool] = []

        def wait_for_the_app(has_answered):
            waits.append(True)
            return has_answered()

        runner.activate_origenerator(wait_for_the_app=wait_for_the_app)

        assert not waits, "an ordinary room was held under its loading screen for the hosted app"
        assert _modes(runner.state) == [SatellitesMode.KINO, SatellitesMode.KINO]
        assert not (tmp_path / "origenerator_cmd.txt").exists()

    def test_an_app_turning_up_after_the_loading_screen_came_off_switches_nothing(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        write_shared_state(state_file, _in_origenerator_mode(
            BridgeState(rfb_mode=RfbMode.ORIGENERATOR)))
        runner = make_runner(tmp_path, config=_hosting(tmp_path))

        runner.activate_origenerator(wait_for_the_app=lambda _: False)
        assert _modes(read_shared_state(state_file)) == [SatellitesMode.KINO, SatellitesMode.KINO]
        assert read_shared_state(state_file).rfb_mode is RfbMode.RFB

        _the_hosted_app_answers(tmp_path)
        runner.tick()
        runner.tick()

        assert _modes(runner.state) == [SatellitesMode.KINO, SatellitesMode.KINO]
        assert runner.state.rfb_mode is RfbMode.RFB
        assert not (tmp_path / "origenerator_cmd.txt").exists()

    def test_it_comes_back_once_and_never_again(self, tmp_path):
        write_shared_state(tmp_path / "shared_state.ini", _in_origenerator_mode(BridgeState()))
        runner = make_runner(tmp_path, config=_hosting(tmp_path))
        _the_hosted_app_answers(tmp_path)
        runner.activate_origenerator(
            wait_for_the_app=self._the_app_answers)
        runner._dispatch(parse_command("portrait_kino_activate"))
        sent = (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8")

        runner.activate_origenerator(
            wait_for_the_app=self._the_app_answers)

        assert (tmp_path / "origenerator_cmd.txt").read_text(encoding="utf-8") == sent
        assert _modes(runner.state) == [SatellitesMode.KINO, SatellitesMode.ORIGENERATOR]


class TestTheConfigTakesWhatTheManifestSaysRatherThanDerivingIt:
    def _manifest(self, cfg_factory, tmp_path, **overrides):
        data = build_windows_bridge_manifest(load_config(cfg_factory()))
        data["commands"].update(overrides)
        return LaunchManifest.read(write_manifest_data(data, tmp_path / "manifest.ini"))

    def test_the_state_dir_is_the_one_the_manifest_names(self, cfg_factory, tmp_path):
        manifest = self._manifest(
            cfg_factory, tmp_path,
            state_dir=str(tmp_path / "elsewhere"),
            dashboard_state_file=str(tmp_path / "somewhere" / "dashboard_state.ini"))

        assert build_bridge_config_from_manifest(manifest).state_dir == tmp_path / "elsewhere"

    def test_the_message_channel_is_the_one_the_manifest_names(self, cfg_factory, tmp_path):
        manifest = self._manifest(
            cfg_factory, tmp_path,
            main_funestra_notice_file=str(tmp_path / "elsewhere" / "notices.txt"),
            main_funestra_status_file=str(tmp_path / "somewhere" / "main_player_status.txt"))

        assert build_bridge_config_from_manifest(manifest).main_funestra_notice_file == (
            tmp_path / "elsewhere" / "notices.txt")

    def test_each_satellites_channel_is_the_one_the_manifest_names(self, cfg_factory, tmp_path):
        data = build_windows_bridge_manifest(load_config(cfg_factory()))
        data["portrait"]["notice_file"] = str(tmp_path / "elsewhere" / "portrait_notice.txt")
        data["landscape"]["cmd_file"] = str(tmp_path / "elsewhere" / "landscape_cmd.txt")
        manifest = LaunchManifest.read(write_manifest_data(data, tmp_path / "manifest.ini"))

        config = build_bridge_config_from_manifest(manifest)

        assert config.satellite(FunTimeFunestra.PORTRAIT).notice_file == (
            tmp_path / "elsewhere" / "portrait_notice.txt")
        assert config.landscape.cmd_file == tmp_path / "elsewhere" / "landscape_cmd.txt"

    def test_whether_the_main_funestra_is_the_headsets_is_the_orchestrators_word(
            self, cfg_factory, tmp_path):
        data = build_windows_bridge_manifest(load_config(cfg_factory()))
        data["media"]["vr_library_dirs"] = str(tmp_path / "vr")
        manifest = LaunchManifest.read(write_manifest_data(data, tmp_path / "manifest.ini"))

        desktop = build_bridge_config_from_manifest(manifest)
        headset = build_bridge_config_from_manifest(manifest, vr_main_funestra=True)

        assert (desktop.vr_main_funestra, headset.vr_main_funestra) == (False, True)
        assert replace(headset, vr_main_funestra=False,
                       main_list_builds=desktop.main_list_builds) == desktop


class TestTheAppTakesAFunestra:
    def _switched(self, tmp_path):
        config = _hosting(tmp_path)
        runner = make_runner(tmp_path, config=config, hud_publisher=HudPublisher(
            {"portrait": tmp_path / "portrait_hud.json"}, tmp_path / "thumbs"))
        runner.state = replace(runner.state, origenerator_ready=True)
        channel = config.satellite(FunTimeFunestra.PORTRAIT)
        write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / "portrait.mp4")])
        publish_whole(channel.origenerator_hud_file, "")
        runner._dispatch(parse_command("portrait_origenerator_activate"))
        return runner, channel

    def test_the_satellite_stays_the_sessions_until_the_app_has_published_for_it(self, tmp_path):
        runner, _channel = self._switched(tmp_path)

        _follow_the_satellites(runner, now=0.0)

        assert runner.state.portrait.handed_over is False

    def test_the_apps_first_hud_hands_the_satellite_over_and_goes_up_at_once(self, tmp_path):
        runner, channel = self._switched(tmp_path)
        publish_whole(channel.origenerator_hud_file,
                      hud_text(HudModel(funestra="portrait", title="Stub slideshow")))

        _follow_the_satellites(runner, now=0.0)

        assert runner.state.portrait.handed_over is True
        published = (tmp_path / "portrait_hud.json").read_text(encoding="utf-8")
        assert parse_hud(published).title == "Stub slideshow"


class TestTheFunestrasComeHome:
    def _left_for_origenerator(self, tmp_path, *, locked=False):
        config = _hosting(tmp_path)
        runner = make_runner(tmp_path, config=config)
        runner.state = replace(runner.state, origenerator_ready=True,
                               portrait=SatelliteState(locked=locked))
        for funestra in FunTimeFunestra.SATELLITES:
            channel = config.satellite(funestra)
            write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / f"{funestra.label}.mp4")])
            keep_aside(channel)
            write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / "image.png")])
            publish_whole(channel.origenerator_hud_file, f'{{"Funestra": "{funestra.label}"}}')
        runner.homecoming.expect(FunTimeFunestra.SATELLITES, runner.config, now=100.0)
        return runner, config

    @staticmethod
    def _lets_go_of(config, funestra) -> None:
        publish_whole(config.satellite(funestra).origenerator_hud_file, "")

    @staticmethod
    def _queued(config, funestra) -> list[str]:
        cmd_file = config.satellite(funestra).cmd_file
        return cmd_file.read_text(encoding="utf-8").split() if cmd_file.exists() else []

    def test_a_satellite_let_go_of_comes_home_locked_as_the_session_holds_it(self, tmp_path):
        runner, config = self._left_for_origenerator(tmp_path, locked=True)
        self._lets_go_of(config, FunTimeFunestra.PORTRAIT)

        _follow_the_satellites(runner, now=100.5)

        assert self._queued(config, FunTimeFunestra.PORTRAIT) == ["RELOAD_PLAYLIST", "LOCK_ON"]
        assert self._queued(config, FunTimeFunestra.LANDSCAPE) == []

    def test_a_satellite_the_app_never_lets_go_of_comes_home_anyway(self, tmp_path):
        runner, config = self._left_for_origenerator(tmp_path)

        _follow_the_satellites(runner, now=100.0 + STALLED_APP_LET_GO_CEILING_S + 0.1)

        assert self._queued(config, FunTimeFunestra.PORTRAIT) == ["RELOAD_PLAYLIST", "LOCK_OFF"]
        assert self._queued(config, FunTimeFunestra.LANDSCAPE) == ["RELOAD_PLAYLIST", "LOCK_OFF"]

    def test_going_back_into_the_mode_first_leaves_them_with_the_app(self, tmp_path):
        runner, config = self._left_for_origenerator(tmp_path)
        runner.state = _in_origenerator_mode(runner.state, FunTimeFunestra.PORTRAIT)
        self._lets_go_of(config, FunTimeFunestra.PORTRAIT)

        _follow_the_satellites(runner, now=100.5)

        assert self._queued(config, FunTimeFunestra.PORTRAIT) == []
        runner.state = runner.state.with_satellite(FunTimeFunestra.PORTRAIT,
                                                   mode=SatellitesMode.KINO)
        _follow_the_satellites(runner, now=100.6)
        assert self._queued(config, FunTimeFunestra.PORTRAIT) == []

    def test_a_handback_waits_for_the_list_the_app_will_still_write(self, tmp_path):
        runner, config = self._left_for_origenerator(tmp_path)
        self._lets_go_of(config, FunTimeFunestra.PORTRAIT)
        runner.homecoming.expect(FunTimeFunestra.SATELLITES, runner.config, now=101.0)

        _follow_the_satellites(runner, now=101.5)

        assert self._queued(config, FunTimeFunestra.PORTRAIT) == []

    def test_the_switch_out_of_the_mode_is_what_sends_for_them(self, tmp_path):
        config = _hosting(tmp_path)
        runner = make_runner(tmp_path, config=config)
        runner.state = _in_origenerator_mode(replace(runner.state, origenerator_ready=True))

        with patch.object(runner.homecoming, "expect") as expect:
            runner._dispatch(parse_command("portrait_kino_activate"))

        assert expect.call_args.args == ((FunTimeFunestra.PORTRAIT,), runner.config)


SECONDARY_MONITOR = MonitorRect(2560, 0, 1440, 3440)


def _seating_runner(tmp_path, cfg_path, *, main_video_portrait: bool) -> DispatchLoopRunner:
    config = make_config(tmp_path)
    config.main_funestra_status_file.write_text(
        f"video=C:/fixtures/scene one.mp4\nportrait={int(main_video_portrait)}\n", encoding="utf-8")
    return make_runner(
        tmp_path, config=config,
        hwnds_by_window={"portrait": PORTRAIT_HWND, "main_funestra": MAIN_FUNESTRA_HWND},
        secondary_rects=partial(secondary_monitor_rects, SECONDARY_MONITOR,
                                load_config(cfg_path).layout))


def test_a_portrait_video_on_the_crowned_main_funestra_gives_it_most_of_the_secondary_monitor(
        tmp_path, cfg_path):
    runner = _seating_runner(tmp_path, cfg_path, main_video_portrait=True)
    usual = {PORTRAIT_HWND: (2560, 0, 1440, 2500), MAIN_FUNESTRA_HWND: (2560, 2500, 1440, 940)}

    with patch("fun_time.fun_time_windows.window_rect", side_effect=usual.get), \
         patch("fun_time.fun_time_windows.is_window_minimized", return_value=False), \
         patch("fun_time.fun_time_windows.place_window") as place:
        runner.tick()

    assert [call.args for call in place.call_args_list] == [
        (PORTRAIT_HWND, 2560, 0, 1440, 940), (MAIN_FUNESTRA_HWND, 2560, 940, 1440, 2500)]


def test_the_app_writes_down_which_funestra_has_most_of_the_secondary_monitor(tmp_path, cfg_path):
    runner = _seating_runner(tmp_path, cfg_path, main_video_portrait=True)

    with patch("fun_time.fun_time_windows.window_rect", return_value=None), \
         patch("fun_time.fun_time_windows.is_window_minimized", return_value=False), \
         patch("fun_time.fun_time_windows.place_window"):
        runner.tick()

    assert read_shared_state(runner.shared_state_file).majority is Crown.MAIN


def test_a_landscape_video_hands_most_of_the_secondary_monitor_back_to_the_portrait_funestra(
        tmp_path, cfg_path):
    runner = _seating_runner(tmp_path, cfg_path, main_video_portrait=False)
    traded = {PORTRAIT_HWND: (2560, 0, 1440, 940), MAIN_FUNESTRA_HWND: (2560, 940, 1440, 2500)}

    with patch("fun_time.fun_time_windows.window_rect", side_effect=traded.get), \
         patch("fun_time.fun_time_windows.is_window_minimized", return_value=False), \
         patch("fun_time.fun_time_windows.place_window") as place:
        runner.tick()

    assert [call.args for call in place.call_args_list] == [
        (MAIN_FUNESTRA_HWND, 2560, 2500, 1440, 940), (PORTRAIT_HWND, 2560, 0, 1440, 2500)]


def test_in_genau_mode_a_portrait_flick_gives_the_main_funestra_most_of_the_secondary_monitor(
        tmp_path, cfg_path):
    runner = _seating_runner(tmp_path, cfg_path, main_video_portrait=False)
    runner.config.genau_status_file.write_text(
        "flick=C:/fixtures/flick one.mp4\nportrait=1\n", encoding="utf-8")
    runner.state = replace(runner.state, main_mode=MainMode.GENAU)
    usual = {PORTRAIT_HWND: (2560, 0, 1440, 2500), MAIN_FUNESTRA_HWND: (2560, 2500, 1440, 940)}

    with patch("fun_time.fun_time_windows.window_rect", side_effect=usual.get), \
         patch("fun_time.fun_time_windows.is_window_minimized", return_value=False), \
         patch("fun_time.fun_time_windows.place_window") as place:
        runner.tick()

    assert [call.args for call in place.call_args_list] == [
        (PORTRAIT_HWND, 2560, 0, 1440, 940), (MAIN_FUNESTRA_HWND, 2560, 940, 1440, 2500)]


def test_an_app_that_arranges_no_secondary_monitor_moves_nothing(tmp_path):
    runner = make_runner(tmp_path, hwnds_by_window={"portrait": PORTRAIT_HWND})

    with patch("fun_time.fun_time_windows.place_window") as place:
        runner.tick()

    place.assert_not_called()

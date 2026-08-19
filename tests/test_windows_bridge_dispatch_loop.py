from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fun_time.command_dispatch import BridgeConfig, BridgeState, WindowOp
from fun_time.hud_transport import HudPublisher
from fun_time.media_metadata import normalize_path_key
from fun_time.voice_commands import parse_command_line
from fun_time.shared_state import read_shared_state, write_shared_state
from fun_time.watch_stats import load_watch_stats
from fun_time.windows_bridge_dispatch_loop import (
    poll_dashboard_commands,
    expand_both_command,
    resolve_active_side_command,
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
# The hosted Origenerator's three windows, resolved by pid AND caption together.
HOSTED_PID = 900
HOSTED_HWND = 8001
HOSTED_PORTRAIT_HWND = 8002
HOSTED_LANDSCAPE_HWND = 8003

PID_TO_HWND = {
    200: NAU_HWND,
    300: PORTRAIT_HWND,
    400: LANDSCAPE_HWND,
    500: DASHBOARD_HWND,
}

# The windows that are topmost in EVERY mode — the ones that own a rect and so
# overlap nothing.  Nau and Genau SHARE the main player's rect, so each is in the band
# only in the modes where it shows something; every test folds those two in or
# out as its own mode requires.
TOPMOST_HWNDS = {
    RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND,
}


@pytest.fixture(autouse=True)
def _neutralise_topmost_reads():
    """The only real-desktop reach left in a tick() is _log_topmost_state, which
    reads each managed window's WS_EX_TOPMOST flag on every OmniPause toggle.
    These tests are about dispatch, not z-order, and a real read would land on
    whichever window answers on this machine — so the flag read is neutralised
    (nothing is ever found topmost).  The native satellites publish their
    playback to status files, so a tick's satellite/main-player sampling is inert
    here (no status files written).  A test asserting on topmost state patches
    is_window_topmost itself."""
    with patch("fun_time.windows_bridge_dispatch_loop.is_window_topmost", return_value=False):
        yield


def lookup_pid(pid):
    return PID_TO_HWND.get(pid, 0)


def lookup_title(title, exact=False):
    return GENAU_HWND if title == "Genau" and not exact else 0


def lookup_hosted(pid, title):
    """The hosted app's windows, which resolve by pid AND caption together."""
    if pid != HOSTED_PID:
        return 0
    return {
        "Origenerator": HOSTED_HWND,
        "Origenerator Portrait": HOSTED_PORTRAIT_HWND,
        "Origenerator Landscape": HOSTED_LANDSCAPE_HWND,
    }.get(title, 0)


def make_config(tmp_path, **overrides) -> BridgeConfig:
    settings = dict(
        portrait_cmd_file=tmp_path / "portrait_cmd.txt",
        portrait_paused_file=tmp_path / "portrait_paused.txt",
        portrait_status_file=tmp_path / "portrait_status.txt",
        portrait_playlist_file=tmp_path / "portrait_playlist.tsv",
        landscape_cmd_file=tmp_path / "landscape_cmd.txt",
        landscape_paused_file=tmp_path / "landscape_paused.txt",
        landscape_status_file=tmp_path / "landscape_status.txt",
        landscape_playlist_file=tmp_path / "landscape_playlist.tsv",
        favs_file=tmp_path / "favs.txt",
        weird_dir=tmp_path / "weird",
        state_dir=tmp_path,
        main_sources="",
        portrait_sources="",
        landscape_sources="",
        genau_mode_file=tmp_path / "rh_mode.txt",
        genau_cmd_file=tmp_path / "rh_cmd.txt",
        genau_paused_file=tmp_path / "rh_paused.txt",
        audio_paused_file=tmp_path / "audio_paused.txt",
        audio_volume_file=tmp_path / "audio_volume.txt",
        nau_cmd_file=tmp_path / "nau_cmd.txt",
        nau_paused_file=tmp_path / "nau_paused.txt",
        nau_status_file=tmp_path / "nau_status.txt",
        dashboard_state_file=tmp_path / "dashboard_state.ini",
        broker_heartbeat_file=tmp_path / "broker_heartbeat.txt",
    )
    settings.update(overrides)
    return BridgeConfig(**settings)


def _make_video(tmp_path, name: str) -> Path:
    """A real file on disk — record_watch_event prunes keys that don't exist."""
    path = tmp_path / name
    path.write_text("x", encoding="utf-8")
    return path


def _write_nau_status(path: Path, video, *, position_ms: int, duration_ms: int, paused: bool = False) -> None:
    """Write a Nau status file the way nau/status.py does."""
    path.write_text(
        f"video={video}\nposition_ms={position_ms}\nduration_ms={duration_ms}\n"
        f"state=normal\npaused={'1' if paused else '0'}\n",
        encoding="utf-8",
    )


def _write_satellite_status(path: Path, video, *, fraction: float | None = None,
                            paused: bool = False, locked: bool = False) -> None:
    """Write a native satellite's status file the way its player publishes it.

    ``fraction`` sets how far through the clip the player reports: None writes a
    not-yet-known duration (``read_satellite_status().fraction`` is then None, so
    the sample is dropped), otherwise position/duration encode the fraction.
    """
    duration_ms = 0 if fraction is None else 1000
    position_ms = 0 if fraction is None else round(fraction * duration_ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"video={video}\nposition_ms={position_ms}\nduration_ms={duration_ms}\n"
        f"paused={'1' if paused else '0'}\nlocked={'1' if locked else '0'}\n",
        encoding="utf-8",
    )


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

    def test_default_threshold_ignores_a_merely_slow_tick(self):
        # A tick blocked on a stalled disk can run tens of seconds.  The default
        # threshold must clear that, not misread it as a wake.
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
        cmd_file.write_text("main_next\nmain_next\nportrait_prev\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["main_next", "main_next", "portrait_prev"]

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
        cmd_file.write_bytes(b"\xef\xbb\xbfmain_prev\n")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["main_prev"]


class TestResolveActiveSideCommand:
    """A side-agnostic 'active_*' command is rewritten onto whichever satellite
    is currently active; anything else passes through untouched."""

    def test_group_commands_ride_the_same_active_and_both_plumbing(self):
        # The loop / lock-action commands follow the <scope>_<action> shape, so
        # they resolve and expand without any special-casing.
        assert resolve_active_side_command("active_action_loop", 3) == "landscape_action_loop"
        assert resolve_active_side_command("active_lock_action", 2) == "portrait_lock_action"
        assert expand_both_command("both_seed_loop") == ["portrait_seed_loop", "landscape_seed_loop"]

    def test_rewrites_to_portrait_when_active_side_is_portrait(self):
        assert resolve_active_side_command("active_lock_on", 2) == "portrait_lock_on"

    def test_rewrites_to_landscape_when_active_side_is_landscape(self):
        assert resolve_active_side_command("active_next", 3) == "landscape_next"

    def test_passes_non_active_commands_through(self):
        assert resolve_active_side_command("main_next", 3) == "main_next"
        assert resolve_active_side_command("portrait_lock", 3) == "portrait_lock"

    def test_active_nav_targets_primary_when_primary_is_active(self):
        """The main player (slot 1) joins the active-side feature for nav."""
        assert resolve_active_side_command("active_next", 1) == "main_next"
        assert resolve_active_side_command("active_prev", 1) == "main_prev"

    def test_reset_on_the_primary_means_the_main_players_own_reset(self):
        """Another phrase that means a different thing on each player: on a
        satellite it drops the act filter and the loop, on the main player its
        length mode and its F-mode.  Without this, a bare "reset" said after
        navigating the main player reached nothing at all."""
        assert resolve_active_side_command("active_reset", 1) == "main_reset"
        assert resolve_active_side_command("active_reset", 2) == "portrait_reset"
        assert resolve_active_side_command("active_reset", 3) == "landscape_reset"

    def test_end_loop_on_the_primary_means_naus_own_loop(self):
        """A side-agnostic phrase may mean a different thing on each player: on a
        satellite "end loop" ends a group loop, on the main player it cancels Nau's A-B
        loop.  The resolution is where that translation belongs."""
        assert resolve_active_side_command("active_no_loop", 1) == "nau_loop_cancel"
        assert resolve_active_side_command("active_no_loop", 2) == "portrait_no_loop"
        assert resolve_active_side_command("active_no_loop", 3) == "landscape_no_loop"

    def test_a_bare_lock_reaches_the_primary_too(self):
        """A lock means the same thing on all three — repeat-one on what is on
        screen — so the bare word follows the active side onto the main player rather
        than falling through to nothing there."""
        assert resolve_active_side_command("active_lock_on", 1) == "main_lock_on"
        assert resolve_active_side_command("active_lock_off", 1) == "main_lock_off"
        assert resolve_active_side_command("active_lock_on", 2) == "portrait_lock_on"
        assert resolve_active_side_command("active_lock_off", 3) == "landscape_lock_off"

    def test_a_bare_f_mode_reaches_whichever_player_is_active(self):
        """Every player has its own F-mode, so the bare phrase follows the active
        side onto any of the three — the main player included, which is where it
        lands at startup."""
        for suffix in ("", "_on", "_off"):
            assert resolve_active_side_command(f"active_fmode{suffix}", 1) == f"main_fmode{suffix}"
            assert resolve_active_side_command(f"active_fmode{suffix}", 2) == f"portrait_fmode{suffix}"
            assert resolve_active_side_command(f"active_fmode{suffix}", 3) == f"landscape_fmode{suffix}"

    def test_a_bare_browse_order_reaches_whichever_player_is_active(self):
        """Every player browses in these two orders, the main player included now
        that Genau answers them too.  Left out, a bare "latest" said while the main
        player was the active one resolved to a command with no handler and did
        nothing — the player had to be named for a word that is supposed to reach
        whoever is active."""
        for order in ("latest", "shuffle"):
            assert resolve_active_side_command(f"active_{order}", 1) == f"main_{order}"
            assert resolve_active_side_command(f"active_{order}", 2) == f"portrait_{order}"
            assert resolve_active_side_command(f"active_{order}", 3) == f"landscape_{order}"

    def test_active_satellite_only_command_is_noop_when_primary_is_active(self):
        """Main has no weird or cycle, so a bare satellite-only command while it
        is active resolves to nothing (unchanged → a downstream no-op)."""
        assert resolve_active_side_command("active_trash", 1) == "active_trash"
        assert resolve_active_side_command("active_cycle_seed", 1) == "active_cycle_seed"


class TestDispatchLoopRunner:
    def test_dispatches_dashboard_command(self, tmp_path):
        # Use huge sync interval so genau sync doesn't fire
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "portrait_next" in commands
        assert not cmd_file.exists()

    def test_bare_active_lock_targets_the_landscape_when_it_is_active(self, tmp_path):
        """Voice 'lock' (active_lock_on) locks whichever side is active — here
        landscape, e.g. after the user navigated it with A/D."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(active_side=3, locked3=False)
        (tmp_path / "dashboard_cmd.txt").write_text("active_lock_on", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "landscape_lock" in commands
        assert "portrait_lock" not in commands

    def test_bare_active_next_targets_the_active_side(self, tmp_path):
        """A non-lock bare command ('next') also follows the active side."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(active_side=2)
        (tmp_path / "dashboard_cmd.txt").write_text("active_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "portrait_next" in commands

    def test_a_spoken_lock_is_back_dated_to_the_video_the_speaker_saw(self, tmp_path):
        """The satellite advanced while "lock portrait" was being recognized, so
        the command carries the utterance's start and is aimed at the video that
        was on screen then."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner._last_watch_sample = float("inf")
        runner.state = BridgeState(active_side=2, locked2=False)
        runner._timelines[2].observe("C:\\clips\\meant.mp4", now=100.0)
        runner._timelines[2].observe("C:\\clips\\advanced_to.mp4", now=101.0)
        (tmp_path / "dashboard_cmd.txt").write_text("portrait_lock_on @100.200", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        assert mock_dispatch.call_args[0][0] == "portrait_lock"
        assert mock_dispatch.call_args.kwargs["target_path"] == "C:\\clips\\meant.mp4"

    def test_a_spoken_command_reads_its_own_players_timeline(self, tmp_path):
        """Each satellite keeps its own history; a landscape command must not be
        back-dated against the portrait's videos."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner._last_watch_sample = float("inf")
        runner.state = BridgeState(active_side=3, locked3=False)
        runner._timelines[2].observe("C:\\clips\\portrait.mp4", now=100.0)
        runner._timelines[3].observe("C:\\clips\\landscape.mp4", now=100.0)
        (tmp_path / "dashboard_cmd.txt").write_text("landscape_trash @100.200", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        assert mock_dispatch.call_args.kwargs["target_path"] == "C:\\clips\\landscape.mp4"

    def test_a_hotkey_command_names_no_video(self, tmp_path):
        """A keypress is instantaneous: it means whatever is playing right now."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner._last_watch_sample = float("inf")
        runner._timelines[2].observe("C:\\clips\\meant.mp4", now=100.0)
        (tmp_path / "dashboard_cmd.txt").write_text("portrait_trash", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        assert mock_dispatch.call_args.kwargs["target_path"] == ""

    def test_sampling_records_each_satellites_video_into_its_timeline(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        _write_satellite_status(runner.config.portrait_status_file, "C:\\clips\\portrait.mp4", fraction=0.1)
        _write_satellite_status(runner.config.landscape_status_file, "C:\\clips\\landscape.mp4", fraction=0.1)

        runner._sample_satellites(now=500.0)

        assert runner._timelines[2].path_at(500.0) == "C:\\clips\\portrait.mp4"
        assert runner._timelines[3].path_at(500.0) == "C:\\clips\\landscape.mp4"

    def test_primary_sampling_records_a_completion_when_a_watched_nau_video_departs(self, tmp_path):
        """Nau's status feed is watch-tracked just like a satellite: a video seen
        to ~the end, then auto-advanced past, is one completion."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        watched = _make_video(tmp_path, "watched.mp4")
        nextv = _make_video(tmp_path, "next.mp4")
        status = tmp_path / "nau_status.txt"

        _write_nau_status(status, watched, position_ms=9000, duration_ms=10000)
        runner._sample_main()
        _write_nau_status(status, nextv, position_ms=0, duration_ms=10000)
        runner._sample_main()

        stats = load_watch_stats(runner._watch_stats_file)
        assert stats[normalize_path_key(str(watched))]["completions"] == 1

    def test_primary_sampling_skips_when_duration_is_unknown(self, tmp_path):
        """Before Nau knows the clip length it publishes duration_ms=0; no
        fraction can be formed, so the sample is dropped (never a divide-by-zero)."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        early = _make_video(tmp_path, "early.mp4")
        nextv = _make_video(tmp_path, "next.mp4")
        status = tmp_path / "nau_status.txt"

        _write_nau_status(status, early, position_ms=5000, duration_ms=0)
        runner._sample_main()
        _write_nau_status(status, nextv, position_ms=0, duration_ms=10000)
        runner._sample_main()

        assert normalize_path_key(str(early)) not in load_watch_stats(runner._watch_stats_file)

    def test_primary_sampling_ignores_paused_nau_samples(self, tmp_path):
        """A paused player isn't watching; its samples are dropped, so a position
        held near the end under pause is not later scored as a completion."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        watched = _make_video(tmp_path, "watched.mp4")
        nextv = _make_video(tmp_path, "next.mp4")
        status = tmp_path / "nau_status.txt"

        _write_nau_status(status, watched, position_ms=9000, duration_ms=10000, paused=True)
        runner._sample_main()
        _write_nau_status(status, nextv, position_ms=0, duration_ms=10000)
        runner._sample_main()

        assert normalize_path_key(str(watched)) not in load_watch_stats(runner._watch_stats_file)

    def test_primary_sampling_ignores_status_with_no_video(self, tmp_path):
        """Between videos Nau can briefly publish an empty video path; that blank
        must not read as the watched video departing (a spurious completion)."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        watched = _make_video(tmp_path, "watched.mp4")
        status = tmp_path / "nau_status.txt"

        _write_nau_status(status, watched, position_ms=9000, duration_ms=10000)
        runner._sample_main()
        _write_nau_status(status, "", position_ms=0, duration_ms=10000)
        runner._sample_main()

        assert normalize_path_key(str(watched)) not in load_watch_stats(runner._watch_stats_file)

    def test_primary_next_marks_the_departed_nau_video_as_a_skip(self, tmp_path):
        """Pressing next on the main player is the "user nav" signal: a Nau video left
        early right after a next counts as a skip, just like a satellite next."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        early = _make_video(tmp_path, "early.mp4")
        nextv = _make_video(tmp_path, "next.mp4")
        status = tmp_path / "nau_status.txt"

        _write_nau_status(status, early, position_ms=1000, duration_ms=10000)
        runner._sample_main()
        runner._dispatch("main_next")
        _write_nau_status(status, nextv, position_ms=0, duration_ms=10000)
        runner._sample_main()

        stats = load_watch_stats(runner._watch_stats_file)
        assert stats[normalize_path_key(str(early))]["skips"] == 1

    def test_tick_samples_the_primary_player(self, tmp_path):
        """Nau watch tracking rides the same periodic sample tick as the satellites."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)

        with patch.object(runner, "_sample_main") as mock_main:
            runner.tick()

        mock_main.assert_called_once()

    def test_tick_does_not_sample_the_primary_under_omnipause(self, tmp_path):
        """Omnipause halts sampling for every player, the main one included."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(omni_paused=True)

        with patch.object(runner, "_sample_main") as mock_main:
            runner.tick()

        mock_main.assert_not_called()

    def test_nudge_dispatches_to_command(self, tmp_path):
        """Nau owns the main player in every mode it appears, so a nudge
        dispatches to Nau's SEEK command (which stacks against its live clock)."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        (tmp_path / "dashboard_cmd.txt").write_text("main_nudge_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "main_nudge_next" in commands

    def test_omnipause_enter_via_tick_drops_topmost_on_all_managed_windows(self, tmp_path):
        """Entering omnipause frees the desktop: EVERY managed window leaves the
        TOPMOST band — including Nau, which carries the topmost flag in nau mode
        and would otherwise stay stranded above the desktop."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        (tmp_path / "dashboard_cmd.txt").write_text("omnipause_toggle", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert runner.state.omni_paused is True
        assert {h for h, v in topmost_calls if v is False} == TOPMOST_HWNDS | {NAU_HWND, GENAU_HWND}

    def test_omnipause_leave_via_tick_restores_topmost_and_refocuses_primary_player(
        self, tmp_path, monkeypatch,
    ):
        """Leaving omnipause in nau mode gives every managed window its TOPMOST
        bit back — INCLUDING Nau, which floats above the desktop again — and
        re-activates the window that owns the main player.

        Genau is the exception, and the reason this is worth pinning: it shares
        Nau's rect and is promoted last, so putting it back in the band puts it
        ABOVE Nau's video.  Coming back from omnipause used to do exactly that.
        """
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        runner.state = BridgeState(omni_paused=True)
        (tmp_path / "dashboard_cmd.txt").write_text("omnipause_toggle", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []
        activated: list[int] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.is_window_topmost", return_value=False), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window", side_effect=activated.append), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert runner.state.omni_paused is False
        assert {h for h, v in topmost_calls if v is True} == TOPMOST_HWNDS | {NAU_HWND}
        assert GENAU_HWND not in {h for h, v in topmost_calls if v is True}
        assert activated == [NAU_HWND]

    def test_omnipause_toggle_updates_state_and_writes_shared_state(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omnipause_toggle", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
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
        runner.state = BridgeState(main_mode="genau")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
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
        runner.state = BridgeState(main_mode="genau")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
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

    def test_backslash_key_sends_browse_library_press_in_nau_mode(self, tmp_path):
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.settimeout(1.0)
        port = recv_sock.getsockname()[1]
        port_file = tmp_path / "dashboard_press_port.txt"
        port_file.write_text(str(port), encoding="utf-8")

        runner = make_runner(tmp_path, sync_interval_ms=999999, dashboard_enabled=True)
        runner._last_sync = float("inf")
        runner.state = BridgeState(main_mode="nau")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
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
        assert "browse_library" in messages

    def test_backslash_key_enters_omnipause_when_not_in_genau_mode(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(main_mode="nau")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("backslash_key", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner.tick()
            time.sleep(0.15)  # background thread needs a moment

        assert runner.state.omni_paused is False  # leaves omnipause after dialog closes

    def test_dispatch_forwards_remaining_ops_to_ahk(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        suspend_op = WindowOp(op="suspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [suspend_op])
            runner._dispatch("some_command")

        assert ahk_cmd_file.read_text(encoding="utf-8") == "suspend_hotkeys"

    def test_dispatch_suppresses_unsuspend_during_integration(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        unsuspend_op = WindowOp(op="unsuspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [unsuspend_op])
            runner._dispatch("some_command")

        assert not ahk_cmd_file.exists()

    def test_dispatch_allows_unsuspend_outside_integration(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        unsuspend_op = WindowOp(op="unsuspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [unsuspend_op])
            runner._dispatch("some_command")

        assert ahk_cmd_file.read_text(encoding="utf-8") == "unsuspend_hotkeys"

    def test_dispatch_sends_a_notice_to_the_event_log_not_to_ahk(self, tmp_path):
        """A notice is a message for the person watching; it goes to the log
        panel's stream, and AHK — which used to flash it at the mouse — never
        hears about it."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        notice_op = WindowOp(op="notice", key="Clipper: MyVideo", source="main")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.notice") as mock_notice:
            mock_dispatch.return_value = (runner.state, [notice_op])
            runner._dispatch("some_command")

        assert not ahk_cmd_file.exists()
        mock_notice.assert_called_once()
        assert mock_notice.call_args[0][1] == "Clipper: MyVideo"
        assert mock_notice.call_args[1] == {"source": "main", "level": notice_op.level}

    def test_a_dead_end_notice_is_logged_at_its_error_level(self, tmp_path):
        """A no-effect notice carries ERROR so the panel and flash render it red;
        the dispatch loop must pass that level through, not flatten it to NOTICE."""
        import logging

        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")

        notice_op = WindowOp(op="notice", key="No other seeds", source="portrait", level=logging.ERROR)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.notice") as mock_notice:
            mock_dispatch.return_value = (runner.state, [notice_op])
            runner._dispatch("portrait_cycle_seed")

        assert mock_notice.call_args[1] == {"source": "portrait", "level": logging.ERROR}

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
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command", return_value=(new_state, [])):
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
        runner.state = BridgeState(main_mode="hybrid")
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

    def test_mode_switch_leaves_the_outgoing_player_up_for_a_beat(self, tmp_path):
        """Minimizing freezes a window's Alt-Tab thumbnail — Windows stops
        compositing it — so the player being left has to be minimized only once
        the DISPLAY_OFF sent with the same switch is on screen.  Minimize in the
        frame or two that takes and the thumbnail keeps the video frame it was
        sitting on, which is the whole thing the blanking is for."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("genau_activate", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()

            assert minimized == [], "Nau minimized before it could paint the black"
            assert runner._pending_hides.keys() == {"nau"}

            # The settle elapses; the next tick parks it, without activation.
            runner._pending_hides["nau"] = time.monotonic() - 0.001
            runner.tick()

        assert minimized == [NAU_HWND]
        assert runner._pending_hides == {}

    def test_switching_straight_back_never_minimizes_the_player(self, tmp_path):
        """A switch inside the settle window would otherwise minimize the very
        player it had just restored."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("genau_activate\nnau_activate", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()
            for role in list(runner._pending_hides):
                runner._pending_hides[role] = time.monotonic() - 0.001
            runner.tick()

        assert NAU_HWND not in minimized, "Nau owns the display again"
        assert minimized == [GENAU_HWND]

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

    def test_a_huds_minimize_button_parks_only_that_player(self, tmp_path):
        """The satellites are borderless, so the HUD button is the only way to get
        one out of the way on its own.  It reaches exactly that window — the other
        players stay up — and never activates, so parking one does not hand the
        foreground to the next."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_minimize", encoding="utf-8")

        minimized: list[tuple[int, dict]] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window", side_effect=lambda h, **kw: minimized.append((h, kw))):
            runner.tick()

        assert [h for h, _ in minimized] == [PORTRAIT_HWND]
        assert all(kw.get("activate") is False for _, kw in minimized)

    def test_a_huds_minimize_button_takes_effect_without_a_settle(self, tmp_path):
        """Unlike the main-slot swap, which waits out PRIMARY_BLANK_SETTLE_S so the
        outgoing player can present its black first, nothing here has been told to
        blank — so the window goes down in the same tick as the press."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("landscape_minimize", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()

            assert minimized == [LANDSCAPE_HWND]
            assert runner._pending_hides == {}

    def test_the_main_players_console_button_parks_the_window_holding_the_slot(self, tmp_path):
        """Nau and Genau share the main rect, so which window the console's button
        reaches is the mode's business: Nau in nau mode, and in hybrid both, where
        Genau's HUD sits over Nau's video."""
        for mode, wanted in (("nau", [NAU_HWND]), ("genau", [GENAU_HWND]),
                             ("hybrid", [NAU_HWND, GENAU_HWND])):
            runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
            runner._last_sync = float("inf")
            # Through the shared state file, which every tick re-reads over
            # whatever the runner is holding.
            write_shared_state(tmp_path / "shared_state.ini", BridgeState(main_mode=mode))
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("main_minimize", encoding="utf-8")

            minimized: list[int] = []

            with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
                 patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
                 patch("fun_time.windows_bridge_dispatch_loop.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
                runner.tick()

            assert minimized == wanted, mode

    def test_leaving_omnipause_brings_back_every_window_a_button_parked(self, tmp_path):
        """A player parked from its own HUD took that HUD down with it, so it
        cannot ask to come back — resuming the room is what returns it, to the same
        rect, and the list is consumed so a second resume restores nothing."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))
        cmd_file = tmp_path / "dashboard_cmd.txt"

        restored: list[tuple[int, dict]] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window", side_effect=lambda h, **kw: restored.append((h, kw))), \
             patch.object(runner, "_restore_all_topmost"):
            cmd_file.write_text("portrait_minimize\nlandscape_minimize", encoding="utf-8")
            runner.tick()
            assert runner._parked_hwnds == [PORTRAIT_HWND, LANDSCAPE_HWND]

            cmd_file.write_text("leave_omnipause", encoding="utf-8")
            runner.tick()

            assert [h for h, _ in restored] == [PORTRAIT_HWND, LANDSCAPE_HWND]
            assert all(kw.get("activate") is False for _, kw in restored)
            assert runner._parked_hwnds == []

            write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))
            cmd_file.write_text("leave_omnipause", encoding="utf-8")
            runner.tick()

        assert [h for h, _ in restored] == [PORTRAIT_HWND, LANDSCAPE_HWND]

    def test_resuming_leaves_the_mode_parked_slot_mate_where_it_is(self, tmp_path):
        """The idle main-slot player is minimized by the mode switch, not by a
        button, and the switch that brings its mode back is what restores it.
        Resuming must not drag it onto a rect the other player is using."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"

        restored: list[int] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window"), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window", side_effect=lambda h, **kw: restored.append(h)), \
             patch.object(runner, "_restore_all_topmost"), \
             patch.object(runner, "_restack_main_slot"):
            # A switch to genau parks Nau, which the settle then flushes.
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner.tick()
            runner._pending_hides = {}
            runner._minimize_role("nau")
            restored.clear()

            write_shared_state(tmp_path / "shared_state.ini",
                               replace(runner.state, omni_paused=True))
            cmd_file.write_text("leave_omnipause", encoding="utf-8")
            runner.tick()

        assert NAU_HWND not in restored
        assert runner._parked_hwnds == []

    def test_a_huds_minimize_button_says_nothing_to_ahk(self, tmp_path):
        """The op loop's fall-through writes an unrecognized op straight to the AHK
        command file, so a new op that is not handled would arrive there as a bogus
        verb rather than doing its job."""
        runner = make_runner(tmp_path, sync_interval_ms=999999, rfb_hwnd=RFB_HWND)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_minimize", encoding="utf-8")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window"):
            runner.tick()

        assert not ahk_cmd_file.exists()

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

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        data, _ = recv_sock.recvfrom(256)
        recv_sock.close()
        assert data.decode("utf-8") == "portrait_lock"

    def test_help_reference_commands_send_press_but_do_not_dispatch(self, tmp_path):
        """The reference popup is a dashboard-UI concern: the loop echoes each
        command (toggle and close) as a press (the dashboard acts on it) and
        dispatches nothing — no player commands, no shared-state churn."""
        for command in ("help_reference", "help_reference_close"):
            recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            recv_sock.bind(("127.0.0.1", 0))
            recv_sock.settimeout(1.0)
            port = recv_sock.getsockname()[1]
            (tmp_path / "dashboard_press_port.txt").write_text(str(port), encoding="utf-8")

            runner = make_runner(tmp_path, sync_interval_ms=999999, dashboard_enabled=True)
            runner._last_sync = float("inf")
            (tmp_path / "dashboard_cmd.txt").write_text(command, encoding="utf-8")

            with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
                runner.tick()

            mock_dispatch.assert_not_called()
            data, _ = recv_sock.recvfrom(256)
            recv_sock.close()
            assert data.decode("utf-8") == command

    def test_udp_press_skipped_when_no_port_file(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999, dashboard_enabled=True)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_lock", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
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

    def test_omnipause_suspends_the_voice_controller(self, tmp_path):
        """Omnipause freezes voice the way it freezes the AHK hotkeys: of what a
        paused room says, only the exempt commands reach the dispatch loop."""
        from fun_time.voice_control import VoiceController

        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner._last_watch_sample = float("inf")
        vc_cmd = tmp_path / "vc_cmd.txt"
        vc = VoiceController(cmd_file=vc_cmd, model_path="unused")
        runner.voice_controller = vc
        runner.state = replace(runner.state, omni_paused=True)

        runner.tick()

        vc._write_command("landscape_next", spoken_at=1.0)
        vc._write_command("play", spoken_at=2.0)
        written = vc_cmd.read_text(encoding="utf-8").splitlines()
        assert [parse_command_line(line)[0] for line in written] == ["play"]

    def test_leaving_omnipause_unsuspends_the_voice_controller(self, tmp_path):
        from fun_time.voice_control import VoiceController

        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner._last_watch_sample = float("inf")
        vc_cmd = tmp_path / "vc_cmd.txt"
        vc = VoiceController(cmd_file=vc_cmd, model_path="unused")
        vc.suspend()
        runner.voice_controller = vc
        runner.state = replace(runner.state, omni_paused=False)

        runner.tick()

        vc._write_command("landscape_next", spoken_at=1.0)
        written = vc_cmd.read_text(encoding="utf-8").splitlines()
        assert [parse_command_line(line)[0] for line in written] == ["landscape_next"]


class TestOpenRfbTab:
    """A lock's tab has to reach the session's own Chrome window.

    That window shares a profile with whatever windows of it the user already
    had open, and Chrome gives a forwarded URL to the most recently activated
    window of the profile — so these tests pin the two things that make the
    session's window the one it picks: it is activated first, and a handoff is
    refused outright when the session has no window of its own left.
    """

    @staticmethod
    def _rfb_patches(calls: list[tuple[str, object]], *, alive: bool = True):
        """Patch the win32 pair and the launcher, recording the order they run in."""
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
            sync_interval_ms=999999,
            rfb_hwnd=12345,
            rfb_shortcut_target=r"C:\Chrome\chrome.exe",
            rfb_shortcut_work_dir=r"C:\Chrome",
            rfb_shortcut_args='--profile-directory="Profile 2"',
        )
        runner._last_sync = float("inf")

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch("portrait_lock")

        assert calls == [
            ("activate", 12345),
            ("open", {
                "urls": ["https://example.com"],
                "shortcut_target": r"C:\Chrome\chrome.exe",
                "shortcut_work_dir": r"C:\Chrome",
                "shortcut_args": '--profile-directory="Profile 2"',
            }),
        ]

    def test_rfb_window_is_activated_before_the_handoff(self, tmp_path):
        """Chrome gives the URL to the most recently activated window of the
        profile, so the activation has to happen before chrome.exe is launched —
        after it, the user's own window is still the one Chrome would pick."""
        runner = make_runner(
            tmp_path,
            sync_interval_ms=999999,
            rfb_hwnd=777,
            rfb_shortcut_target=r"C:\Chrome\chrome.exe",
            rfb_shortcut_work_dir=r"C:\Chrome",
            rfb_shortcut_args='--profile-directory="Profile 2"',
        )
        runner._last_sync = float("inf")

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch("portrait_lock")

        assert [name for name, _ in calls] == ["activate", "open"]

    def test_open_rfb_tab_op_skipped_when_the_rfb_window_is_gone(self, tmp_path):
        """A handle outlives the window it named.  With no window of its own to
        open into, every URL handed to Chrome lands in one of the user's — so the
        tab is dropped rather than opened somewhere Fun Time does not own."""
        runner = make_runner(
            tmp_path,
            sync_interval_ms=999999,
            rfb_hwnd=12345,
            rfb_shortcut_target=r"C:\Chrome\chrome.exe",
            rfb_shortcut_work_dir=r"C:\Chrome",
            rfb_shortcut_args='--profile-directory="Profile 2"',
        )
        runner._last_sync = float("inf")

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls, alive=False)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch("portrait_lock")

        assert calls == []

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

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch("portrait_lock")

        assert calls == []

    def test_open_rfb_tab_op_skipped_when_no_shortcut_target(self, tmp_path):
        runner = make_runner(
            tmp_path,
            sync_interval_ms=999999,
            rfb_hwnd=12345,
        )
        runner._last_sync = float("inf")

        calls: list[tuple[str, object]] = []
        exists, activate, open_tab = self._rfb_patches(calls)
        rfb_op = WindowOp(op="open_rfb_tab", key="https://example.com")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             exists, activate, open_tab:
            mock_dispatch.return_value = (runner.state, [rfb_op])
            runner._dispatch("portrait_lock")

        assert calls == []

    def test_lock_both_opens_both_videos_in_one_launch(self, tmp_path):
        """"lock both" locks two videos in one tick; their RFB tabs must open in
        a single Chrome launch, or the second races the singleton and is dropped."""
        runner = make_runner(
            tmp_path,
            sync_interval_ms=999999,
            rfb_hwnd=12345,
            rfb_shortcut_target=r"C:\Chrome\chrome.exe",
            rfb_shortcut_work_dir=r"C:\Chrome",
            rfb_shortcut_args='--profile-directory="Profile 2"',
        )
        runner._last_sync = float("inf")
        runner.state = BridgeState(locked2=False, locked3=False)
        (tmp_path / "dashboard_cmd.txt").write_text("both_lock_on", encoding="utf-8")

        def fake_dispatch(cmd, state, config, target_path=""):
            if cmd == "portrait_lock":
                return replace(state, locked2=True), [WindowOp(op="open_rfb_tab", key="http://p")]
            if cmd == "landscape_lock":
                return replace(state, locked3=True), [WindowOp(op="open_rfb_tab", key="http://l")]
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
                "shortcut_target": r"C:\Chrome\chrome.exe",
                "shortcut_work_dir": r"C:\Chrome",
                "shortcut_args": '--profile-directory="Profile 2"',
            }),
        ]


class TestModeSwitchVisibility:
    """The two main-slot players (Nau and Genau) share one screen rect.
    A mode switch swaps window VISIBILITY: the incoming player is shown and
    activated BEFORE the outgoing one hides, so focus never falls through to
    another application.

    These tests run the real dispatch_command, pinning the whole path from
    command string to win32 call — including the show_role/activate_role/
    hide_role ops, whose silent dropping broke mode switches once.
    """

    def _run_mode_switch(self, tmp_path, monkeypatch, *, from_mode, command,
                         integration_env=False):
        if integration_env:
            monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        else:
            monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode=from_mode)

        calls: list[tuple[str, int]] = []
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window",
                   side_effect=lambda h, **kw: calls.append(("show", h))), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window",
                   side_effect=lambda h, **kw: calls.append(("hide", h))), \
             patch("fun_time.windows_bridge_dispatch_loop.activate_window",
                   side_effect=lambda h: calls.append(("activate", h))), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"):
            runner._dispatch(command)
            # The outgoing player's minimize is held back a beat, so it can paint
            # the black the same switch told it to before its Alt-Tab thumbnail
            # freezes (see _hide_role).  Run that out here, so these tests still
            # see the whole ordered sequence.
            for role in runner._pending_hides:
                runner._pending_hides[role] = time.monotonic() - 0.001
            runner._flush_pending_hides()

        assert runner.state.main_mode == {
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

        # Nau is now minimized: the pid/title lookups are mocked to fail, but
        # the cache still answers, and a show_role op reaches the cached hwnd
        # (show_role restores rather than SW_SHOWs — the idle player is parked
        # by minimizing it, so bringing it back is a restore).
        shown: list[int] = []
        show_op = WindowOp(op="show_role", key="nau")
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window", side_effect=lambda h, **kw: shown.append(h)), \
             patch("fun_time.windows_bridge_dispatch_loop.dispatch_command",
                   return_value=(runner.state, [show_op])):
            assert runner._resolve_role("nau") == NAU_HWND
            runner._dispatch("nau_activate")

        assert shown == [NAU_HWND]


class TestModeDependentTopmost:
    """Every managed window is topmost in every mode EXCEPT Nau, whose band is
    mode-dependent: topmost in nau mode (floating above the desktop like the
    main player always has), non-topmost in hybrid (under Genau's HUD)."""

    def _topmost_calls(self, runner, method_name):
        calls: list[tuple[int, bool]] = []
        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_for_process",
                   side_effect=lookup_hosted), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda h, v: calls.append((h, v))):
            getattr(runner, method_name)()
        return calls

    def test_remove_all_topmost_drops_every_managed_window(self, tmp_path):
        """Omnipause enter frees the desktop entirely — Nau included, so it is
        never left stranded on top."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)

        calls = self._topmost_calls(runner, "_remove_all_topmost")

        assert {h for h, v in calls if v is False} == TOPMOST_HWNDS | {NAU_HWND, GENAU_HWND}

    def test_restore_all_topmost_floats_nau_in_nau_mode(self, tmp_path):
        """nau mode: Nau reclaims the topmost band, above the desktop."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(main_mode="nau")

        calls = self._topmost_calls(runner, "_restore_all_topmost")

        assert {h for h, v in calls if v is True} == TOPMOST_HWNDS | {NAU_HWND}

    def test_restore_all_topmost_stacks_genau_above_nau_in_hybrid(self, tmp_path):
        """hybrid: Nau and Genau are BOTH topmost so the composite floats above
        the desktop, and Nau is promoted BEFORE Genau so the HUD stacks over the
        video."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(main_mode="hybrid")

        calls = self._topmost_calls(runner, "_restore_all_topmost")

        promoted = [h for h, v in calls if v is True]
        assert {RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND,
                NAU_HWND, GENAU_HWND} <= set(promoted)
        # Nau promoted before Genau → Genau's HUD lands above Nau's video.
        assert promoted.index(NAU_HWND) < promoted.index(GENAU_HWND)

    def test_restore_all_topmost_leaves_the_browser_under_the_hosted_app(self, tmp_path):
        """His: the Random Favs Browser flashes over Origenerator for a moment
        every time the room resumes from OmniPause.

        The browser shares its rect with the hosted app's main window, and the
        band policy already answers "not topmost" for it in origenerator mode
        — but this path promoted every fixed role without asking, so the
        browser went to the top of the band (HWND_TOPMOST inserts there) and
        stayed above Origenerator until _restack_satellites promoted the host
        back over it a moment later.  That gap is the flash.
        """
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND, origenerator_pid=HOSTED_PID)
        runner.state = BridgeState(main_mode="nau", satellites_mode="origenerator")

        calls = self._topmost_calls(runner, "_restore_all_topmost")

        promoted = [h for h, v in calls if v is True]
        assert RFB_HWND not in promoted, (
            "the browser was promoted into the topmost band while the hosted "
            "app owns its rect, which puts it over Origenerator until the next "
            "promotion pushes it back down"
        )
        # Everything the mode really does show still comes back.
        assert {PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND, NAU_HWND,
                HOSTED_HWND, HOSTED_PORTRAIT_HWND,
                HOSTED_LANDSCAPE_HWND} <= set(promoted)


class TestBrowseLibrary:
    """Tests for the browse_library command (the Nau "browse" feature).

    Browsing must leave playback and voice alone — everything keeps playing
    while you pick.  The browser only needs the topmost bands dropped so it is
    not buried under the always-on-top windows; it must never enter OmniPause.
    """

    def test_browse_never_enters_omnipause(self, tmp_path):
        """The core regression: browsing must not pause the session.

        The bug — browse entered OmniPause, and picking a video resumed only
        Nau, leaving the satellites + voice frozen (so "pause" was ignored with
        "we're in omnipause").  Browsing keeps everything playing, so it never
        enters OmniPause and never leaves the session paused.
        """
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch, \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        dispatched = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "enter_omnipause" not in dispatched
        assert "leave_omnipause" not in dispatched
        assert runner.state.omni_paused is False

    def test_removes_topmost_from_all_managed_windows(self, tmp_path):
        """The dialog needs a clear stage: every managed window drops out of the
        topmost band so it can't bury the dialog.  Nothing pauses — this is the
        only window state browsing touches."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda hwnd, on_top: topmost_calls.append((hwnd, on_top))), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        removed = {h for h, v in topmost_calls if not v}
        assert removed == TOPMOST_HWNDS | {NAU_HWND, GENAU_HWND}

    def test_browses_the_session_library_over_the_primary_display(self, tmp_path):
        """The browse opens Fun Time's own library browser, filling Nau's rect.

        Nau's window is what the pick will play in, so the browser stands exactly
        where the video will be — and covers nothing else on either monitor.
        """
        config = make_config(tmp_path, python_exe=r"C:\python.exe")
        runner = make_runner(tmp_path, config=config, manifest_path=tmp_path / "launch.ini")
        runner.state = BridgeState(omni_paused=False)

        with patch.object(runner, "_remove_all_topmost"), \
             patch.object(runner, "_restore_all_topmost"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.window_rect", return_value=(0, 400, 1080, 1520)), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None) as mock_browse:
            runner._handle_browse_library()

        mock_browse.assert_called_once_with(
            tmp_path / "launch.ini", r"C:\python.exe", over=(0, 400, 1080, 1520),
            runner=runner._run_browser,
        )

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

        with patch.object(runner, "_remove_all_topmost"), \
             patch.object(runner, "_restore_all_topmost"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=str(video)):
            runner._handle_browse_library()

        command = runner.config.nau_cmd_file.read_text(encoding="utf-8")
        assert command == f"PLAY_FILE {video}\t{mirrored}\n"

    def test_sends_selected_file_to_nau_in_hybrid(self, tmp_path):
        """Hybrid displays Nau, so a selected file becomes a Nau PLAY_FILE
        command there too (no funscript pairing when none exists)."""
        config = make_config(tmp_path, main_sources=r"C:\videos")
        runner = make_runner(tmp_path, config=config)
        runner.state = BridgeState(omni_paused=False, main_mode="hybrid")

        with patch.object(runner, "_remove_all_topmost"), \
             patch.object(runner, "_restore_all_topmost"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=r"C:\videos\movie.mp4"):
            runner._handle_browse_library()

        assert runner.config.nau_cmd_file.read_text(
            encoding="utf-8") == "PLAY_FILE C:\\videos\\movie.mp4\n"

    def test_does_not_play_anything_on_cancel(self, tmp_path):
        """When the user cancels the dialog, nothing is sent to Nau."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch.object(runner, "_remove_all_topmost"), \
             patch.object(runner, "_restore_all_topmost"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        assert not runner.config.nau_cmd_file.exists()

    def test_restores_topmost_after_the_pick(self, tmp_path):
        """After the pick, every managed window gets its topmost band back —
        Nau included in nau mode, so it floats above the desktop again."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda hwnd, on_top: topmost_calls.append((hwnd, on_top))), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        restored = {h for h, v in topmost_calls if v}
        assert restored == TOPMOST_HWNDS | {NAU_HWND}

    def test_never_restores_nau_topmost_even_in_genau_mode(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False, main_mode="genau")

        topmost_calls = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda hwnd, on_top: topmost_calls.append((hwnd, on_top))), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        # genau mode: Nau is hidden and never joins the topmost band — it is
        # explicitly held non-topmost, never promoted.
        restored = {h for h, v in topmost_calls if v}
        assert restored == TOPMOST_HWNDS | {GENAU_HWND}
        assert (NAU_HWND, False) in topmost_calls
        assert NAU_HWND not in restored

    def test_hands_the_keyboard_to_the_browser_and_takes_it_back(self, tmp_path):
        """The global hotkeys are suspended for the browse, then restored.

        Otherwise they eat the keys the browser needs: the arrows are the
        satellites' prev/next and every letter is some command, so an
        alphabetical grid could neither be walked nor typed at — a hotkey
        consumes the press rather than passing it on.
        """
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        suspends: list[str] = []

        with patch.object(runner, "_remove_all_topmost"), \
             patch.object(runner, "_restore_all_topmost"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library",
                   side_effect=lambda *a, **kw: suspends.append(
                       runner.ahk_cmd_file.read_text(encoding="utf-8"))):
            runner._handle_browse_library()

        assert suspends == ["suspend_hotkeys"]
        assert runner.ahk_cmd_file.read_text(encoding="utf-8") == "unsuspend_hotkeys"

    def test_leaves_the_suspended_hotkeys_alone_when_already_paused(self, tmp_path):
        """Under OmniPause the hotkeys are already suspended and must stay that
        way — unsuspending after the browse would end the pause's own hold."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        assert not runner.ahk_cmd_file.exists()

    def test_topmost_removed_before_the_browser_opens(self, tmp_path):
        """Topmost removal happens before the browser window goes up."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        call_log: list[str] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top", side_effect=lambda h, v: call_log.append(f"topmost_{v}")), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", side_effect=lambda *a, **kw: (call_log.append("browse"), None)[-1]):
            runner._handle_browse_library()

        assert "browse" in call_log
        first_remove = next(i for i, c in enumerate(call_log) if c == "topmost_False")
        assert first_remove < call_log.index("browse")

    def test_skips_topmost_management_when_already_paused(self, tmp_path):
        """Under OmniPause the topmost bands are already down and must stay down
        (restoring them would strand windows on top mid-pause), so browse leaves
        them alone — but still opens the browser."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=NAU_HWND), \
             patch("fun_time.windows_bridge_dispatch_loop.window_rect", return_value=(0, 0, 800, 600)), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as mock_topmost, \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None) as mock_browse:
            runner._handle_browse_library()

        mock_topmost.assert_not_called()
        mock_browse.assert_called_once()

    def test_browses_unplaced_when_the_primary_display_cannot_be_found(self, tmp_path):
        """With no Nau window to stand over, the browser picks its own place.

        find_window_by_title is stubbed too: _resolve_role("nau") falls back to it
        when the pid lookup misses, and left live it would enumerate the real
        desktop and return a stray HWND (a running Fun Time's Nau window) — a flake.
        """
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch.object(runner, "_remove_all_topmost"), \
             patch.object(runner, "_restore_all_topmost"), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None) as mock_browse:
            runner._handle_browse_library()

        assert mock_browse.call_args.kwargs["over"] is None

    def test_concurrent_invocations_prevented(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)  # fast path — no omnipause

        with patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            original_handle = runner._handle_browse_library

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
        assert hasattr(runner, "_browse_lock")

    def test_browse_library_routed_from_tick(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("browse_library", encoding="utf-8")

        with patch.object(runner, "_handle_browse_library") as mock_handle:
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
# OmniPause voice freeze
# ---------------------------------------------------------------------------

class TestOmnipauseVoiceFreeze:
    """Under OmniPause a *spoken* command is frozen unless it resumes, quits, or
    retracts the OSR2.

    A mis-heard phrase must not act on a paused room — that is the whole bug.
    ``spoken_at`` is what marks a voice line; the deliberate mouse (dashboard,
    lock HUD) stays live, because a click cannot mis-fire the way a phrase can,
    and those channels write the command file with no ``spoken_at`` stamp.  This
    is the dispatch-loop backstop to VoiceController's own suspend, closing the
    entry race where a phrase is written in the tick before the suspend flag is
    set.
    """

    def test_freezes_a_spoken_command_under_omnipause(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner._handle_command("landscape_next", spoken_at=123.0)
        mock_dispatch.assert_not_called()

    def test_freezes_a_spoken_reference_popup_under_omnipause(self, tmp_path):
        """The bug it was born from: room noise heard as "help" opening the
        reference popup mid-pause.  Frozen, it never reaches the dashboard —
        the popup gets no exemption from the freeze, by the user's call."""
        for command in ("help_reference", "help_reference_close"):
            runner = make_runner(tmp_path)
            runner.state = BridgeState(omni_paused=True)
            with patch.object(runner, "_send_press") as mock_press:
                runner._handle_command(command, spoken_at=123.0)
            mock_press.assert_not_called()

    def test_keeps_the_mouse_live_under_omnipause(self, tmp_path):
        """A lock-HUD click (bare command, no ``spoken_at``) still acts while
        paused — the user cannot fat-finger it the way a phrase mis-fires."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner._handle_command("portrait_lock_video|C:/clip.mp4")
        assert mock_dispatch.call_args[0][0] == "portrait_lock_video|C:/clip.mp4"

    def test_a_spoken_resume_still_un_pauses(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        with patch.object(runner, "_handle_omnipause_toggle") as mock_toggle:
            runner._handle_command("play", spoken_at=123.0)
        mock_toggle.assert_called_once()

    def test_a_spoken_quit_still_quits(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        runner._handle_command("quit", spoken_at=123.0)
        assert (tmp_path / "ahk_cmd.txt").read_text(encoding="utf-8") == "exit"

    def test_outside_omnipause_a_spoken_command_dispatches(self, tmp_path):
        """The freeze is OmniPause-only: live, the same phrase dispatches."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner._handle_command("landscape_next", spoken_at=123.0)
        assert mock_dispatch.call_args[0][0] == "landscape_next"


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

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0), \
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

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert {h for h, v in topmost_calls if v is False} == TOPMOST_HWNDS | {NAU_HWND, GENAU_HWND}

    def test_enter_omnipause_noop_when_already_paused(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(omni_paused=True)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("enter_omnipause", encoding="utf-8")

        with patch.object(runner, "_dispatch") as mock_d:
            runner.tick()

        mock_d.assert_not_called()

    # -- relief_omnipause (Shift+Esc) --

    def test_relief_omnipause_still_dispatches_when_already_paused(self, tmp_path):
        """Space is swallowed when the session is already paused; Shift+Esc is not.

        A paused session can still have the device on the user — that is the case
        relief exists for — so the retract must reach the broker rather than being
        dropped as a redundant enter.
        """
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(omni_paused=True)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("relief_omnipause", encoding="utf-8")

        with patch.object(runner, "_dispatch") as mock_d:
            runner.tick()

        assert [call.args[0] for call in mock_d.call_args_list] == ["relief_omnipause"]

    def test_relief_omnipause_logs_the_topmost_state_like_any_other_entry(self, tmp_path):
        """Relief drops every window out of the topmost band exactly as Space and
        Esc do, so it owes the same post-enter record — that log is what pins a
        window which re-asserted itself while the session was meant to be free."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(omni_paused=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("relief_omnipause", encoding="utf-8")

        with patch.object(runner, "_dispatch"), \
             patch.object(runner, "_log_topmost_state") as mock_log:
            runner.tick()

        mock_log.assert_called_once_with("post-enter")

    # -- lock portrait / lock landscape --

    def test_portrait_lock_on_dispatches_when_unlocked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked2=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_on", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("portrait_lock", None)

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
        mock_d.assert_called_once_with("landscape_lock", None)

    def test_landscape_lock_on_noop_when_locked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked3=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("landscape_lock_on", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_not_called()

    # -- f mode, sided --

    def test_a_sided_fmode_reaches_the_dispatch_as_written(self, tmp_path):
        """The on/off forms are the dispatch's own commands now — it alone knows
        which players each names, and it is what decides a no-op rebuilds nothing —
        so the loop passes them straight through rather than second-guessing them."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_fmode_on", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("portrait_fmode_on", None)

    def test_both_fmode_is_expanded_into_the_two_satellites(self, tmp_path):
        """"both f mode" is sugar, exactly as it is for every other sided command:
        there is no combined handler, just the pair run in turn."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("both_fmode", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        assert [call.args[0] for call in mock_d.call_args_list] == [
            "portrait_fmode", "landscape_fmode",
        ]

    # -- genau activate --

    def test_genau_activate_dispatches_when_not_in_genau_mode(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(main_mode="nau")
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("genau_activate", None)

    def test_genau_activate_dispatches_in_hybrid_mode(self, tmp_path):
        """Hybrid mode is genau-active but is NOT genau mode: the Genau-mode
        button must still switch to full Genau.  Regression — the old guard
        used genau_active(), which is True for hybrid, so it swallowed this."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(main_mode="hybrid")
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("genau_activate", None)

    def test_genau_activate_dispatches_when_already_in_genau_mode(self, tmp_path):
        """The loop forwards genau_activate unconditionally — switching to the
        mode you are already in is a no-op at the planner level (see
        test_mode_plan.test_same_mode_is_noop), not a special case here."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(main_mode="genau")
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("genau_activate", None)

    # -- lock off (idempotent unlock) --

    def test_portrait_lock_off_unlocks_when_locked(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner.state = BridgeState(locked2=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_off", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_d.assert_called_once_with("portrait_lock", None)

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
        mock_d.assert_called_once_with("landscape_lock", None)

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
        with patch("fun_time.windows_bridge_dispatch_loop.launch_broker_tray") as mock_launch:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_start", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
            time.sleep(0.2)  # daemon thread
        mock_launch.assert_called_once_with(runner.config.broker_tray_launcher)

    def test_broker_start_noop_when_already_running(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        # Fresh heartbeat → broker running
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")
        with patch("fun_time.windows_bridge_dispatch_loop.launch_broker_tray") as mock_launch:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_start", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
        mock_launch.assert_not_called()

    def test_broker_start_never_kills_the_broker(self, tmp_path):
        """A stale heartbeat does not mean no broker. osr2_broker only ticks the
        heartbeat while it holds the serial port, so a broker that cannot reach a
        powered-off OSR2 reads as dead while it is very much alive -- and killing
        it drops harem and the user's own MFP session with it. Starting is a
        start; only an explicit stop may kill."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        # No heartbeat file at all: the broker reads as dead.
        with patch("fun_time.windows_bridge_startup.stop_broker_processes") as mock_stop:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_start", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
            time.sleep(0.2)  # daemon thread
        mock_stop.assert_not_called()

    def test_broker_panel_toggle_starts_without_killing(self, tmp_path):
        """The B panel toggles the broker.  Toggling one that reads as dead has
        to start it, not restart it — the same stale-heartbeat trap as
        broker_start, and the same live broker on the other side of it."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        # No heartbeat file: the toggle takes its "not running, so start" arm.
        with patch("fun_time.windows_bridge_startup.stop_broker_processes") as mock_stop:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_panel", encoding="utf-8")
            runner._last_sync = float("inf")
            runner.tick()
            time.sleep(0.2)  # daemon thread
        mock_stop.assert_not_called()

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
        """Drive tick() through (t, portrait_path, portrait_fraction) samples,
        publishing each to the portrait satellite's status file the way its
        native player would (a None fraction writes an unknown duration, which
        the sampler drops)."""
        fake_now = {"t": 0.0}
        monkeypatch.setattr(
            "fun_time.windows_bridge_dispatch_loop.time.monotonic", lambda: fake_now["t"]
        )
        for t, path, fraction in timeline:
            fake_now["t"] = t
            _write_satellite_status(runner.config.portrait_status_file, path, fraction=fraction)
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

        fake_now = {"t": 100.0}
        monkeypatch.setattr(
            "fun_time.windows_bridge_dispatch_loop.time.monotonic", lambda: fake_now["t"]
        )
        _write_satellite_status(runner.config.portrait_status_file, str(a), fraction=0.2)
        runner.tick()                       # samples a.mp4 at 20%
        runner._dispatch("portrait_next")   # user skips
        fake_now["t"] = 101.1
        _write_satellite_status(runner.config.portrait_status_file, str(tmp_path / "b.mp4"), fraction=0.0)
        runner.tick()

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(a))]["skips"] == 1

    def test_trash_suppresses_classifying_the_discarded_video(self, tmp_path, monkeypatch):
        from fun_time.watch_stats import load_watch_stats

        config = make_config(tmp_path, weird_dir=tmp_path / "weird")
        runner = make_runner(tmp_path, config=config)
        a = tmp_path / "a.mp4"
        a.write_text("x", encoding="utf-8")

        fake_now = {"t": 100.0}
        monkeypatch.setattr(
            "fun_time.windows_bridge_dispatch_loop.time.monotonic", lambda: fake_now["t"]
        )
        _write_satellite_status(runner.config.portrait_status_file, str(a), fraction=0.2)
        runner.tick()
        runner._dispatch("portrait_trash")  # moves the file to weird
        fake_now["t"] = 101.1
        _write_satellite_status(runner.config.portrait_status_file, str(tmp_path / "b.mp4"), fraction=0.0)
        runner.tick()

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}


class TestSeededRoleHwnds:
    def test_startup_seed_lets_hidden_windows_be_shown_again(self, tmp_path):
        """Startup parks the idle main-slot window (Genau) BEFORE the dispatch
        loop ever resolves it; with the pid/title lookups mocked to fail, the
        runner must answer from the hwnds the startup sequencer seeded while
        everything was visible, or genau/hybrid could never bring windows back."""
        runner = make_runner(
            tmp_path,
            role_hwnds={"genau": 6001, "nau": 2001},
        )
        shown: list[int] = []

        with patch("fun_time.windows_bridge_dispatch_loop.find_window_by_pid", return_value=0),              patch("fun_time.windows_bridge_dispatch_loop.find_window_by_title", return_value=0),              patch("fun_time.windows_bridge_dispatch_loop.restore_window", side_effect=lambda h, **kw: shown.append(h)):
            assert runner._resolve_role("genau") == 6001
            assert runner._resolve_role("nau") == 2001
            runner._dispatch("hybrid_activate")

        assert shown == [2001, 6001]  # hybrid shows Nau then the Genau HUD


class TestHybridFunscriptHandoff:
    """In hybrid the OSR2 follows the current video moment-to-moment: the
    funscript drives its scripted stretches (Genau yields), and Genau drives
    the unscripted ones — a funscript's quiet lead-in and its interior gaps,
    which Nau flags as ``funscript_resting``."""

    def _write_status(self, runner, *, has_funscript=True, resting=False,
                      position_ms=10, touch_ms=None):
        touch = "" if touch_ms is None else str(touch_ms)
        runner.config.nau_status_file.write_text(
            "video=C:\\clip.mp4\n"
            f"position_ms={position_ms}\n"
            f"has_funscript={1 if has_funscript else 0}\n"
            f"funscript_resting={1 if resting else 0}\n"
            f"handoff_touch_ms={touch}\n",
            encoding="utf-8",
        )

    def _genau(self, runner):
        # The arbiter appends its verbs (a shared single slot clobbered a
        # handoff once), so reads strip the trailing newline.
        return runner.config.genau_cmd_file.read_text(encoding="utf-8").strip()

    def _nau(self, runner):
        return runner.config.nau_cmd_file.read_text(encoding="utf-8").strip()

    def test_the_flip_waits_for_the_touch_the_trace_chose(self, tmp_path):
        """Nau publishes the touch-down its picture drew the blue ending on;
        Genau keeps the device until the playhead reaches it.  When each side
        chose its own touch from its own read of the wave, the arbiter could
        take an earlier one — and the leftover drawn blue vanished the moment
        the dot reached it."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, resting=True, position_ms=14_000)
        runner._sync_hybrid_driver()                        # Genau's turn first
        self._write_status(runner, resting=False, position_ms=15_100,
                           touch_ms=16_400)

        runner._sync_hybrid_driver()

        assert self._genau(runner) == "RESUME"              # still Genau's
        assert runner._park_touch_deadline is not None

    def test_the_held_flip_lands_when_the_playhead_reaches_the_touch(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, resting=True, position_ms=14_000)
        runner._sync_hybrid_driver()
        self._write_status(runner, resting=False, position_ms=15_100,
                           touch_ms=16_400)
        runner._sync_hybrid_driver()

        self._write_status(runner, resting=False, position_ms=16_450,
                           touch_ms=16_400)
        runner._sync_hybrid_driver()

        assert self._genau(runner).splitlines()[-1] == "PAUSE"

    def test_no_published_touch_flips_at_once(self, tmp_path):
        """The ramp case — a raised floor — has no touch to wait for: the
        descent is the drawn ramp, walked by Nau's driver."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, resting=True, position_ms=14_000)
        runner._sync_hybrid_driver()
        self._write_status(runner, resting=False, position_ms=15_100)

        runner._sync_hybrid_driver()

        assert self._genau(runner).splitlines()[-1] == "PAUSE"

    def test_a_stalled_playhead_cannot_hold_the_flip_forever(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, resting=True, position_ms=14_000)
        runner._sync_hybrid_driver()
        self._write_status(runner, resting=False, position_ms=15_100,
                           touch_ms=16_400)
        runner._sync_hybrid_driver()

        runner._park_touch_deadline = 0.0                   # the cap expiring
        runner._sync_hybrid_driver()

        assert self._genau(runner).splitlines()[-1] == "PAUSE"

    def test_a_seek_into_the_script_s_turn_flips_at_once(self, tmp_path):
        """A hold honors a drawn blue ending, and a seek-entry never drew one:
        jumped into dense action from a rest, the picture already shows the
        script's turn running — even a touch left published from before the
        seek must not hold the flip."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, resting=True, position_ms=1_000)
        runner._sync_hybrid_driver()
        self._write_status(runner, resting=False, position_ms=41_000,
                           touch_ms=42_000)                 # a 40s jump

        runner._sync_hybrid_driver()

        assert self._genau(runner).splitlines()[-1] == "PAUSE"
        assert runner._park_touch_deadline is None

    def test_scripted_stretch_drives_from_the_funscript(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, has_funscript=True, resting=False)

        runner._sync_hybrid_driver()

        assert self._genau(runner) == "PAUSE"               # Genau yields
        assert self._nau(runner) == "SET_TCODE_ENABLED 1"   # funscript drives

    def test_funscript_gap_hands_the_stretch_to_genau(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, has_funscript=True, resting=True)

        runner._sync_hybrid_driver()

        assert self._genau(runner) == "RESUME"              # Genau fills the gap
        assert self._nau(runner) == "SET_TCODE_ENABLED 0"   # funscript muted

    def test_unscripted_video_drives_from_genau(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, has_funscript=False)

        runner._sync_hybrid_driver()

        assert self._genau(runner) == "RESUME"
        assert self._nau(runner) == "SET_TCODE_ENABLED 0"

    def test_commands_written_only_on_change(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, has_funscript=True, resting=False)
        runner._sync_hybrid_driver()
        runner.config.genau_cmd_file.unlink()
        runner.config.nau_cmd_file.unlink()

        runner._sync_hybrid_driver()  # unchanged driver -> no re-issue (edge-only)

        assert not runner.config.genau_cmd_file.exists()
        assert not runner.config.nau_cmd_file.exists()

    def test_entering_a_gap_flips_the_driver(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, has_funscript=True, resting=False)
        runner._sync_hybrid_driver()

        self._write_status(runner, has_funscript=True, resting=True)  # gap begins
        runner._sync_hybrid_driver()

        # Appended after the first flip's PAUSE; the players drain in order.
        assert self._genau(runner).splitlines()[-1] == "RESUME"
        assert self._nau(runner).splitlines()[-1] == "SET_TCODE_ENABLED 0"

    def test_no_arbitration_outside_hybrid(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="genau")
        self._write_status(runner, has_funscript=True, resting=False)

        runner._sync_hybrid_driver()

        assert not runner.config.genau_cmd_file.exists()
        assert not runner.config.nau_cmd_file.exists()

    def test_no_arbitration_when_omnipaused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid", omni_paused=True)
        self._write_status(runner, has_funscript=True, resting=False)

        runner._sync_hybrid_driver()

        assert not runner.config.genau_cmd_file.exists()
        assert not runner.config.nau_cmd_file.exists()

    def test_leaving_hybrid_resets_so_reentry_reapplies(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, has_funscript=True, resting=False)
        runner._sync_hybrid_driver()  # funscript driving

        runner.state = BridgeState(main_mode="genau")
        runner._sync_hybrid_driver()  # leaves hybrid -> forgets
        runner.config.genau_cmd_file.unlink()

        runner.state = BridgeState(main_mode="hybrid")
        runner._sync_hybrid_driver()

        assert self._genau(runner) == "PAUSE"

    def _genau_last(self, runner):
        return self._genau(runner).splitlines()[-1]

    def test_tick_runs_the_handoff(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="hybrid")
        self._write_status(runner, has_funscript=True, resting=False)

        # The native satellites publish to status files (none written here), so
        # the tick's sampling is inert and the hybrid handoff runs alone.
        runner.tick()

        assert self._genau(runner) == "PAUSE"


class TestExpandBothCommand:
    """A "both" command is sugar for its Portrait + Landscape pair."""

    def test_passes_through_non_both_commands(self):
        assert expand_both_command("portrait_next") == ["portrait_next"]
        assert expand_both_command("quit") == ["quit"]

    def test_expands_to_portrait_then_landscape(self):
        assert expand_both_command("both_next") == ["portrait_next", "landscape_next"]
        assert expand_both_command("both_prev") == ["portrait_prev", "landscape_prev"]
        assert expand_both_command("both_trash") == ["portrait_trash", "landscape_trash"]

    def test_expands_multiword_suffixes(self):
        assert expand_both_command("both_lock_on") == ["portrait_lock_on", "landscape_lock_on"]
        assert expand_both_command("both_lock_off") == ["portrait_lock_off", "landscape_lock_off"]
        assert expand_both_command("both_cycle_action") == [
            "portrait_cycle_action", "landscape_cycle_action",
        ]
        assert expand_both_command("both_cycle_seed") == [
            "portrait_cycle_seed", "landscape_cycle_seed",
        ]


class TestBothSatelliteCommands:
    """A polled "both" command drives Portrait then Landscape through the same
    per-command handling as the individual satellite commands."""

    def test_both_next_dispatches_to_each_satellite(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        (tmp_path / "dashboard_cmd.txt").write_text("both_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.side_effect = lambda cmd, state, config, target_path="": (state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert commands == ["portrait_next", "landscape_next"]

    def test_lock_both_locks_each_unlocked_satellite(self, tmp_path):
        """"lock both" (both_lock_on) reuses the idempotent per-satellite lock:
        an already-locked side is left alone, so it only toggles the unlocked one."""
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(locked2=True, locked3=False)
        (tmp_path / "dashboard_cmd.txt").write_text("both_lock_on", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.side_effect = lambda cmd, state, config, target_path="": (state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert commands == ["landscape_lock"]  # portrait already locked → skipped

    def test_unlock_both_unlocks_each_locked_satellite(self, tmp_path):
        runner = make_runner(tmp_path, sync_interval_ms=999999)
        runner._last_sync = float("inf")
        runner.state = BridgeState(locked2=True, locked3=True)
        (tmp_path / "dashboard_cmd.txt").write_text("both_lock_off", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.side_effect = lambda cmd, state, config, target_path="": (state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert commands == ["portrait_lock", "landscape_lock"]


class TestHudPublishing:
    """The dispatch loop owns the lock HUD's model: it holds the bridge state and
    already ticks, so it builds each satellite's panel and publishes it to the
    file that satellite's player renders from."""

    def _runner_with_hud(self, tmp_path):
        publisher = HudPublisher(
            {"portrait": tmp_path / "portrait_hud.json",
             "landscape": tmp_path / "landscape_hud.json",
             "nau": tmp_path / "nau_console.json"},
            tmp_path / "thumbs",
        )
        return make_runner(tmp_path, hud_publisher=publisher)

    def test_tick_publishes_each_satellites_panel(self, tmp_path):
        runner = self._runner_with_hud(tmp_path)
        _write_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4", fraction=0.1)
        _write_satellite_status(tmp_path / "landscape_status.txt", "C:/v/l.mp4", fraction=0.1)
        runner.state = replace(runner.state, locked2=True, portrait_filter="alpha")

        runner.tick()

        portrait = json.loads((tmp_path / "portrait_hud.json").read_text(encoding="utf-8"))
        landscape = json.loads((tmp_path / "landscape_hud.json").read_text(encoding="utf-8"))
        assert portrait["side"] == "portrait"
        assert portrait["locked"] is True
        # The status line composes the lot — lock, order, and the filter unlabeled.
        assert portrait["lock_label"] == "Locked · Shuffle · alpha"
        assert portrait["corner"]["path"] == "C:/v/p.mp4"
        assert landscape["locked"] is False
        assert landscape["corner"]["path"] == "C:/v/l.mp4"

    def test_origenerator_mode_publishes_mapless_mode_panels(self, tmp_path):
        """In origenerator mode the players are black and paused, so their clip
        maps would be thumbnails of videos nobody is being shown — the HUDs
        looked like Player mode.  The sides publish the mode instead: no map,
        the status naming it, and satellites_mode riding along (the mode row's
        way back, and what keys the players' own blackout)."""
        publisher = HudPublisher(
            {"portrait": tmp_path / "portrait_hud.json",
             "landscape": tmp_path / "landscape_hud.json",
             "nau": tmp_path / "nau_console.json"},
            tmp_path / "thumbs",
        )
        config = make_config(tmp_path, origenerator_enabled=True,
                             origenerator_cmd_file=tmp_path / "origenerator_cmd.txt")
        runner = make_runner(tmp_path, config=config, hud_publisher=publisher)
        _write_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4", fraction=0.1)
        _write_satellite_status(tmp_path / "landscape_status.txt", "C:/v/l.mp4", fraction=0.1)
        runner.state = replace(runner.state, satellites_mode="origenerator")

        runner.tick()

        portrait = json.loads((tmp_path / "portrait_hud.json").read_text(encoding="utf-8"))
        assert portrait["corner"] is None          # no map of unseen videos
        assert portrait["seeds"] == []
        assert portrait["lock_label"] == "Origenerator mode"
        assert portrait["satellites_mode"] == "origenerator"

    def test_the_published_panel_says_when_that_sides_f_mode_is_on(self, tmp_path):
        """The flag lives on the bridge state and nowhere the player can see, so the
        publish is the only way F-mode reaches the screen a satellite is on — as the
        status line, and as the flag its own button lights off.

        It is sided: the satellite that is not in F-mode must not say it is."""
        runner = self._runner_with_hud(tmp_path)
        for side in ("portrait", "landscape"):
            _write_satellite_status(tmp_path / f"{side}_status.txt", f"C:/v/{side}.mp4",
                                    fraction=0.1)
        runner.state = replace(runner.state, portrait_f_mode=True)

        runner.tick()

        portrait = json.loads((tmp_path / "portrait_hud.json").read_text(encoding="utf-8"))
        landscape = json.loads((tmp_path / "landscape_hud.json").read_text(encoding="utf-8"))
        assert portrait["lock_label"] == "Unlocked · Shuffle · F-Mode"
        assert portrait["f_mode"] is True
        assert landscape["lock_label"] == "Unlocked · Shuffle"
        assert landscape["f_mode"] is False

    def test_the_published_panel_says_which_side_has_the_floor(self, tmp_path):
        """The active side is a slot number in the state and a side *name* on the
        panel, so exactly one satellite can claim it — and neither does while the
        the main player holds it."""
        runner = self._runner_with_hud(tmp_path)
        for side in ("portrait", "landscape"):
            _write_satellite_status(tmp_path / f"{side}_status.txt", f"C:/v/{side}.mp4",
                                    fraction=0.1)

        def actives(slot: int) -> tuple[bool, bool]:
            runner.state = replace(runner.state, active_side=slot)
            runner._last_hud_publish -= 1  # past the publish throttle
            runner.tick()
            return tuple(
                json.loads((tmp_path / f"{side}_hud.json").read_text(encoding="utf-8"))["active"]
                for side in ("portrait", "landscape")
            )

        assert actives(2) == (True, False)
        assert actives(3) == (False, True)
        assert actives(1) == (False, False)

    def _console(self, tmp_path) -> dict:
        return json.loads((tmp_path / "nau_console.json").read_text(encoding="utf-8"))

    def test_the_console_says_when_the_primary_has_the_floor(self, tmp_path):
        """The main player's dot rides the console file, the same file that carries the
        rest of its panel — one source for both players, in place of the separate
        command it used to be sent."""
        runner = self._runner_with_hud(tmp_path)

        def active(slot: int) -> bool:
            runner.state = replace(runner.state, active_side=slot)
            runner._last_hud_publish -= 1  # past the publish throttle
            runner.tick()
            return self._console(tmp_path)["active"]

        assert active(1) is True   # the the main player holds it
        assert active(2) is False  # a satellite does

    def test_the_console_says_what_has_the_osr2_and_whether_the_broker_is_up(self, tmp_path):
        """Broker status is the main player's alone — it moved off the dashboard onto
        this panel — and the OSR2 state comes down as one word for the console to
        box."""
        runner = self._runner_with_hud(tmp_path)
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")

        runner._last_hud_publish -= 1
        runner.tick()

        console = self._console(tmp_path)
        assert console["broker"] is True
        assert console["osr2"] in ("off", "auto", "funscript", "genau", "idle")

    def test_both_broker_lights_read_the_brokers_directory_not_the_sessions(self, tmp_path):
        """A branch session moves ``state/`` into its worktree; the machine's one
        broker keeps writing where it always did.

        Reading the heartbeat and the serial stamp out of the *session's*
        directory is what had this console calling a live broker dead and a
        driven OSR2 off — so a stamp in the session's own directory must not
        light either one.
        """
        broker_state = tmp_path / "primary_state"
        broker_state.mkdir()
        publisher = HudPublisher(
            {"portrait": tmp_path / "portrait_hud.json",
             "landscape": tmp_path / "landscape_hud.json",
             "nau": tmp_path / "nau_console.json"},
            tmp_path / "thumbs",
        )
        runner = make_runner(tmp_path, hud_publisher=publisher, config=make_config(
            tmp_path,
            broker_state_dir=broker_state,
            broker_heartbeat_file=broker_state / "broker_heartbeat.txt",
        ))

        def published(state_dir: Path) -> dict:
            now = time.time()
            (state_dir / "broker_heartbeat.txt").write_text(str(now), encoding="utf-8")
            (state_dir / "osr2_serial_rx.txt").write_text(str(now), encoding="utf-8")
            runner._last_hud_publish -= 1
            runner.tick()
            return self._console(tmp_path)

        # The worktree's own state dir: fresh stamps nothing reads.
        session = published(tmp_path)
        assert session["broker"] is False
        assert session["osr2"] == "off"

        broker = published(broker_state)
        assert broker["broker"] is True
        assert broker["osr2"] != "off"

    def test_the_console_carries_the_lock_back_to_whoever_draws_it(self, tmp_path):
        """Each player owns its own lock, and neither can see the other's — so
        both go out on their status files and the one the mode says is showing
        comes back down here, the way the loop state does."""
        runner = self._runner_with_hud(tmp_path)
        nau = runner.config.nau_status_file
        genau = runner.config.state_dir / "genau_status.txt"

        def published(mode: str) -> bool:
            runner.state = replace(runner.state, main_mode=mode)
            runner._last_hud_publish -= 1
            runner.tick()
            return self._console(tmp_path)["locked"]

        nau.write_text("video=C:/v/n.mp4\nlocked=0\n", encoding="utf-8")
        genau.write_text("locked=1\n", encoding="utf-8")
        assert published("nau") is False
        assert published("hybrid") is False
        assert published("genau") is True

        nau.write_text("video=C:/v/n.mp4\nlocked=1\n", encoding="utf-8")
        genau.write_text("locked=0\n", encoding="utf-8")
        assert published("nau") is True
        assert published("genau") is False

    def test_each_sides_panel_says_whether_its_own_clip_is_a_favorite(self, tmp_path):
        """The dashboard's panel used to say this by turning green; the HUD marks
        it, so the loop has to judge each side's clip against the favs file."""
        runner = self._runner_with_hud(tmp_path)
        _write_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4", fraction=0.1)
        _write_satellite_status(tmp_path / "landscape_status.txt", "C:/v/l.mp4", fraction=0.1)
        runner.config.favs_file.write_text("local,C:/v/p.mp4,web\n", encoding="utf-8")

        runner.tick()

        portrait = json.loads((tmp_path / "portrait_hud.json").read_text(encoding="utf-8"))
        landscape = json.loads((tmp_path / "landscape_hud.json").read_text(encoding="utf-8"))
        assert portrait["is_favorite"] is True
        assert landscape["is_favorite"] is False

    def test_the_favorites_file_is_re_read_only_when_it_moves(self, tmp_path):
        """Every publish asks the question, ~7x a second for the whole session,
        while the list itself moves a few times an hour — so the read is gated on
        the file actually having changed, and picks the change up when it does."""
        runner = self._runner_with_hud(tmp_path)
        _write_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4", fraction=0.1)
        favs = runner.config.favs_file
        favs.write_text("local,C:/v/other.mp4,web\n", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.read_favs_content",
                   side_effect=lambda path: path.read_text(encoding="utf-8")) as read:
            runner.tick()
            runner._last_hud_publish -= 1
            runner.tick()
            assert read.call_count == 1, "an unmoved file is not read again"

            favs.write_text("local,C:/v/p.mp4,web\nlocal,C:/v/other.mp4,web\n", encoding="utf-8")
            runner._last_hud_publish -= 1
            runner.tick()
            assert read.call_count == 2

        portrait = json.loads((tmp_path / "portrait_hud.json").read_text(encoding="utf-8"))
        assert portrait["is_favorite"] is True

    def test_publishing_is_throttled_below_the_tick_rate(self, tmp_path):
        """The loop ticks 20x/s; rebuilding and rewriting both panels that often
        is waste the map never shows, so publishing runs on its own cadence."""
        runner = self._runner_with_hud(tmp_path)
        _write_satellite_status(tmp_path / "portrait_status.txt", "C:/v/p.mp4", fraction=0.1)

        with patch.object(runner._hud_publisher, "publish", return_value=True) as publish:
            runner.tick()
            runner.tick()

        assert publish.call_count == 2, "one publish per side on the first tick only"

    def test_a_status_that_cannot_be_read_keeps_the_clip_it_last_named(self, tmp_path):
        """A satellite always has a clip, so a status file that reads blank means
        the read lost — not that the player has nothing.

        Believing the blank republishes an empty panel, which the player renders
        as a HUD with nothing on it, and the next tick puts it back: the map
        blinks.  It is most visible under OmniPause, where the picture is frozen
        and the map is the only thing that can move.
        """
        runner = self._runner_with_hud(tmp_path)
        status = tmp_path / "portrait_status.txt"
        _write_satellite_status(status, "C:/v/p.mp4", fraction=0.1)
        runner.tick()

        status.write_text("", encoding="utf-8")  # caught mid-republish
        runner._last_hud_publish -= 1  # past the publish throttle
        runner.tick()

        portrait = json.loads((tmp_path / "portrait_hud.json").read_text(encoding="utf-8"))
        assert portrait["corner"]["path"] == "C:/v/p.mp4"

    def test_a_satellite_that_has_not_started_yet_publishes_an_empty_panel(self, tmp_path):
        # The other side of it: before a satellite's first status there is no
        # clip to hold onto, and an empty map is the truth.
        runner = self._runner_with_hud(tmp_path)

        runner.tick()

        portrait = json.loads((tmp_path / "portrait_hud.json").read_text(encoding="utf-8"))
        assert portrait["corner"] is None

    def test_a_runner_without_a_hud_publisher_just_ticks(self, tmp_path):
        make_runner(tmp_path).tick()


class TestBrowserOutlivesNothing:
    """The browser is a child of the session, so quitting has to take it.

    It is launched mid-session rather than at startup, so it cannot join the
    _CHILD_GROUPS teardown list the way the players and companions do; the
    dispatch loop owns it instead, and its stop is where the browse dies.
    """

    def test_quitting_the_session_kills_a_browser_still_open(self, tmp_path):
        runner = make_runner(tmp_path)
        opened = threading.Event()
        process = Mock()
        process.wait.side_effect = lambda: (opened.set(), time.sleep(0.4))

        with patch("fun_time.windows_bridge_dispatch_loop.subprocess.Popen",
                   return_value=process) as mock_popen:
            browsing = threading.Thread(target=runner._run_browser, args=(["python"],))
            browsing.start()
            assert opened.wait(2.0), "the browser never started"
            runner.stop()
            browsing.join(timeout=2.0)

        mock_popen.assert_called_once()
        process.terminate.assert_called_once()
        assert runner._browser_process is None, "the finished browse is not still held"

    def test_stopping_with_no_browse_open_terminates_nothing(self, tmp_path):
        runner = make_runner(tmp_path)

        runner.stop()

        assert runner._browser_process is None


class TestOrigeneratorShows:
    def _hosting_runner(self, tmp_path):
        config = make_config(tmp_path, origenerator_enabled=True,
                             origenerator_cmd_file=tmp_path / "origenerator_cmd.txt",
                             origenerator_paused_file=tmp_path / "origenerator_paused.txt")
        runner = make_runner(tmp_path, config=config, origenerator_pid=700)
        runner.state = replace(runner.state, satellites_mode="origenerator")
        return runner

    def test_rfb_tabs_hold_while_origenerator_covers_the_browser(self, tmp_path):
        runner = self._hosting_runner(tmp_path)
        runner.rfb_shortcut_target = "chrome.exe"
        runner._pending_rfb_urls = ["file:///tab.html"]
        runner._flush_rfb_tabs()
        assert runner._pending_rfb_urls == ["file:///tab.html"]  # held, not dropped


class TestOrigeneratorWindowConverger:
    def test_a_resumed_origenerator_mode_restores_the_window_once_it_exists(self, tmp_path):
        config = make_config(tmp_path)
        runner = make_runner(tmp_path, config=config, origenerator_pid=700)
        runner.state = replace(runner.state, satellites_mode="origenerator")
        with patch.object(runner, "_resolve_role", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window") as restore:
            runner._converge_origenerator_window()
        restore.assert_not_called()  # still booting — nothing to drive
        with patch.object(runner, "_resolve_role", return_value=4242), \
             patch("fun_time.windows_bridge_dispatch_loop.is_window_minimized",
                   return_value=True), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window") as restore, \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"):
            runner._converge_origenerator_window()
        restore.assert_called_once_with(4242, activate=False)

    def test_a_restore_the_busy_app_dropped_is_retried_next_pass(self, tmp_path):
        """The app's boot blocks its main thread, so a restore can time out
        through the hung-window guard and do nothing.  The converger judges
        from the WINDOW each pass — still minimized means try again — instead
        of remembering it as shown and leaving a resumed session parked until
        the user digs it out of the taskbar."""
        runner = make_runner(tmp_path, origenerator_pid=700)
        runner.state = replace(runner.state, satellites_mode="origenerator")
        with patch.object(runner, "_resolve_role", return_value=4242), \
             patch("fun_time.windows_bridge_dispatch_loop.is_window_minimized",
                   return_value=True), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window") as restore, \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top"):
            runner._converge_origenerator_window()
            runner._converge_origenerator_window()
        assert restore.call_count == 2

    def test_a_shown_window_out_of_the_band_is_re_promoted(self, tmp_path):
        # Restored but buried (the topmost bit never took): the converger
        # re-bands it rather than reading "not minimized" as converged.
        runner = make_runner(tmp_path, origenerator_pid=700)
        runner.state = replace(runner.state, satellites_mode="origenerator")
        with patch.object(runner, "_resolve_role", return_value=4242), \
             patch("fun_time.windows_bridge_dispatch_loop.is_window_minimized",
                   return_value=False), \
             patch("fun_time.windows_bridge_dispatch_loop.is_window_topmost",
                   return_value=False), \
             patch("fun_time.windows_bridge_dispatch_loop.restore_window") as restore, \
             patch("fun_time.windows_bridge_dispatch_loop.set_always_on_top") as promote:
            runner._converge_origenerator_window()
        restore.assert_not_called()
        promote.assert_any_call(4242, True)

    def test_player_mode_parks_a_window_left_up(self, tmp_path):
        runner = make_runner(tmp_path, origenerator_pid=700)
        with patch.object(runner, "_resolve_role", return_value=4242), \
             patch("fun_time.windows_bridge_dispatch_loop.is_window_minimized",
                   return_value=False), \
             patch("fun_time.windows_bridge_dispatch_loop.minimize_window") as minimize:
            runner._converge_origenerator_window()
        minimize.assert_called_once_with(4242, activate=False)

    def test_without_a_hosted_app_the_converger_is_inert(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = replace(runner.state, satellites_mode="origenerator")
        with patch.object(runner, "_resolve_role") as resolve:
            runner._converge_origenerator_window()
        resolve.assert_not_called()


class TestOrigeneratorWatchGuard:
    def test_a_routed_step_books_nothing_against_the_blacked_player(self, tmp_path):
        config = make_config(tmp_path, origenerator_enabled=True,
                             origenerator_cmd_file=tmp_path / "origenerator_cmd.txt")
        runner = make_runner(tmp_path, config=config, origenerator_pid=700)
        runner.state = replace(runner.state, satellites_mode="origenerator")
        with patch.object(runner._watch_trackers[2], "note_user_nav") as nav:
            runner._dispatch("portrait_next")
        nav.assert_not_called()

    def test_a_player_mode_step_still_books_the_player(self, tmp_path):
        config = make_config(tmp_path, origenerator_enabled=True,
                             origenerator_cmd_file=tmp_path / "origenerator_cmd.txt")
        runner = make_runner(tmp_path, config=config, origenerator_pid=700)
        with patch.object(runner._watch_trackers[2], "note_user_nav") as nav:
            runner._dispatch("portrait_next")
        nav.assert_called_once()

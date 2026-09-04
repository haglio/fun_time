from __future__ import annotations

import contextlib
import logging
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from app_support.threading_utils import wait_until

from fun_time.bridge_records import BridgeConfig, WindowOp
from fun_time.media_metadata import normalize_path_key
from fun_time.role_windows import (
    MAIN_BLANK_SETTLE_S,
    ChildPids,
    WindowRoles,
)
from fun_time.shared_state import BridgeState, read_shared_state, write_shared_state
from fun_time.voice_commands import parse_command_line
from fun_time.watch_stats import load_watch_stats
from fun_time.windows_bridge_dispatch_loop import (
    DispatchLoopRunner,
    detect_sleep_gap,
    expand_both_command,
    poll_dashboard_commands,
    resolve_active_side_command,
)
from fun_time.windows_bridge_random_favs_browser import ChromeShortcut
from tests.role_window_fakes import (
    DASHBOARD_HWND,
    DASHBOARD_PID,
    GENAU_HWND,
    LANDSCAPE_HWND,
    LANDSCAPE_PID,
    NAU_HWND,
    NAU_PID,
    PORTRAIT_HWND,
    PORTRAIT_PID,
    RFB_HWND,
    TOPMOST_HWNDS,
    FakeClock,
    lookup_pid,
    lookup_title,
)


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
    with patch("fun_time.role_windows.is_window_topmost", return_value=False):
        yield


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
    """A runner over the imaginary desktop in ``tests.role_window_fakes``.

    The pids and the browser hwnd are the windows object's, not the runner's,
    so they are named here the way the tests have always named them and folded
    into one.  ``clock`` is that object's settle clock: a test that is about
    the beat a mode switch waits out hands in a :class:`FakeClock`.
    """
    windows = WindowRoles(
        pids=ChildPids(
            nau=kwargs.pop("nau_pid", NAU_PID),
            portrait=kwargs.pop("portrait_pid", PORTRAIT_PID),
            landscape=kwargs.pop("landscape_pid", LANDSCAPE_PID),
            dashboard=kwargs.pop("dashboard_pid", DASHBOARD_PID),
            origenerator=kwargs.pop("origenerator_pid", 0),
        ),
        rfb_hwnd=kwargs.pop("rfb_hwnd", 0),
        role_hwnds=kwargs.pop("role_hwnds", None),
        **({"clock": kwargs.pop("clock")} if "clock" in kwargs else {}),
    )
    settings = dict(dashboard_enabled=False)
    settings.update(kwargs)
    runner = DispatchLoopRunner(
        config=config or make_config(tmp_path),
        dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
        shared_state_file=tmp_path / "shared_state.ini",
        ahk_cmd_file=tmp_path / "ahk_cmd.txt",
        windows=windows,
        **settings,
    )
    # Park the periodic sync (z-order convergence + dashboard update) in the
    # far future so a tick in a test runs only what the test drove.  The one
    # test that is ABOUT the sync moves _last_sync back into the past itself.
    runner._last_sync = float("inf")
    return runner


def _wait_for_the_browse(mock_browse) -> None:
    """Hold the caller's patch until the browse thread has actually taken it.

    ``browse_library`` runs on a daemon thread the tick starts, and the press
    these tests assert on goes out *before* that thread does. So leaving the
    patch as soon as the press lands releases it under a thread that has not
    called yet, and the browse reaches the real library browser -- which opens
    a real window on the machine the family is used from. The fixed 0.15 s nap
    that used to sit here was covering exactly that, by hoping rather than by
    waiting for it.
    """
    wait_until(lambda: mock_browse.call_count >= 1, timeout=10.0)


@contextlib.contextmanager
def _press_channel(tmp_path):
    """A listening dashboard press socket, its port published where the
    runner looks for it.  Closes with the block, assertion failures included."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as recv_sock:
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.settimeout(1.0)
        (tmp_path / "dashboard_press_port.txt").write_text(
            str(recv_sock.getsockname()[1]), encoding="utf-8")
        yield recv_sock


def _presses_until(recv_sock, expected: str, *, timeout: float = 10.0) -> list[str]:
    """Every press read off ``recv_sock`` up to and including ``expected``.

    The press goes out on the tick's own thread, so what these tests are waiting
    for is a datagram, not a length of time. The socket's own short timeout paces
    the polling, and this returns the moment the expected press lands -- or
    everything that did arrive, so the assertion above can say what came instead.
    """
    received: list[str] = []

    def _arrived() -> bool:
        try:
            received.append(recv_sock.recvfrom(256)[0].decode("utf-8"))
        except OSError:
            pass
        return expected in received

    recv_sock.settimeout(0.02)
    try:
        wait_until(_arrived, timeout=timeout, interval=0)
    except TimeoutError:
        pass
    return received


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
        runner = make_runner(tmp_path)
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
        runner = make_runner(tmp_path)
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
        runner = make_runner(tmp_path)
        runner.state = BridgeState(active_side=2)
        (tmp_path / "dashboard_cmd.txt").write_text("active_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "portrait_next" in commands

    def test_a_spoken_command_carries_the_video_the_sampler_back_dates_it_to(self, tmp_path):
        """A phrase is only recognized once the speaker stops, so the video on
        screen when they started talking is the one they meant.  Which video
        that was is the sampler's timeline to answer; what the runner owes is
        putting its answer on the dispatch."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(active_side=2, locked2=False)
        (tmp_path / "dashboard_cmd.txt").write_text("portrait_lock_on @100.200", encoding="utf-8")

        with patch.object(runner.watch, "video_at", return_value="C:\\clips\\meant.mp4"), \
             patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        assert mock_dispatch.call_args[0][0] == "portrait_lock"
        assert mock_dispatch.call_args.kwargs["target_path"] == "C:\\clips\\meant.mp4"

    def test_the_tick_samples_every_player_on_the_watch_cadence(self, tmp_path):
        """Watch tracking rides the tick, and knows whether the room is paused —
        under OmniPause nothing is playing, so nothing is sampled."""
        runner = make_runner(tmp_path)

        with patch.object(runner.watch, "sample_due") as sample:
            runner.tick()
            assert sample.call_args.kwargs["paused"] is False

            runner.state = BridgeState(omni_paused=True)
            write_shared_state(tmp_path / "shared_state.ini", runner.state)
            runner.tick()
            assert sample.call_args.kwargs["paused"] is True

    def test_nudge_dispatches_to_command(self, tmp_path):
        """Nau owns the main player in every mode it appears, so a nudge
        dispatches to Nau's SEEK command (which stacks against its live clock)."""
        runner = make_runner(tmp_path)
        (tmp_path / "dashboard_cmd.txt").write_text("main_nudge_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "main_nudge_next" in commands

    def test_omnipause_enter_via_tick_drops_topmost_on_all_managed_windows(self, tmp_path):
        """Entering omnipause frees the desktop: EVERY managed window leaves the
        TOPMOST band — including Nau, which carries the topmost flag in video mode
        and would otherwise stay stranded above the desktop."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        (tmp_path / "dashboard_cmd.txt").write_text("omnipause_toggle", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert runner.state.omni_paused is True
        assert {h for h, v in topmost_calls if v is False} == TOPMOST_HWNDS | {NAU_HWND, GENAU_HWND}

    def test_omnipause_leave_via_tick_restores_topmost_and_refocuses_primary_player(
        self, tmp_path,
    ):
        """Leaving omnipause in video mode gives every managed window its TOPMOST
        bit back — Nau, which floats above the desktop again, and Genau, which
        shares Nau's rect and is promoted last, so putting it back in the band
        puts its HUD ABOVE Nau's video — and re-activates the window on top of
        the main player, which is Genau's.
        """
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=True)
        (tmp_path / "dashboard_cmd.txt").write_text("omnipause_toggle", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []
        activated: list[int] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.is_window_topmost", return_value=False), \
             patch("fun_time.role_windows.activate_window", side_effect=activated.append), \
             patch("fun_time.role_windows.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert runner.state.omni_paused is False
        assert {h for h, v in topmost_calls if v is True} == TOPMOST_HWNDS | {NAU_HWND, GENAU_HWND}
        assert activated == [GENAU_HWND]

    def test_omnipause_toggle_updates_state_and_writes_shared_state(self, tmp_path):
        runner = make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omnipause_toggle", encoding="utf-8")

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", return_value=0), \
             patch("fun_time.role_windows.set_always_on_top"):
            runner.tick()

        assert runner.state.omni_paused is True
        loaded = read_shared_state(tmp_path / "shared_state.ini")
        assert loaded is not None
        assert loaded.omni_paused is True

    def test_browse_library_sends_its_press_and_browses_with_the_room_playing(self, tmp_path):
        with _press_channel(tmp_path) as recv_sock:
            runner = make_runner(tmp_path, dashboard_enabled=True)
            runner.state = BridgeState(main_mode="video")
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("browse_library", encoding="utf-8")

            with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
                 patch("fun_time.role_windows.find_window_by_title", return_value=0), \
                 patch("fun_time.role_windows.set_always_on_top"), \
                 patch("fun_time.windows_bridge_dispatch_loop.browse_library",
                       return_value=None) as mock_browse:
                runner.tick()

                messages = _presses_until(recv_sock, "browse_library")
                _wait_for_the_browse(mock_browse)

        assert "browse_library" in messages
        # Browsing must NOT enter OmniPause: the old flow paused the whole
        # session for the browse and resumed only Nau, stranding the
        # satellites + voice frozen.  The browser opens with everything
        # still playing.
        assert runner.state.omni_paused is False

    def test_dispatch_forwards_remaining_ops_to_ahk(self, tmp_path):
        runner = make_runner(tmp_path)
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        suspend_op = WindowOp(op="suspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [suspend_op])
            runner._dispatch("some_command")

        assert ahk_cmd_file.read_text(encoding="utf-8") == "suspend_hotkeys"

    def test_dispatch_suppresses_unsuspend_during_integration(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        runner = make_runner(tmp_path)
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        unsuspend_op = WindowOp(op="unsuspend_hotkeys")
        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [unsuspend_op])
            runner._dispatch("some_command")

        assert not ahk_cmd_file.exists()

    def test_dispatch_allows_unsuspend_outside_integration(self, tmp_path):
        runner = make_runner(tmp_path)
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
        runner = make_runner(tmp_path)
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

        runner = make_runner(tmp_path)

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
        runner = make_runner(tmp_path)
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
        runner = make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("quit", encoding="utf-8")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        runner.tick()

        assert ahk_cmd_file.read_text(encoding="utf-8") == "exit"

    def test_omniminimize_minimizes_only_mode_visible_windows(self, tmp_path):
        """omniminimize minimizes the windows the current mode shows, without
        stealing focus — in video mode Genau's HUD among them.  (In genau mode
        the hidden slot-mate, Nau, is NOT minimized: SW_MINIMIZE would drag a
        hidden window back into view.)"""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omniminimize", encoding="utf-8")

        minimized: list[tuple[int, dict]] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.minimize_window", side_effect=lambda h, **kw: minimized.append((h, kw))):
            runner.tick()

        assert {h for h, _ in minimized} == {
            RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND, NAU_HWND, GENAU_HWND,
        }
        # Minimized without activation so focus isn't yanked between windows.
        assert all(kw.get("activate") is False for _, kw in minimized)

    def test_omniminimize_in_hybrid_includes_nau_and_genau(self, tmp_path):
        """Video mode shows Nau under Genau's HUD (Genau drives the OSR2)."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(main_mode="video")
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omniminimize", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()

        assert set(minimized) == {
            RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND,
            NAU_HWND, GENAU_HWND,
        }

    def test_omniminimize_skips_windows_that_are_not_found(self, tmp_path):
        """Windows whose lookup returns 0 are skipped — no minimize call for them."""
        runner = make_runner(tmp_path, rfb_hwnd=0)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("omniminimize", encoding="utf-8")

        minimized: list[int] = []
        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", return_value=0), \
             patch("fun_time.role_windows.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()

        assert minimized == []

    def test_mode_switch_leaves_the_outgoing_player_up_for_a_beat(self, tmp_path):
        """Minimizing freezes a window's Alt-Tab thumbnail — Windows stops
        compositing it — so the player being left has to be minimized only once
        the DISPLAY_OFF sent with the same switch is on screen.  Minimize in the
        frame or two that takes and the thumbnail keeps the video frame it was
        sitting on, which is the whole thing the blanking is for."""
        clock = FakeClock()
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND, clock=clock)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("genau_activate", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.activate_window"), \
             patch("fun_time.role_windows.restore_window"), \
             patch("fun_time.role_windows.set_always_on_top"), \
             patch("fun_time.role_windows.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()
            assert minimized == [], "Nau minimized before it could paint the black"

            # A tick inside the beat still leaves it up.
            clock.advance(MAIN_BLANK_SETTLE_S / 2)
            runner.tick()
            assert minimized == []

            # The settle elapses; the next tick parks it, without activation.
            clock.advance(MAIN_BLANK_SETTLE_S)
            runner.tick()
            assert minimized == [NAU_HWND]

            # And it is off the list: a later tick does not park it twice.
            clock.advance(MAIN_BLANK_SETTLE_S)
            runner.tick()

        assert minimized == [NAU_HWND]

    def test_switching_straight_back_never_minimizes_the_player(self, tmp_path):
        """A switch inside the settle window would otherwise minimize the very
        player it had just restored."""
        clock = FakeClock()
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND, clock=clock)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("genau_activate\nmain_video_activate", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.activate_window"), \
             patch("fun_time.role_windows.restore_window"), \
             patch("fun_time.role_windows.set_always_on_top"), \
             patch("fun_time.role_windows.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()
            clock.advance(MAIN_BLANK_SETTLE_S)
            runner.tick()

        assert NAU_HWND not in minimized, "Nau owns the display again"
        assert minimized == [], "and video mode parks nobody: both share the screen"

    def test_omnirestore_restores_exactly_the_minimized_windows(self, tmp_path):
        """omnirestore un-minimizes the windows omniminimize minimized — no
        more (a second omnirestore is a no-op), no less, never activating."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        cmd_file = tmp_path / "dashboard_cmd.txt"

        minimized_hwnds: list[int] = []
        restored: list[tuple[int, dict]] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.minimize_window",
                   side_effect=lambda h, **kw: minimized_hwnds.append(h)), \
             patch("fun_time.role_windows.restore_window", side_effect=lambda h, **kw: restored.append((h, kw))):
            cmd_file.write_text("omniminimize", encoding="utf-8")
            runner.tick()
            assert minimized_hwnds, "omniminimize put nothing down"

            cmd_file.write_text("omnirestore", encoding="utf-8")
            runner.tick()

            assert [h for h, _ in restored] == minimized_hwnds
            assert all(kw.get("activate") is False for _, kw in restored)

            # The minimized set was consumed: another omnirestore does nothing.
            cmd_file.write_text("omnirestore", encoding="utf-8")
            runner.tick()

        assert [h for h, _ in restored] == minimized_hwnds

    def test_a_huds_minimize_button_parks_only_that_player(self, tmp_path):
        """The satellites are borderless, so the HUD button is the only way to get
        one out of the way on its own.  It reaches exactly that window — the other
        players stay up — and never activates, so parking one does not hand the
        foreground to the next."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_minimize", encoding="utf-8")

        minimized: list[tuple[int, dict]] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.minimize_window", side_effect=lambda h, **kw: minimized.append((h, kw))):
            runner.tick()

        assert [h for h, _ in minimized] == [PORTRAIT_HWND]
        assert all(kw.get("activate") is False for _, kw in minimized)

    def test_a_huds_minimize_button_takes_effect_without_a_settle(self, tmp_path):
        """Unlike the main-slot swap, which waits out MAIN_BLANK_SETTLE_S so the
        outgoing player can present its black first, nothing here has been told to
        blank — so the window goes down in the same tick as the press."""
        clock = FakeClock()
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND, clock=clock)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("landscape_minimize", encoding="utf-8")

        minimized: list[int] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
            runner.tick()
            assert minimized == [LANDSCAPE_HWND]

            # Nothing was queued behind a settle either: letting one pass adds
            # no second minimize.
            clock.advance(MAIN_BLANK_SETTLE_S)
            runner.tick()
            assert minimized == [LANDSCAPE_HWND]

    def test_the_main_players_console_button_parks_the_window_holding_the_slot(self, tmp_path):
        """Nau and Genau share the main rect, so which window the console's button
        reaches is the mode's business: Genau in genau mode, and in video mode
        both, where Genau's HUD sits over Nau's video."""
        for mode, wanted in (("genau", [GENAU_HWND]), ("video", [NAU_HWND, GENAU_HWND])):
            runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
            # Through the shared state file, which every tick re-reads over
            # whatever the runner is holding.
            write_shared_state(tmp_path / "shared_state.ini", BridgeState(main_mode=mode))
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("main_minimize", encoding="utf-8")

            minimized: list[int] = []

            with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
                 patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
                 patch("fun_time.role_windows.minimize_window", side_effect=lambda h, **kw: minimized.append(h)):
                runner.tick()

            assert minimized == wanted, mode

    def test_leaving_omnipause_brings_back_every_window_a_button_parked(self, tmp_path):
        """A player parked from its own HUD took that HUD down with it, so it
        cannot ask to come back — resuming the room is what returns it, to the same
        rect, and the list is consumed so a second resume restores nothing."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))
        cmd_file = tmp_path / "dashboard_cmd.txt"

        restored: list[tuple[int, dict]] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.minimize_window"), \
             patch("fun_time.role_windows.activate_window"), \
             patch("fun_time.role_windows.restore_window", side_effect=lambda h, **kw: restored.append((h, kw))), \
             patch.object(runner.windows, "restore_all_topmost"):
            cmd_file.write_text("portrait_minimize\nlandscape_minimize", encoding="utf-8")
            runner.tick()
            assert restored == [], "parking a player restores nothing"

            cmd_file.write_text("omnipause_toggle", encoding="utf-8")
            runner.tick()

            assert [h for h, _ in restored] == [PORTRAIT_HWND, LANDSCAPE_HWND]
            assert all(kw.get("activate") is False for _, kw in restored)

            write_shared_state(tmp_path / "shared_state.ini", BridgeState(omni_paused=True))
            cmd_file.write_text("omnipause_toggle", encoding="utf-8")
            runner.tick()

        assert [h for h, _ in restored] == [PORTRAIT_HWND, LANDSCAPE_HWND]

    def test_resuming_leaves_the_mode_parked_slot_mate_where_it_is(self, tmp_path):
        """The idle main-slot player is minimized by the mode switch, not by a
        button, and the switch that brings its mode back is what restores it.
        Resuming must not drag it onto a rect the other player is using."""
        clock = FakeClock()
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND, clock=clock)
        cmd_file = tmp_path / "dashboard_cmd.txt"

        restored: list[int] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.minimize_window"), \
             patch("fun_time.role_windows.activate_window"), \
             patch("fun_time.role_windows.restore_window", side_effect=lambda h, **kw: restored.append(h)), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch.object(runner.windows, "restack_main_slot"):
            # A switch to genau parks Nau, which the settle then flushes.
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner.tick()
            clock.advance(MAIN_BLANK_SETTLE_S)
            runner.tick()
            restored.clear()

            write_shared_state(tmp_path / "shared_state.ini",
                               replace(runner.state, omni_paused=True))
            cmd_file.write_text("omnipause_toggle", encoding="utf-8")
            runner.tick()

        assert NAU_HWND not in restored

    def test_a_huds_minimize_button_says_nothing_to_ahk(self, tmp_path):
        """The op loop's fall-through writes an unrecognized op straight to the AHK
        command file, so a new op that is not handled would arrive there as a bogus
        verb rather than doing its job."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_minimize", encoding="utf-8")
        ahk_cmd_file = tmp_path / "ahk_cmd.txt"

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.minimize_window"):
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
        """The reference popup is a dashboard-UI concern: the loop echoes each
        command (toggle and close) as a press (the dashboard acts on it) and
        dispatches nothing — no player commands, no shared-state churn."""
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
        """The press channel is best-effort UI feedback: before the dashboard
        has published its port (or if it never does), the command itself must
        still dispatch rather than die with the echo."""
        runner = make_runner(tmp_path, dashboard_enabled=True)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_lock", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.return_value = (runner.state, [])
            runner.tick()

        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args[0][0] == "portrait_lock"

    def test_voice_off_mutes_voice_controller(self, tmp_path):
        from fun_time.voice_control import VoiceController

        runner = make_runner(tmp_path)
        vc = VoiceController(cmd_file=tmp_path / "vc_cmd.txt", model_path="unused")
        runner.voice_controller = vc
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("voice_off", encoding="utf-8")

        runner.tick()

        assert vc.is_muted

    def test_voice_toggle_unmutes_when_muted(self, tmp_path):
        from fun_time.voice_control import VoiceController

        runner = make_runner(tmp_path)
        vc = VoiceController(cmd_file=tmp_path / "vc_cmd.txt", model_path="unused")
        vc.mute()
        runner.voice_controller = vc
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("voice_toggle", encoding="utf-8")

        runner.tick()

        assert not vc.is_muted

    def test_voice_toggle_mutes_when_not_muted(self, tmp_path):
        from fun_time.voice_control import VoiceController

        runner = make_runner(tmp_path)
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

        runner = make_runner(tmp_path)
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

        runner = make_runner(tmp_path)
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
                        rfb_hwnd=12345,
            rfb_shortcut=ChromeShortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                args='--profile-directory="Profile 2"'),
        )

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
                "shortcut": ChromeShortcut(
                    target=r"C:\Chrome\chrome.exe",
                    work_dir=r"C:\Chrome",
                    args='--profile-directory="Profile 2"'),
            }),
        ]

    def test_rfb_window_is_activated_before_the_handoff(self, tmp_path):
        """Chrome gives the URL to the most recently activated window of the
        profile, so the activation has to happen before chrome.exe is launched —
        after it, the user's own window is still the one Chrome would pick."""
        runner = make_runner(
            tmp_path,
                        rfb_hwnd=777,
            rfb_shortcut=ChromeShortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                args='--profile-directory="Profile 2"'),
        )

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
                        rfb_hwnd=12345,
            rfb_shortcut=ChromeShortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                args='--profile-directory="Profile 2"'),
        )

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
                        rfb_hwnd=0,
            rfb_shortcut=ChromeShortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                args='--profile-directory="Profile 2"'),
        )

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
                        rfb_hwnd=12345,
        )

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
                        rfb_hwnd=12345,
            rfb_shortcut=ChromeShortcut(
                target=r"C:\Chrome\chrome.exe",
                work_dir=r"C:\Chrome",
                args='--profile-directory="Profile 2"'),
        )
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
                "shortcut": ChromeShortcut(
                    target=r"C:\Chrome\chrome.exe",
                    work_dir=r"C:\Chrome",
                    args='--profile-directory="Profile 2"'),
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
        clock = FakeClock()
        runner = make_runner(tmp_path, clock=clock)
        runner.state = BridgeState(main_mode=from_mode)

        calls: list[tuple[str, int]] = []
        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.restore_window",
                   side_effect=lambda h, **kw: calls.append(("show", h))), \
             patch("fun_time.role_windows.minimize_window",
                   side_effect=lambda h, **kw: calls.append(("hide", h))), \
             patch("fun_time.role_windows.activate_window",
                   side_effect=lambda h: calls.append(("activate", h))), \
             patch("fun_time.role_windows.set_always_on_top"):
            runner._dispatch(command)
            # The outgoing player's minimize is held back a beat, so it can paint
            # the black the same switch told it to before its Alt-Tab thumbnail
            # freezes (see WindowRoles.hide_after_settle).  Let that beat pass,
            # so these tests still see the whole ordered sequence.
            clock.advance(MAIN_BLANK_SETTLE_S)
            runner.windows.flush_pending_hides()

        assert runner.state.main_mode == {
            "genau_activate": "genau", "main_video_activate": "video",
        }[command]
        return calls

    def test_genau_activate_shows_genau_before_hiding_nau(self, tmp_path, monkeypatch):
        calls = self._run_mode_switch(
            tmp_path, monkeypatch, from_mode="video", command="genau_activate",
        )
        assert calls == [
            ("show", GENAU_HWND),
            ("activate", GENAU_HWND),
            ("hide", NAU_HWND),
        ]

    def test_main_video_activate_shows_nau_under_genaus_hud(self, tmp_path, monkeypatch):
        calls = self._run_mode_switch(
            tmp_path, monkeypatch, from_mode="genau", command="main_video_activate",
        )
        assert calls == [
            ("show", NAU_HWND),
            ("show", GENAU_HWND),
            ("activate", GENAU_HWND),
        ]

    def test_video_to_genau_hides_nau(self, tmp_path, monkeypatch):
        """Video mode and Genau differ only in Nau's visibility, so the transition
        must still swap windows.  Regression — a guard that compared
        genau_active() instead of the mode missed this pair."""
        calls = self._run_mode_switch(
            tmp_path, monkeypatch, from_mode="video", command="genau_activate",
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
            tmp_path, monkeypatch, from_mode="video", command="genau_activate",
            integration_env=True,
        )
        assert calls == [
            ("show", GENAU_HWND),
            ("hide", NAU_HWND),
        ]


class TestResolveRole:
    def test_cached_hwnd_survives_hiding_and_show_role_reaches_it(self, tmp_path):
        """Hidden windows are invisible to the pid/title lookups, so the
        HWND captured while a window was visible must be cached and reused —
        otherwise a hidden slot-mate could never be shown again."""
        runner = make_runner(tmp_path)

        # Nau is visible: the pid lookup finds it once, populating the cache.
        with patch("fun_time.role_windows.find_window_by_pid",
                   side_effect=lookup_pid):
            assert runner.windows.hwnd("nau") == NAU_HWND

        # Nau is now minimized: the pid/title lookups are mocked to fail, but
        # the cache still answers, and a show_role op reaches the cached hwnd
        # (show_role restores rather than SW_SHOWs — the idle player is parked
        # by minimizing it, so bringing it back is a restore).
        shown: list[int] = []
        show_op = WindowOp(op="show_role", key="nau")
        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", return_value=0), \
             patch("fun_time.role_windows.restore_window", side_effect=lambda h, **kw: shown.append(h)), \
             patch("fun_time.windows_bridge_dispatch_loop.dispatch_command",
                   return_value=(runner.state, [show_op])):
            assert runner.windows.hwnd("nau") == NAU_HWND
            runner._dispatch("main_video_activate")

        assert shown == [NAU_HWND]


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
             patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.set_always_on_top"), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        dispatched = [c[0][0] for c in mock_dispatch.call_args_list]
        assert "enter_omnipause" not in dispatched
        assert "omnipause_toggle" not in dispatched
        assert runner.state.omni_paused is False

    def test_removes_topmost_from_all_managed_windows(self, tmp_path):
        """The dialog needs a clear stage: every managed window drops out of the
        topmost band so it can't bury the dialog.  Nothing pauses — this is the
        only window state browsing touches."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.set_always_on_top",
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

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.windows_bridge_dispatch_loop.window_rect", return_value=(0, 400, 1080, 1520)), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None) as mock_browse:
            runner._handle_browse_library()

        mock_browse.assert_called_once_with(
            tmp_path / "launch.ini", r"C:\python.exe", over=(0, 400, 1080, 1520),
            runner=runner._run_browser,
        )

    def test_sends_selected_file_to_nau_by_default(self, tmp_path):
        """In video mode (the default) a selected file becomes a Nau PLAY_FILE
        command, paired with its mirrored funscript when one exists."""
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
             patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=str(video)):
            runner._handle_browse_library()

        command = runner.config.nau_cmd_file.read_text(encoding="utf-8")
        assert command == f"PLAY_FILE {video}\t{mirrored}\n"

    def test_sends_selected_file_to_nau_in_video_mode(self, tmp_path):
        """Video mode displays Nau, so a selected file becomes a Nau PLAY_FILE
        command there too (no funscript pairing when none exists)."""
        config = make_config(tmp_path, main_sources=r"C:\videos")
        runner = make_runner(tmp_path, config=config)
        runner.state = BridgeState(omni_paused=False, main_mode="video")

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=r"C:\videos\movie.mp4"):
            runner._handle_browse_library()

        assert runner.config.nau_cmd_file.read_text(
            encoding="utf-8") == "PLAY_FILE C:\\videos\\movie.mp4\n"

    def test_does_not_play_anything_on_cancel(self, tmp_path):
        """When the user cancels the dialog, nothing is sent to Nau."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        assert not runner.config.nau_cmd_file.exists()

    def test_restores_topmost_after_the_pick(self, tmp_path):
        """After the pick, every managed window gets its topmost band back —
        Nau and Genau's HUD over it included, so the video floats above the
        desktop again."""
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)

        topmost_calls = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.set_always_on_top",
                   side_effect=lambda hwnd, on_top: topmost_calls.append((hwnd, on_top))), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        restored = {h for h, v in topmost_calls if v}
        assert restored == TOPMOST_HWNDS | {NAU_HWND, GENAU_HWND}

    def test_never_restores_nau_topmost_even_in_genau_mode(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False, main_mode="genau")

        topmost_calls = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.set_always_on_top",
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

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", return_value=0), \
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

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None):
            runner._handle_browse_library()

        assert not runner.ahk_cmd_file.exists()

    def test_topmost_removed_before_the_browser_opens(self, tmp_path):
        """Topmost removal happens before the browser window goes up."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)

        call_log: list[str] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.set_always_on_top", side_effect=lambda h, v: call_log.append(f"topmost_{v}")), \
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

        with patch("fun_time.role_windows.find_window_by_pid", return_value=NAU_HWND), \
             patch("fun_time.windows_bridge_dispatch_loop.window_rect", return_value=(0, 0, 800, 600)), \
             patch("fun_time.role_windows.set_always_on_top") as mock_topmost, \
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

        with patch.object(runner.windows, "remove_all_topmost"), \
             patch.object(runner.windows, "restore_all_topmost"), \
             patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", return_value=0), \
             patch("fun_time.windows_bridge_dispatch_loop.browse_library", return_value=None) as mock_browse:
            runner._handle_browse_library()

        assert mock_browse.call_args.kwargs["over"] is None

    def test_a_second_browse_while_one_is_open_is_dropped(self, tmp_path):
        """The browser is the user's window, not the loop's: a second request
        while one is open would stack a second Chrome window and a second
        topmost drop/restore pair.  So while a browse holds the floor, another
        press is a no-op — dropped without blocking, not queued behind it."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)  # fast path — no bands to manage

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

            runner._handle_browse_library()  # the second press, while one is open

            let_the_browse_close.set()
            first.join(timeout=10.0)

        assert browses == ["open"]

    def test_browse_library_routed_from_tick(self, tmp_path):
        runner = make_runner(tmp_path)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("browse_library", encoding="utf-8")

        with patch.object(runner, "_handle_browse_library") as mock_handle:
            runner.tick()
            # The routing happens on a daemon thread the tick starts, so the
            # call is what there is to wait for -- and it is the assertion too.
            wait_until(lambda: mock_handle.call_count >= 1, timeout=10.0)

        mock_handle.assert_called_once()


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
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        with patch.object(runner, "_handle_omnipause_toggle") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("pause", encoding="utf-8")
            runner.tick()
        mock_toggle.assert_called_once()

    def test_pause_noop_when_already_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        with patch.object(runner, "_handle_omnipause_toggle") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("pause", encoding="utf-8")
            runner.tick()
        mock_toggle.assert_not_called()

    def test_play_leaves_omnipause_when_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=True)
        with patch.object(runner, "_handle_omnipause_toggle") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("play", encoding="utf-8")
            runner.tick()
        mock_toggle.assert_called_once()

    def test_play_noop_when_not_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        with patch.object(runner, "_handle_omnipause_toggle") as mock_toggle:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("play", encoding="utf-8")
            runner.tick()
        mock_toggle.assert_not_called()

    # -- enter_omnipause (Space hotkey) --

    def test_enter_omnipause_enters_when_not_paused(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("enter_omnipause", encoding="utf-8")

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", return_value=0), \
             patch("fun_time.role_windows.set_always_on_top"):
            runner.tick()

        assert runner.state.omni_paused is True

    def test_enter_omnipause_removes_topmost(self, tmp_path):
        runner = make_runner(tmp_path, rfb_hwnd=RFB_HWND)
        runner.state = BridgeState(omni_paused=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("enter_omnipause", encoding="utf-8")

        topmost_calls: list[tuple[int, bool]] = []

        with patch("fun_time.role_windows.find_window_by_pid", side_effect=lookup_pid), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=lookup_title), \
             patch("fun_time.role_windows.set_always_on_top",
                   side_effect=lambda h, v: topmost_calls.append((h, v))):
            runner.tick()

        assert {h for h, v in topmost_calls if v is False} == TOPMOST_HWNDS | {NAU_HWND, GENAU_HWND}

    def test_enter_omnipause_noop_when_already_paused(self, tmp_path):
        runner = make_runner(tmp_path)
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
        runner = make_runner(tmp_path)
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
        runner = make_runner(tmp_path)
        runner.state = BridgeState(omni_paused=False)
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("relief_omnipause", encoding="utf-8")

        with patch.object(runner, "_dispatch"), \
             patch.object(runner, "_log_topmost_state") as mock_log:
            runner.tick()

        mock_log.assert_called_once_with("post-enter")

    # -- lock portrait / lock landscape --

    def test_portrait_lock_on_dispatches_when_unlocked(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked2=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_on", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with("portrait_lock", None)

    def test_portrait_lock_on_noop_when_locked(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked2=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_on", encoding="utf-8")
            runner.tick()
        mock_d.assert_not_called()

    def test_landscape_lock_on_dispatches_when_unlocked(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked3=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("landscape_lock_on", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with("landscape_lock", None)

    def test_landscape_lock_on_noop_when_locked(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked3=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("landscape_lock_on", encoding="utf-8")
            runner.tick()
        mock_d.assert_not_called()

    # -- f mode, sided --

    def test_a_sided_fmode_reaches_the_dispatch_as_written(self, tmp_path):
        """The on/off forms are the dispatch's own commands now — it alone knows
        which players each names, and it is what decides a no-op rebuilds nothing —
        so the loop passes them straight through rather than second-guessing them."""
        runner = make_runner(tmp_path)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_fmode_on", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with("portrait_fmode_on", None)

    def test_both_fmode_is_expanded_into_the_two_satellites(self, tmp_path):
        """"both f mode" is sugar, exactly as it is for every other sided command:
        there is no combined handler, just the pair run in turn."""
        runner = make_runner(tmp_path)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("both_fmode", encoding="utf-8")
            runner.tick()
        assert [call.args[0] for call in mock_d.call_args_list] == [
            "portrait_fmode", "landscape_fmode",
        ]

    # -- genau activate --

    def test_genau_activate_dispatches_when_not_in_genau_mode(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="video")
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with("genau_activate", None)

    def test_genau_activate_dispatches_in_video_mode(self, tmp_path):
        """Video mode has the Robot Hand behind it but is NOT genau mode: the
        Genau-mode button must still switch to full Genau.  Regression — an old
        guard asked whether Genau was active, which video mode also was, so it
        swallowed this."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="video")
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with("genau_activate", None)

    def test_genau_activate_dispatches_when_already_in_genau_mode(self, tmp_path):
        """The loop forwards genau_activate unconditionally — switching to the
        mode you are already in is a no-op at the planner level (see
        test_mode_plan.test_same_mode_is_noop), not a special case here."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="genau")
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("genau_activate", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with("genau_activate", None)

    # -- lock off (idempotent unlock) --

    def test_portrait_lock_off_unlocks_when_locked(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked2=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_off", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with("portrait_lock", None)

    def test_portrait_lock_off_noop_when_already_unlocked(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked2=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("portrait_lock_off", encoding="utf-8")
            runner.tick()
        mock_d.assert_not_called()

    def test_landscape_lock_off_unlocks_when_locked(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked3=True)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("landscape_lock_off", encoding="utf-8")
            runner.tick()
        mock_d.assert_called_once_with("landscape_lock", None)

    def test_landscape_lock_off_noop_when_already_unlocked(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked3=False)
        with patch.object(runner, "_dispatch") as mock_d:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("landscape_lock_off", encoding="utf-8")
            runner.tick()
        mock_d.assert_not_called()

    # -- the dashboard snapshot's failure mode --

    def test_a_locked_dashboard_state_file_warns_once_a_minute_not_never(self, tmp_path, caplog):
        """_update_dashboard swallowed EVERYTHING silently — a genuine bug in
        the snapshot writer froze the dashboard forever, twice a second, with
        nothing in the log.  An OSError (the file locked by a reader) now warns,
        throttled so a stuck disk does not fill the log."""
        runner = make_runner(tmp_path, dashboard_enabled=True)
        with patch(
            "fun_time.windows_bridge_dispatch_loop.write_dashboard_snapshot",
            side_effect=OSError("locked"),
        ), caplog.at_level(logging.WARNING, logger="fun_time.windows_bridge_dispatch_loop"):
            runner._update_dashboard()
            runner._update_dashboard()
        warnings = [r for r in caplog.records if "dashboard" in r.message]
        assert len(warnings) == 1

    def test_a_bug_in_the_snapshot_writer_is_not_swallowed(self, tmp_path):
        """A TypeError is ours, not the disk's; the loop's outer per-tick
        exception log is where it belongs."""
        runner = make_runner(tmp_path, dashboard_enabled=True)
        with patch(
            "fun_time.windows_bridge_dispatch_loop.write_dashboard_snapshot",
            side_effect=TypeError("renamed keyword"),
        ), pytest.raises(TypeError):
            runner._update_dashboard()

    # -- the window-op vocabulary --

    def test_an_unknown_op_is_an_error_not_an_ahk_verb(self, tmp_path, caplog):
        """The interpreter's fall-through used to write any unknown op verbatim
        into ahk_cmd.txt, where AHK ignored it — a misspelled op was a silent
        no-op.  Now only the two hotkey-suspension verbs pass through, and
        anything else is an error in the log."""
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
        """Every Op member has a handler, so a new op without one is caught by
        this (and by the import-time assert beside the table) instead of at the
        first press."""
        from fun_time.bridge_records import Op
        from fun_time.windows_bridge_dispatch_loop import _AHK_PASSTHROUGH_OPS, _OP_HANDLERS

        assert set(_OP_HANDLERS) == set(Op)
        assert {Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS} == _AHK_PASSTHROUGH_OPS

    # -- clipper save --

    def test_save_clip_runs_on_a_worker_thread_and_flashes_the_result(self, tmp_path, caplog):
        """The save boots a sibling repo's interpreter (up to its 10 s timeout),
        so the tick hands it to a thread and the toast lands when it does —
        the one command whose confirmation trails the keypress."""
        runner = make_runner(tmp_path)
        with patch(
            "fun_time.windows_bridge_dispatch_loop.save_clip_session",
            return_value="Clipper: fabricated-session",
        ) as mock_save, caplog.at_level(
            logging.INFO, logger="fun_time.windows_bridge_dispatch_loop"
        ):
            (tmp_path / "dashboard_cmd.txt").write_text("clipper_save", encoding="utf-8")
            runner.tick()
            wait_until(lambda: mock_save.call_count >= 1, timeout=10.0)
            wait_until(
                lambda: any(r.message == "Clipper: fabricated-session" for r in caplog.records),
                timeout=10.0,
            )
        mock_save.assert_called_once_with(runner.config)
        flashed = [r for r in caplog.records if r.message == "Clipper: fabricated-session"]
        assert flashed[0].source == "main"
        # The op is handled, never mistaken for an AHK verb (the else-branch
        # writes unknown ops verbatim into ahk_cmd.txt).
        assert not runner.ahk_cmd_file.exists()

    def test_save_clip_failure_flashes_nothing(self, tmp_path, caplog):
        """save_clip_session answers "" on failure and has already logged why;
        an empty toast would flash an empty box."""
        runner = make_runner(tmp_path)
        with patch(
            "fun_time.windows_bridge_dispatch_loop.save_clip_session", return_value=""
        ) as mock_save, caplog.at_level(
            logging.INFO, logger="fun_time.windows_bridge_dispatch_loop"
        ):
            (tmp_path / "dashboard_cmd.txt").write_text("clipper_save", encoding="utf-8")
            runner.tick()
            wait_until(lambda: mock_save.call_count >= 1, timeout=10.0)
        assert all(not getattr(r, "source", "") == "main" or "Clipper" not in r.message
                   for r in caplog.records)

    # -- broker start / broker stop --

    def test_broker_start_starts_when_not_running(self, tmp_path):
        runner = make_runner(tmp_path)
        # No heartbeat file → broker not running
        with patch("fun_time.windows_bridge_dispatch_loop.launch_broker_tray") as mock_launch:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_start", encoding="utf-8")
            runner.tick()
            wait_until(lambda: mock_launch.call_count >= 1, timeout=10.0)
        mock_launch.assert_called_once_with(runner.config.broker_tray_launcher)

    def test_broker_start_noop_when_already_running(self, tmp_path):
        runner = make_runner(tmp_path)
        # Fresh heartbeat → broker running
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")
        with patch("fun_time.windows_bridge_dispatch_loop.launch_broker_tray") as mock_launch:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_start", encoding="utf-8")
            runner.tick()
        mock_launch.assert_not_called()

    def test_broker_start_never_kills_the_broker(self, tmp_path):
        """A stale heartbeat does not mean no broker. osr2_broker only ticks the
        heartbeat while it holds the serial port, so a broker that cannot reach a
        powered-off OSR2 reads as dead while it is very much alive -- and killing
        it drops harem and the user's own MFP session with it. Starting is a
        start; only an explicit stop may kill."""
        runner = make_runner(tmp_path)
        # No heartbeat file at all: the broker reads as dead.
        with patch("fun_time.windows_bridge_startup.stop_broker_processes") as mock_stop, \
             patch("fun_time.windows_bridge_dispatch_loop.launch_broker_tray") as mock_launch:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_start", encoding="utf-8")
            runner.tick()
            # "Never kills" is an absence, and an absence cannot be waited for.
            # The start it does take is the event that says the thread got
            # there, so wait for that and read the absence beside it.
            wait_until(lambda: mock_launch.call_count >= 1, timeout=10.0)
        mock_stop.assert_not_called()

    def test_broker_panel_toggle_starts_without_killing(self, tmp_path):
        """The B panel toggles the broker.  Toggling one that reads as dead has
        to start it, not restart it — the same stale-heartbeat trap as
        broker_start, and the same live broker on the other side of it."""
        runner = make_runner(tmp_path)
        # No heartbeat file: the toggle takes its "not running, so start" arm.
        with patch("fun_time.windows_bridge_startup.stop_broker_processes") as mock_stop, \
             patch("fun_time.windows_bridge_dispatch_loop.launch_broker_tray") as mock_launch:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_panel", encoding="utf-8")
            runner.tick()
            wait_until(lambda: mock_launch.call_count >= 1, timeout=10.0)
        mock_stop.assert_not_called()

    def test_broker_stop_stops_when_running(self, tmp_path):
        runner = make_runner(tmp_path)
        (tmp_path / "broker_heartbeat.txt").write_text(str(time.time()), encoding="utf-8")
        with patch("fun_time.windows_bridge_dispatch_loop.stop_broker_processes") as mock_stop:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_stop", encoding="utf-8")
            runner.tick()
            wait_until(lambda: mock_stop.call_count >= 1, timeout=10.0)
        mock_stop.assert_called_once()

    def test_broker_stop_noop_when_not_running(self, tmp_path):
        runner = make_runner(tmp_path)
        # No heartbeat file → broker not running
        with patch("fun_time.windows_bridge_dispatch_loop.stop_broker_processes") as mock_stop:
            cmd_file = tmp_path / "dashboard_cmd.txt"
            cmd_file.write_text("broker_stop", encoding="utf-8")
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
        """A genau session's startup parks the idle main-slot window (Nau)
        BEFORE the dispatch loop ever resolves it; with the pid/title lookups
        mocked to fail, the runner must answer from the hwnds the startup
        sequencer seeded while everything was visible, or video mode could
        never bring Nau back."""
        runner = make_runner(
            tmp_path,
            role_hwnds={"genau": 6001, "nau": 2001},
        )
        runner.state = BridgeState(main_mode="genau")
        shown: list[int] = []

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0),              patch("fun_time.role_windows.find_window_by_title", return_value=0),              patch("fun_time.role_windows.restore_window", side_effect=lambda h, **kw: shown.append(h)):
            assert runner.windows.hwnd("genau") == 6001
            assert runner.windows.hwnd("nau") == 2001
            runner._dispatch("main_video_activate")

        assert shown == [2001, 6001]  # video mode shows Nau then the Genau HUD


class TestVideoModeFunscriptHandoff:
    """The arbitration itself is the driver's (see tests/test_device_arbiter.py);
    what the runner owes is running it each tick against the current modes."""

    def test_the_tick_arbitrates_for_the_mode_the_session_is_in(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="video", omni_paused=True)

        with patch.object(runner.arbiter, "sync") as sync:
            runner.tick()

        sync.assert_called_once_with("video", paused=True)


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
        runner = make_runner(tmp_path)
        (tmp_path / "dashboard_cmd.txt").write_text("both_next", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.side_effect = lambda cmd, state, config, target_path="": (state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert commands == ["portrait_next", "landscape_next"]

    def test_lock_both_locks_each_unlocked_satellite(self, tmp_path):
        """"lock both" (both_lock_on) reuses the idempotent per-satellite lock:
        an already-locked side is left alone, so it only toggles the unlocked one."""
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked2=True, locked3=False)
        (tmp_path / "dashboard_cmd.txt").write_text("both_lock_on", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.side_effect = lambda cmd, state, config, target_path="": (state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert commands == ["landscape_lock"]  # portrait already locked → skipped

    def test_unlock_both_unlocks_each_locked_satellite(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(locked2=True, locked3=True)
        (tmp_path / "dashboard_cmd.txt").write_text("both_lock_off", encoding="utf-8")

        with patch("fun_time.windows_bridge_dispatch_loop.dispatch_command") as mock_dispatch:
            mock_dispatch.side_effect = lambda cmd, state, config, target_path="": (state, [])
            runner.tick()

        commands = [c[0][0] for c in mock_dispatch.call_args_list]
        assert commands == ["portrait_lock", "landscape_lock"]


class TestHudPublishing:
    """The panels themselves are the feed's (see tests/test_hud_feed.py); what
    the runner owes is putting the current state on the feed's cadence."""

    def test_the_tick_feeds_the_huds_the_state_it_is_holding(self, tmp_path):
        runner = make_runner(tmp_path)
        runner.state = BridgeState(main_mode="video", locked2=True)

        with patch.object(runner.hud, "publish_due") as publish:
            runner.tick()

        assert publish.call_args[0][0] == runner.state
        assert "now" in publish.call_args.kwargs


class TestBrowserOutlivesNothing:
    """The browser is a child of the session, so quitting has to take it.

    It is launched mid-session rather than at startup, so it cannot join the
    _CHILD_GROUPS teardown list the way the players and companions do; the
    dispatch loop owns it instead, and its stop is where the browse dies.
    """

    def test_quitting_the_session_kills_a_browser_still_open(self, tmp_path):
        runner = make_runner(tmp_path)
        opened = threading.Event()
        terminated = threading.Event()
        process = Mock()
        # A browser stays up until something ends it, which is the shape this
        # test is about -- so the fake's wait() returns when terminate() is
        # called rather than after 0.4 s of standing in for a browse. If the
        # stop ever failed to terminate, the old fake released the thread
        # anyway and the test could still pass; this one cannot.
        process.wait.side_effect = lambda: (opened.set(), terminated.wait(10.0))
        process.terminate.side_effect = terminated.set

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
        runner.rfb_shortcut = ChromeShortcut(target="chrome.exe", work_dir="", args="")
        runner._pending_rfb_urls = ["file:///tab.html"]
        runner._flush_rfb_tabs()
        assert runner._pending_rfb_urls == ["file:///tab.html"]  # held, not dropped


class TestOrigeneratorWindowConverger:
    """The convergence itself is the windows object's (see
    tests/test_role_windows.py); what the runner owns is when it runs."""

    def test_omnipause_leaves_the_hosted_window_exactly_where_it_is(self, tmp_path):
        """OmniPause's window state is its own — every band is down and the
        room is stopped — so a converger firing inside it would restore or
        park the hosted app against what the pause just did."""
        runner = make_runner(tmp_path, origenerator_pid=700)
        runner.state = replace(runner.state, satellites_mode="origenerator",
                               omni_paused=True)

        with patch.object(runner.windows, "converge_origenerator_window") as converge:
            runner._converge_origenerator_window()

        converge.assert_not_called()

    def test_outside_omnipause_the_windows_object_is_asked_for_these_modes(self, tmp_path):
        runner = make_runner(tmp_path, origenerator_pid=700)
        runner.state = replace(runner.state, main_mode="video",
                               satellites_mode="origenerator")

        with patch.object(runner.windows, "converge_origenerator_window") as converge:
            runner._converge_origenerator_window()

        converge.assert_called_once_with("video", "origenerator")


class TestOrigeneratorWatchGuard:
    def test_a_routed_step_books_nothing_against_the_blacked_player(self, tmp_path):
        config = make_config(tmp_path, origenerator_enabled=True,
                             origenerator_cmd_file=tmp_path / "origenerator_cmd.txt")
        runner = make_runner(tmp_path, config=config, origenerator_pid=700)
        runner.state = replace(runner.state, satellites_mode="origenerator")
        with patch.object(runner.watch, "note_command") as note:
            runner._dispatch("portrait_next")
        note.assert_not_called()

    def test_a_player_mode_step_still_books_the_player(self, tmp_path):
        config = make_config(tmp_path, origenerator_enabled=True,
                             origenerator_cmd_file=tmp_path / "origenerator_cmd.txt")
        runner = make_runner(tmp_path, config=config, origenerator_pid=700)
        with patch.object(runner.watch, "note_command") as note:
            runner._dispatch("portrait_next")
        note.assert_called_once_with("portrait_next")

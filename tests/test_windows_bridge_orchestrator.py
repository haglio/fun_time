from __future__ import annotations

import configparser
from dataclasses import replace
from pathlib import Path
from unittest.mock import call, patch, MagicMock, call

import threading

import pytest

from fun_time import windows_bridge_orchestrator
from fun_time.config import load_config
from fun_time.manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.windows_bridge_orchestrator import (
    HUD_PRIME_TIMEOUT_S,
    POST_LOADING_RESOLVE_TIMEOUT_S,
    SETTLE_PASSES,
    SETTLE_WAIT_S,
    _close_origenerator_gracefully,
    ChildProcess,
    _CHILD_PID_KEYS,
    _fix_post_loading_windows,
    _log_window_obstruction,
    _open_event_log,
    _shutdown_children,
    identify_children,
    kill_process_tree,
    kill_recorded_child,
    write_pids_file,
    run_python_orchestrated_bridge,
)
from fun_time.win32 import StackedWindow
from fun_time.loading_screen import STALE_TIMEOUT_S
from fun_time.windows_bridge_dispatch_loop import BridgeState
from fun_time.windows_bridge_sequencer import StartupResult
from fun_time.window_layout import WindowLayoutPlan, WindowRect
from fun_time.overlay_progress import (
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    NullProgress,
    PhaseProgress,
    StartupCancelled,
    cancel_file_for,
    ready_file_for,
)


def _fake_plan() -> WindowLayoutPlan:
    r = WindowRect(0, 0, 100, 100)
    return WindowLayoutPlan(
        portrait=r, landscape=r,
        dashboard=r, random_favs_browser=r,
    )


def _fake_startup_result() -> StartupResult:
    return StartupResult(
        nau_pid=200,
        portrait_pid=300,
        landscape_pid=400,
        dashboard_pid=500,
        genau_pid=600,
        audio_pid=700,
        origenerator_pid=800,
        layout_plan=_fake_plan(),
    )


class TestFixPostLoadingWindows:
    """The overlay's teardown can shuffle z-order and activation, so the whole
    window policy is applied again once the overlay process has exited."""

    def test_reapplies_the_policy_for_the_mode_the_session_opened_in(self):
        """A resumed genau session would otherwise get nau's stacking back here:
        Nau promoted over Genau and un-parked, one pass after the sequencer
        parked it — the display handed back to the player that is not playing."""
        result = replace(_fake_startup_result(), main_mode="genau")

        with patch(
            "fun_time.windows_bridge_orchestrator._apply_startup_window_state"
        ) as apply, patch(
            "fun_time.windows_bridge_orchestrator.find_window_by_pid", return_value=0
        ), patch(
            "fun_time.windows_bridge_orchestrator.wait_for_window_by_title", return_value=0
        ), patch("fun_time.windows_bridge_orchestrator._log_window_obstruction"):
            _fix_post_loading_windows(result)

        assert apply.call_args.kwargs["mode"] == "genau"

    def test_satellites_resolve_by_title_when_their_pids_are_launcher_shims(self):
        """python_exe is the venv's pythonw SHIM: the recorded satellite pid is
        the launcher's, not the interpreter that owns the SDL window, so the
        by-pid lookup finds nothing.  This pass was the only banding the
        satellites got on a loading-screen startup, and with hwnd 0 it silently
        skipped them — every session opened with both players out of the
        topmost band, buried by the first window raised over their rects."""
        result = _fake_startup_result()
        titles = {"Portrait AI Player": 111, "Landscape AI Player": 222}

        with patch(
            "fun_time.windows_bridge_orchestrator._apply_startup_window_state"
        ) as apply, patch(
            "fun_time.windows_bridge_orchestrator.find_window_by_pid", return_value=0
        ), patch(
            "fun_time.windows_bridge_orchestrator.wait_for_window_by_title",
            side_effect=lambda title, **kwargs: titles.get(title, 0),
        ), patch("fun_time.windows_bridge_orchestrator._log_window_obstruction"):
            _fix_post_loading_windows(result)

        assert apply.call_args.kwargs["portrait_hwnd"] == 111
        assert apply.call_args.kwargs["landscape_hwnd"] == 222

    def test_a_buried_satellite_is_re_promoted_until_frontmost(self):
        """The banding waits on each window's own thread, and the satellites
        are at their busiest exactly at the reveal — a promotion that times
        out through the hung-window guard leaves the player under whatever
        the user had on that monitor (a maximized Chrome sat over the
        landscape player until the next full re-band).  The pass now walks
        the real z-order afterwards and re-promotes whoever is still buried."""
        result = _fake_startup_result()
        titles = {"Portrait AI Player": 111, "Landscape AI Player": 222}
        chrome = StackedWindow(hwnd=9, title="jazz - Chrome", topmost=False,
                               rect=(0, 0, 2560, 1410))
        buried = [chrome,
                  StackedWindow(hwnd=222, title="Landscape AI Player",
                                topmost=True, rect=(854, 0, 1706, 1410)),
                  StackedWindow(hwnd=111, title="Portrait AI Player",
                                topmost=True, rect=(2560, 0, 1440, 2560))]
        risen = [buried[1], chrome, buried[2]]  # landscape above Chrome now

        with patch(
            "fun_time.windows_bridge_orchestrator._apply_startup_window_state"
        ), patch(
            "fun_time.windows_bridge_orchestrator.find_window_by_pid", return_value=0
        ), patch(
            "fun_time.windows_bridge_orchestrator.wait_for_window_by_title",
            side_effect=lambda title, **kwargs: titles.get(title, 0),
        ), patch(
            "fun_time.windows_bridge_orchestrator.iter_zorder",
            side_effect=[buried, risen],
        ), patch(
            "fun_time.windows_bridge_orchestrator.set_always_on_top"
        ) as promote, patch(
            "fun_time.windows_bridge_orchestrator.time.sleep"
        ), patch("fun_time.windows_bridge_orchestrator._log_window_obstruction"):
            _fix_post_loading_windows(result)

        promote.assert_called_once_with(222, True)  # only the buried one, once

    def test_the_curtain_goes_back_on_top_after_the_bands_are_applied(self):
        """Behind the overlay is where this pass belongs — the bands are what
        decides what the reveal looks like — and every promotion it makes
        inserts ABOVE the overlay (HWND_TOPMOST inserts at the top of the
        band).  So the overlay is put back on top after the pass, or the room
        it is hiding shows through the moment it is banded."""
        result = _fake_startup_result()

        with patch(
            "fun_time.windows_bridge_orchestrator._apply_startup_window_state"
        ), patch(
            "fun_time.windows_bridge_orchestrator.find_window_by_pid", return_value=0
        ), patch(
            "fun_time.windows_bridge_orchestrator.wait_for_window_by_title", return_value=0
        ), patch(
            "fun_time.windows_bridge_orchestrator.iter_zorder", return_value=[]
        ), patch(
            # The cover goes back through the sequencer's keep_the_cover_up,
            # which both ends of startup share; a player is promoted through
            # this module's own name.
            "fun_time.windows_bridge_sequencer.set_always_on_top"
        ) as cover_back, patch(
            "fun_time.windows_bridge_orchestrator.set_always_on_top"
        ), patch("fun_time.windows_bridge_orchestrator._log_window_obstruction"):
            _fix_post_loading_windows(result, overlay_hwnd=77)

        cover_back.assert_called_once_with(77, True)

    def test_the_curtain_is_not_a_burial(self):
        """The overlay covers both players by design, so counting it as a
        covering window would spend every pass re-promoting players that are
        exactly where they belong — and each promotion would put one over the
        curtain."""
        result = _fake_startup_result()
        titles = {"Portrait AI Player": 111, "Landscape AI Player": 222}
        curtain = StackedWindow(hwnd=77, title="Fun Time Loading", topmost=True,
                                rect=(0, 0, 4000, 2560))
        stack = [curtain,
                 StackedWindow(hwnd=222, title="Landscape AI Player",
                               topmost=True, rect=(854, 0, 1706, 1410)),
                 StackedWindow(hwnd=111, title="Portrait AI Player",
                               topmost=True, rect=(2560, 0, 1440, 2560))]

        with patch(
            "fun_time.windows_bridge_orchestrator._apply_startup_window_state"
        ), patch(
            "fun_time.windows_bridge_orchestrator.find_window_by_pid", return_value=0
        ), patch(
            "fun_time.windows_bridge_orchestrator.wait_for_window_by_title",
            side_effect=lambda title, **kwargs: titles.get(title, 0),
        ), patch(
            "fun_time.windows_bridge_orchestrator.iter_zorder", return_value=stack,
        ), patch(
            "fun_time.windows_bridge_sequencer.set_always_on_top"
        ) as cover_back, patch(
            "fun_time.windows_bridge_orchestrator.set_always_on_top"
        ) as promote, patch(
            "fun_time.windows_bridge_orchestrator.time.sleep"
        ) as slept, patch("fun_time.windows_bridge_orchestrator._log_window_obstruction"):
            _fix_post_loading_windows(result, overlay_hwnd=77)

        # The curtain put back, and nothing else: neither player is buried.
        assert cover_back.call_args_list == [call(77, True)]
        promote.assert_not_called()
        slept.assert_not_called()

    def test_origenerator_mode_bands_and_settles_the_shows_over_the_players(self):
        """In origenerator mode the players are blacked and held for the whole
        mode and the hosted app's region shows cover them on purpose.

        So the shows are what this pass has to band (as managed roles promoted
        after the players) and what it has to settle: pointed at the players,
        the settle loop reads a show covering its player as a burial and
        re-promotes the blacked player over it, once every pass for twelve
        seconds — which on a session that opened in the mode is one picture and
        then a black rectangle."""
        result = replace(_fake_startup_result(), satellites_mode="origenerator")
        by_title = {"Portrait AI Player": 111, "Landscape AI Player": 222}
        for_process = {"Origenerator": 800, "Origenerator Portrait": 801,
                       "Origenerator Landscape": 802}
        promoted = []

        with patch(
            "fun_time.windows_bridge_orchestrator._apply_startup_window_state"
        ) as apply, patch(
            "fun_time.windows_bridge_orchestrator.find_window_by_pid", return_value=0
        ), patch(
            "fun_time.windows_bridge_orchestrator.wait_for_window_by_title",
            side_effect=lambda title, **kwargs: by_title.get(title, 0),
        ), patch(
            "fun_time.windows_bridge_orchestrator.find_window_for_process",
            side_effect=lambda _pid, title: for_process.get(title, 0),
        ), patch(
            "fun_time.windows_bridge_orchestrator.iter_zorder", return_value=[]
        ), patch(
            "fun_time.windows_bridge_orchestrator.windows_obscuring",
            side_effect=lambda hwnd, _stack: [],
        ), patch(
            "fun_time.windows_bridge_orchestrator.set_always_on_top",
            side_effect=lambda hwnd, on: promoted.append(hwnd),
        ), patch(
            "fun_time.windows_bridge_orchestrator._log_window_obstruction"
        ) as obstruction:
            _fix_post_loading_windows(result)

        assert apply.call_args.kwargs["origenerator_portrait_hwnd"] == 801
        assert apply.call_args.kwargs["origenerator_landscape_hwnd"] == 802
        # And the burial test asks about the shows, never the players under them.
        watched = [call.args[1] for call in obstruction.call_args_list]
        assert 801 in watched and 802 in watched
        assert 111 not in watched and 222 not in watched

    def test_player_mode_still_settles_the_players_themselves(self):
        """Nothing covers a player when no show is hosted over it, so the pass
        is unchanged there — and a session with no Origenerator at all resolves
        no show windows to settle."""
        result = _fake_startup_result()
        by_title = {"Portrait AI Player": 111, "Landscape AI Player": 222}

        with patch(
            "fun_time.windows_bridge_orchestrator._apply_startup_window_state"
        ) as apply, patch(
            "fun_time.windows_bridge_orchestrator.find_window_by_pid", return_value=0
        ), patch(
            "fun_time.windows_bridge_orchestrator.wait_for_window_by_title",
            side_effect=lambda title, **kwargs: by_title.get(title, 0),
        ), patch(
            "fun_time.windows_bridge_orchestrator.iter_zorder", return_value=[]
        ), patch(
            "fun_time.windows_bridge_orchestrator.set_always_on_top"
        ), patch(
            "fun_time.windows_bridge_orchestrator._log_window_obstruction"
        ) as obstruction:
            _fix_post_loading_windows(result)

        assert apply.call_args.kwargs["origenerator_portrait_hwnd"] == 0
        watched = [call.args[1] for call in obstruction.call_args_list]
        assert 111 in watched and 222 in watched

    def test_it_hands_back_the_windows_it_resolved(self):
        """The reveal re-asserts the bands once the overlay is gone, and does
        it on these rather than resolving every window a second time."""
        result = _fake_startup_result()
        titles = {"Portrait AI Player": 111, "Landscape AI Player": 222}

        with patch(
            "fun_time.windows_bridge_orchestrator._apply_startup_window_state",
            return_value={"portrait": 111, "landscape": 222},
        ), patch(
            "fun_time.windows_bridge_orchestrator.find_window_by_pid", return_value=0
        ), patch(
            "fun_time.windows_bridge_orchestrator.wait_for_window_by_title",
            side_effect=lambda title, **kwargs: titles.get(title, 0),
        ), patch(
            "fun_time.windows_bridge_orchestrator.iter_zorder", return_value=[]
        ), patch(
            "fun_time.windows_bridge_orchestrator.set_always_on_top"
        ), patch("fun_time.windows_bridge_orchestrator._log_window_obstruction"):
            resolved = _fix_post_loading_windows(result)

        assert resolved == {"portrait": 111, "landscape": 222}
    def test_taskkills_the_pid_and_its_descendants(self):
        with patch("fun_time.windows_bridge_orchestrator.subprocess.run") as mock_run:
            kill_process_tree(1234)

        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["taskkill", "/PID", "1234", "/T", "/F"]

    def test_ignores_the_zero_pid_of_a_child_that_was_never_launched(self):
        with patch("fun_time.windows_bridge_orchestrator.subprocess.run") as mock_run:
            kill_process_tree(0)

        mock_run.assert_not_called()


class TestKillRecordedChild:
    def test_kills_the_child_whose_pid_still_names_it(self):
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            return_value=111_000,
        ), patch("fun_time.windows_bridge_orchestrator.kill_process_tree") as mock_kill:
            kill_recorded_child(ChildProcess(pid=1234, created_at=111_000))

        mock_kill.assert_called_once_with(1234)

    def test_does_not_kill_a_pid_windows_recycled_to_another_process(self, caplog):
        """The recorded child died and Windows handed its PID to something else —
        an integration run's pytest, say.  Killing it would take that process down."""
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            return_value=222_000,
        ), patch("fun_time.windows_bridge_orchestrator.kill_process_tree") as mock_kill, \
             caplog.at_level("WARNING", logger="fun_time.windows_bridge_orchestrator"):
            kill_recorded_child(ChildProcess(pid=1234, created_at=111_000))

        mock_kill.assert_not_called()
        assert "1234" in caplog.text

    def test_skips_an_already_exited_child_without_a_recycle_warning(self, caplog):
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            return_value=None,
        ), patch("fun_time.windows_bridge_orchestrator.kill_process_tree") as mock_kill, \
             caplog.at_level("INFO", logger="fun_time.windows_bridge_orchestrator"):
            kill_recorded_child(ChildProcess(pid=1234, created_at=111_000))

        mock_kill.assert_not_called()
        assert not [r for r in caplog.records if r.levelno >= 30]  # no WARNING
        assert "1234" in caplog.text


class TestIdentifyChildren:
    def test_pins_every_launched_pid_to_its_creation_time(self):
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            side_effect=lambda pid: pid * 10,
        ):
            children = identify_children(_fake_startup_result())

        assert children["nau_pid"] == ChildProcess(pid=200, created_at=2000)
        assert children["audio_pid"] == ChildProcess(pid=700, created_at=7000)

    def test_records_a_child_that_already_exited_as_unkillable(self):
        """A PID whose creation time cannot be read is already gone; recording
        0 means no later creation time can ever match it."""
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            return_value=None,
        ):
            children = identify_children(_fake_startup_result())

        assert children["nau_pid"] == ChildProcess(pid=200, created_at=0)


def _recorded_children(**overrides: ChildProcess) -> dict[str, ChildProcess]:
    """Every child a session records, as teardown expects to be handed them.

    Unnamed ones stand in as pid 0 — the never-launched child, which nothing
    kills."""
    children = {key: ChildProcess(pid=0, created_at=0) for key in _CHILD_PID_KEYS}
    children.update(overrides)
    return children


class TestShutdownChildren:
    def test_closes_rfb_window(self):
        with patch("fun_time.windows_bridge_orchestrator.kill_recorded_child"), \
             patch("fun_time.windows_bridge_orchestrator.close_window") as mock_close:
            _shutdown_children(88888, _recorded_children(), NullProgress())

        mock_close.assert_called_once_with(88888)

    def test_skips_rfb_close_when_no_hwnd(self):
        with patch("fun_time.windows_bridge_orchestrator.kill_recorded_child"), \
             patch("fun_time.windows_bridge_orchestrator.close_window") as mock_close:
            _shutdown_children(0, _recorded_children(), NullProgress())

        mock_close.assert_called_once_with(0)

    def test_kills_the_recorded_children_but_never_a_recycled_pid(self):
        children = _recorded_children(
            nau_pid=ChildProcess(pid=200, created_at=111),
            portrait_pid=ChildProcess(pid=300, created_at=222),
        )
        live_creation_times = {200: 111, 300: 999}  # 300 was recycled
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            side_effect=live_creation_times.get,
        ), patch("fun_time.windows_bridge_orchestrator.kill_process_tree") as mock_kill, \
             patch("fun_time.windows_bridge_orchestrator.close_window"):
            _shutdown_children(0, children, NullProgress())

        mock_kill.assert_called_once_with(200)

    def test_every_recorded_child_belongs_to_a_reported_group(self):
        """The groups teardown walks are the same list startup records, so a
        seventh child cannot be launched and pinned yet never killed."""
        killed: list[int] = []
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            side_effect=lambda pid: pid * 10,
        ), patch("fun_time.windows_bridge_orchestrator.close_window"), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree",
                   side_effect=killed.append):
            children = identify_children(_fake_startup_result())
            _shutdown_children(0, children, NullProgress())

        assert sorted(killed) == sorted(child.pid for child in children.values())

    def test_the_broker_is_never_a_recorded_child_so_teardown_leaves_it_running(self):
        """A session's teardown taskkills only the children it recorded at
        startup.  The broker is deliberately not one of them — it is a service
        that outlives the session (harem and the user's own tools keep
        talking to it), launched detached with its handle discarded — so a normal
        exit must leave it running."""
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            side_effect=lambda pid: pid * 10,
        ):
            children = identify_children(_fake_startup_result())

        assert not any("broker" in key for key in children)


class TestWritePidsFile:
    def _write(self, tmp_path):
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            side_effect=lambda pid: pid * 10,
        ):
            children = identify_children(_fake_startup_result())
        pids_path = tmp_path / "pids.ini"
        write_pids_file(pids_path, children)

        parser = configparser.ConfigParser()
        parser.read(str(pids_path), encoding="utf-8")
        return parser

    def test_writes_all_pids(self, tmp_path):
        parser = self._write(tmp_path)

        assert parser.getint("pids", "nau_pid") == 200
        assert parser.getint("pids", "portrait_pid") == 300
        assert parser.getint("pids", "landscape_pid") == 400
        assert parser.getint("pids", "dashboard_pid") == 500
        assert parser.getint("pids", "genau_pid") == 600
        assert parser.getint("pids", "audio_pid") == 700

    def test_writes_the_creation_time_that_pins_each_pid(self, tmp_path):
        """Teardown reads this back to tell our child from whatever process
        Windows has since handed the PID to."""
        parser = self._write(tmp_path)

        assert parser.getint("created_at", "nau_pid") == 2000
        assert parser.getint("created_at", "audio_pid") == 7000


class TestHotkeySuspendDuringIntegration:
    def test_writes_suspend_command_during_integration(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        state_dir = tmp_path / "state"

        def fake_sequence(**kwargs):
            return _fake_startup_result()

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", side_effect=fake_sequence), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_ahk_proc), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=state_dir,
                project_dir=tmp_path,
            )

        ahk_cmd_file = state_dir / "ahk_cmd.txt"
        assert ahk_cmd_file.read_text(encoding="utf-8") == "suspend_hotkeys"

    def test_no_suspend_command_outside_integration(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        state_dir = tmp_path / "state"

        def fake_sequence(**kwargs):
            return _fake_startup_result()

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", side_effect=fake_sequence), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_ahk_proc), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=state_dir,
                project_dir=tmp_path,
            )

        ahk_cmd_file = state_dir / "ahk_cmd.txt"
        assert not ahk_cmd_file.exists()


class TestRunPythonOrchestratedBridge:
    def test_runs_startup_then_launches_ahk_then_shuts_down(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        # Track call order
        calls: list[str] = []

        def fake_sequence(**kwargs):
            calls.append("startup_sequence")
            return _fake_startup_result()

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            if "loading_screen" in str(cmd):
                calls.append("launch_loading")
                return fake_loading_proc
            if "closing_screen" in str(cmd):
                calls.append("launch_closing")
                return fake_loading_proc
            calls.append("launch_ahk")
            return fake_ahk_proc

        killed_pids: list[int] = []

        def fake_kill_tree(pid):
            killed_pids.append(pid)

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", side_effect=fake_sequence), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.get_process_creation_time", side_effect=lambda pid: pid * 10), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree", side_effect=fake_kill_tree):

            code = run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe=str(tmp_path / "ahk.exe"),
                hotkey_script=str(tmp_path / "hotkeys.ahk"),
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        assert calls == ["launch_loading", "launch_ahk", "startup_sequence", "launch_closing"]
        assert code == 0

        # Should have killed all 6 child processes
        assert 200 in killed_pids  # nau
        assert 300 in killed_pids  # portrait
        assert 400 in killed_pids  # landscape
        assert 500 in killed_pids  # dashboard
        assert 600 in killed_pids  # genau
        assert 700 in killed_pids  # audio

    def test_holds_loading_screen_until_the_hud_indexes_are_primed(self, cfg_factory, tmp_path):
        """The reveal blocks on the HUD's group indexes being built, so Fun Time
        never appears with its satellites' maps still blank.  Priming runs in this
        process now (the dispatch loop owns the model), so the wait is on its
        event rather than a flag file another process writes."""
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0
        primed = threading.Event()

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence",
                   side_effect=lambda **kwargs: _fake_startup_result()),              patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_proc),              patch("fun_time.windows_bridge_orchestrator.kill_process_tree"),              patch("fun_time.windows_bridge_orchestrator._start_hud_priming",
                   return_value=(MagicMock(), primed)) as start_priming,              patch.object(primed, "wait", return_value=True) as mock_wait:
            run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state", project_dir=tmp_path,
            )

        start_priming.assert_called_once()
        mock_wait.assert_called_once_with(timeout=20.0)

    def test_does_not_wait_on_the_hud_when_it_is_disabled(self, cfg_factory, tmp_path):
        """No publisher (an integration run) means no indexes to prime, so the
        reveal must not block on one."""
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0
        primed = threading.Event()

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence",
                   side_effect=lambda **kwargs: _fake_startup_result()),              patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_proc),              patch("fun_time.windows_bridge_orchestrator.kill_process_tree"),              patch("fun_time.windows_bridge_orchestrator._start_hud_priming",
                   return_value=(None, primed)),              patch.object(primed, "wait") as mock_wait:
            run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state", project_dir=tmp_path,
            )

        mock_wait.assert_not_called()

    def test_lets_the_browser_pages_read_the_live_omnipause_state(self, cfg_factory, tmp_path):
        """The RFB tab pages poll the loopback server to decide whether to freeze
        their clips, so it has to read the dispatch loop as it runs — a state
        copied at startup would answer "playing" for the rest of the session."""
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence",
                   side_effect=lambda **kwargs: _fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_proc), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.DispatchLoopRunner") as mock_runner, \
             patch("fun_time.windows_bridge_orchestrator.serve_loopback") as mock_serve:

            run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state", project_dir=tmp_path,
            )

        omni_paused = mock_serve.call_args.kwargs["omni_paused"]
        mock_runner.return_value.state = BridgeState(omni_paused=True)
        assert omni_paused() is True
        mock_runner.return_value.state = BridgeState(omni_paused=False)
        assert omni_paused() is False

    def test_serves_on_the_port_its_own_config_named(self, cfg_factory, tmp_path):
        """8770 is machine-wide, and a busy one costs the loser its whole loopback
        surface: no Tampermonkey auto-update, and RFB tab pages that never hear
        about OmniPause.  A session started alongside another — an integration run
        above all — has to be able to serve somewhere else.
        """
        cfg = load_config(cfg_factory({"loopback_port": 54321}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence",
                   side_effect=lambda **kwargs: _fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_proc), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.serve_loopback") as mock_serve:

            run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state", project_dir=tmp_path,
            )

        assert mock_serve.call_args.kwargs["port"] == 54321

    def test_passes_manifest_and_pids_file_to_ahk(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        popen_cmds: list[list] = []

        def fake_sequence(**kwargs):
            return _fake_startup_result()

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            popen_cmds.append(list(cmd))
            if "loading_screen" in str(cmd):
                return fake_loading_proc
            return fake_ahk_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", side_effect=fake_sequence), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="C:\\ahk.exe",
                hotkey_script="C:\\hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        # Find the AHK launch command (not the loading screen one)
        ahk_cmd = [c for c in popen_cmds if "ahk.exe" in str(c)][0]
        assert ahk_cmd[0] == "C:\\ahk.exe"
        assert ahk_cmd[1] == "C:\\hotkeys.ahk"
        assert ahk_cmd[2] == str(manifest_path)
        assert ahk_cmd[3].endswith(".ini")


class TestLoadingScreenLifecycle:
    """Loading screen is launched in normal mode and skipped in integration mode."""

    def test_loading_screen_launched_in_normal_mode(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        result_with_hwnds = StartupResult(
            nau_pid=200, portrait_pid=300, landscape_pid=400,
            dashboard_pid=500, genau_pid=600, audio_pid=700,
            layout_plan=_fake_plan(),
        )

        popen_calls: list[list] = []
        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            if "loading_screen" in str(cmd):
                return fake_loading_proc
            return fake_ahk_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", return_value=result_with_hwnds), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        # Loading screen subprocess should have been launched
        loading_cmd = [c for c in popen_calls if "loading_screen" in str(c)]
        assert len(loading_cmd) == 1, "Loading screen subprocess not launched"

    def test_loading_screen_skipped_in_integration_mode(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        popen_calls: list[list] = []
        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return fake_ahk_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", return_value=_fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        # No loading screen subprocess should have been launched
        loading_cmds = [c for c in popen_calls if "loading_screen" in str(c)]
        assert len(loading_cmds) == 0, "Loading screen launched in integration mode"


class TestClosingScreenLifecycle:
    """The session's windows go out behind a cover, the way they came in behind
    one: raised before the first kill, dropped after the last."""

    def _run(self, cfg_factory, tmp_path, *, events: list[str], ready: bool = True):
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        state_dir = tmp_path / "state"

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_overlay_proc = MagicMock()
        fake_overlay_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            if "closing_screen" in str(cmd):
                events.append("cover_up")
                if ready:
                    # What the real closing screen does the moment it is painted.
                    ready_file_for(state_dir / SHUTDOWN_PROGRESS_FILENAME).write_text(
                        "", encoding="utf-8"
                    )
                return fake_overlay_proc
            if "loading_screen" in str(cmd):
                return fake_overlay_proc
            return fake_ahk_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence",
                   return_value=_fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.get_process_creation_time",
                   side_effect=lambda pid: pid * 10), \
             patch("fun_time.windows_bridge_orchestrator.close_window",
                   side_effect=lambda hwnd: events.append("close_browser")), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree",
                   side_effect=lambda pid: events.append(f"kill:{pid}")):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=state_dir,
                project_dir=tmp_path,
            )
        return state_dir

    def test_the_cover_is_up_before_the_first_window_goes(self, cfg_factory, tmp_path):
        events: list[str] = []

        self._run(cfg_factory, tmp_path, events=events)

        assert events[0] == "cover_up"
        assert set(events[1:]) == {
            "close_browser", "kill:200", "kill:300", "kill:400",
            "kill:500", "kill:600", "kill:700", "kill:800",
        }

    def test_nothing_is_killed_until_the_cover_says_it_is_painted(self, cfg_factory, tmp_path):
        """A tkinter process needs a moment to boot, and a cover that is not on
        screen yet hides nothing — so teardown holds until the screen's own
        ready flag lands, not merely until its process has been spawned."""
        events: list[str] = []
        seen_when_killing: list[bool] = []

        real_wait = windows_bridge_orchestrator._wait_for_closing_screen

        def recording_wait(ready_file, proc):
            real_wait(ready_file, proc)
            seen_when_killing.append(ready_file.exists())

        with patch.object(windows_bridge_orchestrator, "_wait_for_closing_screen",
                          side_effect=recording_wait):
            self._run(cfg_factory, tmp_path, events=events)

        assert seen_when_killing == [True]

    def test_the_cover_comes_down_only_once_everything_is_gone(self, cfg_factory, tmp_path):
        events: list[str] = []

        real_advance = PhaseProgress.advance
        real_finish = PhaseProgress.finish

        def spy_advance(self, phase):
            real_advance(self, phase)
            events.append(f"advance:{phase}")

        def spy_finish(self):
            real_finish(self)
            events.append("done")

        with patch.object(PhaseProgress, "advance", spy_advance), \
             patch.object(PhaseProgress, "finish", spy_finish):
            state_dir = self._run(cfg_factory, tmp_path, events=events)

        assert events[-1] == "done"
        assert events.index("advance:browser") < events.index("close_browser")
        assert events.index("advance:players") < events.index("kill:300")
        assert events.index("advance:companions") < events.index("kill:500")
        # Nothing of the shutdown channel is left behind for the next session.
        assert not (state_dir / SHUTDOWN_PROGRESS_FILENAME).exists()
        assert not ready_file_for(state_dir / SHUTDOWN_PROGRESS_FILENAME).exists()

    def test_no_closing_screen_in_integration_mode(self, cfg_factory, tmp_path, monkeypatch):
        """An integration run has no eyes on it and no desktop of its own to
        cover — the same reason it skips the loading screen."""
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        events: list[str] = []

        self._run(cfg_factory, tmp_path, events=events)

        assert "cover_up" not in events


@pytest.mark.real_startup_waits
class TestWaitForClosingScreen:
    """The hold itself, so it runs on the real timeout the session spends."""

    def test_returns_as_soon_as_the_flag_lands(self, tmp_path):
        ready_file = tmp_path / "shutdown_ready.flag"
        ready_file.write_text("", encoding="utf-8")
        proc = MagicMock()
        proc.poll.return_value = None

        windows_bridge_orchestrator._wait_for_closing_screen(ready_file, proc)

    def test_stops_waiting_on_a_screen_that_died(self, tmp_path, caplog):
        """No flag will ever land from a process that has exited, so waiting out
        the full timeout would only delay a teardown that has to happen anyway."""
        proc = MagicMock()
        proc.poll.return_value = 1

        with caplog.at_level("WARNING", logger="fun_time.windows_bridge_orchestrator"):
            windows_bridge_orchestrator._wait_for_closing_screen(
                tmp_path / "shutdown_ready.flag", proc
            )

        assert "exited before it was ready" in caplog.text

    def test_gives_up_rather_than_wedge_the_teardown(self, tmp_path, caplog, monkeypatch):
        """A screen that is alive but never reports is worth a flicker, not a
        session that will not close."""
        monkeypatch.setattr(
            windows_bridge_orchestrator, "CLOSING_SCREEN_READY_TIMEOUT_S", 0.05
        )
        proc = MagicMock()
        proc.poll.return_value = None

        with caplog.at_level("WARNING", logger="fun_time.windows_bridge_orchestrator"):
            windows_bridge_orchestrator._wait_for_closing_screen(
                tmp_path / "shutdown_ready.flag", proc
            )

        assert "anyway" in caplog.text


class TestStartupCancellation:
    """Pressing Esc aborts startup: the half-built session is torn down, the
    hotkey script that read the Esc is taken back out, and the dispatch loop
    never starts."""

    def test_cancel_mid_startup_kills_launched_children_and_stops_the_hotkeys(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        state_dir = tmp_path / "state"

        popen_cmds: list[list] = []
        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0
        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            popen_cmds.append(list(cmd))
            if "loading_screen" in str(cmd):
                return fake_loading_proc
            return fake_ahk_proc

        def cancel_sequence(**kwargs):
            raise StartupCancelled(launched_pids=[300, 400, 600], rfb_hwnd=1234)

        killed: list[int] = []
        closed: list[int] = []

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", side_effect=cancel_sequence), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree", side_effect=killed.append), \
             patch("fun_time.windows_bridge_orchestrator.close_window", side_effect=closed.append), \
             patch("fun_time.windows_bridge_orchestrator.DispatchLoopRunner") as mock_runner:

            code = run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=state_dir, project_dir=tmp_path,
            )

        assert code == 0
        assert {300, 400, 600} <= set(killed)
        assert 1234 in closed
        # The hotkey script is up from the start of a launch, so a cancelled one
        # has to take it back out — left running it would go on swallowing every
        # key it binds with nothing to hand them to.
        assert len([c for c in popen_cmds if "ahk.exe" in str(c)]) == 1
        assert (state_dir / "ahk_cmd.txt").read_text(encoding="utf-8") == "exit"
        fake_ahk_proc.wait.assert_called()
        # Its keys never went live: the pids file is what lifts that hold, and
        # this startup never got as far as writing one.
        assert not (state_dir / "bridge_pids.ini").exists()
        mock_runner.assert_not_called()
        # The overlay is brought down after teardown.
        fake_loading_proc.wait.assert_called()

    def test_cancel_flag_after_a_finished_sequence_tears_down_the_full_result(self, cfg_factory, tmp_path):
        """The user can hit Esc in the sliver between the last checkpoint and the
        reveal: the sequence returns a full result but the flag is set, so the
        whole result is torn down and the reveal never happens."""
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        state_dir = tmp_path / "state"

        def sequence_then_flag(**kwargs):
            state_dir.mkdir(parents=True, exist_ok=True)
            cancel_file_for(state_dir / PROGRESS_FILENAME).write_text("", encoding="utf-8")
            return _fake_startup_result()

        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0
        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0

        popen_cmds: list[list] = []

        def fake_popen(cmd, **kwargs):
            popen_cmds.append(list(cmd))
            if "loading_screen" in str(cmd):
                return fake_loading_proc
            return fake_ahk_proc

        killed: list[int] = []

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", side_effect=sequence_then_flag), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree", side_effect=killed.append), \
             patch("fun_time.windows_bridge_orchestrator.close_window"), \
             patch("fun_time.windows_bridge_orchestrator._start_hud_priming",
                   return_value=(None, threading.Event())) as mock_priming, \
             patch("fun_time.windows_bridge_orchestrator.DispatchLoopRunner") as mock_runner:

            code = run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=state_dir, project_dir=tmp_path,
            )

        assert code == 0
        assert {200, 300, 400, 500, 600, 700} <= set(killed)
        assert (state_dir / "ahk_cmd.txt").read_text(encoding="utf-8") == "exit"
        assert not (state_dir / "bridge_pids.ini").exists()
        mock_runner.assert_not_called()
        # Priming is kicked off before the sequence, but the run bailed before the
        # reveal, so no dispatch loop was ever started to publish what it warmed.
        mock_priming.assert_called_once()

    def test_stale_cancel_flag_is_cleared_before_startup(self, cfg_factory, tmp_path):
        """A cancel flag left over from a previous session must not abort this
        one — it is cleared before the loading screen launches."""
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        stale = cancel_file_for(state_dir / PROGRESS_FILENAME)
        stale.write_text("", encoding="utf-8")

        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0
        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0

        popen_cmds: list[list] = []

        def fake_popen(cmd, **kwargs):
            popen_cmds.append(list(cmd))
            if "loading_screen" in str(cmd):
                return fake_loading_proc
            return fake_ahk_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", return_value=_fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator._start_hud_priming",
                   return_value=(None, threading.Event())), \
             patch("fun_time.windows_bridge_orchestrator.DispatchLoopRunner"):

            code = run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=state_dir, project_dir=tmp_path,
            )

        assert code == 0
        assert not stale.exists()  # cleared before startup
        # Startup ran to completion: the stale flag was not honored as a cancel.
        assert [c for c in popen_cmds if "ahk.exe" in str(c)]


class TestHotkeyScriptGoesUpFirst:
    """The hotkey script is launched before the startup sequence runs, not after
    it.

    Its hotkeys are the only keys in a launch that do not care which window holds
    the focus — AHK hooks the keyboard rather than waiting its turn in a window's
    message queue — so this ordering is what makes Esc reach the cancel after
    something has taken the focus from the loading screen.  Launched last, as it
    was, there was nothing hooking Esc during the one stretch where Esc is the
    only way out.
    """

    def _run(self, cfg_factory, tmp_path, *, on_ahk_launch=None) -> Path:
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        state_dir = tmp_path / "state"

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_overlay_proc = MagicMock()
        fake_overlay_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            if "ahk.exe" in str(cmd):
                if on_ahk_launch is not None:
                    on_ahk_launch()
                return fake_ahk_proc
            return fake_overlay_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence",
                   return_value=_fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=state_dir,
                project_dir=tmp_path,
            )
        return state_dir

    def test_the_script_is_up_before_the_sequence_starts_launching_windows(
        self, cfg_factory, tmp_path
    ):
        calls: list[str] = []

        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            calls.append("ahk" if "ahk.exe" in str(cmd) else "overlay")
            return fake_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence",
                   side_effect=lambda **kwargs: (calls.append("sequence"), _fake_startup_result())[1]), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        assert calls.index("ahk") < calls.index("sequence")

    def test_a_dead_sessions_pids_file_is_gone_before_the_script_can_read_it(
        self, cfg_factory, tmp_path
    ):
        """The pids file appearing is what tells the script the session is up and
        its keys have something to reach.  A previous session's copy would put
        every key live over one that is still assembling — and would take Esc's
        cancel away with them, since Esc only cancels while the hold is on."""
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "bridge_pids.ini").write_text("[pids]\nnau_pid = 999\n", encoding="utf-8")

        seen: list[bool] = []
        self._run(
            cfg_factory, tmp_path,
            on_ahk_launch=lambda: seen.append((state_dir / "bridge_pids.ini").exists()),
        )

        assert seen == [False]
        # …and this session writes its own once startup is done, which is the
        # handover: from here the hotkeys are live.
        assert (state_dir / "bridge_pids.ini").is_file()


class TestPostLoadingWindowState:
    """Z-order must be re-asserted AFTER the loading screen closes.

    Phase 4 sets topmost while the loading screen overlay is still up.
    When the overlay is destroyed, the OS may rearrange z-order.  The
    orchestrator must correct this after the loading screen exits.
    """

    def test_window_state_reasserted_after_loading_closes(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        result_with_hwnds = StartupResult(
            nau_pid=200, portrait_pid=300, landscape_pid=400,
            dashboard_pid=500, genau_pid=600, audio_pid=700,
            layout_plan=_fake_plan(),
            rfb_hwnd=55555,
        )

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0
        fake_loading_proc.pid = 9999

        def fake_popen(cmd, **kwargs):
            if "loading_screen" in str(cmd):
                return fake_loading_proc
            return fake_ahk_proc

        topmost_calls: list[tuple] = []
        hide_calls: list[int] = []
        GENAU_HWND = 6060
        DASH_HWND = 5050
        pid_to_hwnd = {200: 2020, 300: 3030, 400: 4040, 500: DASH_HWND}
        title_to_hwnd = {"Fun Time": DASH_HWND, "Genau": GENAU_HWND}

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", return_value=result_with_hwnds), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=lambda h, v: topmost_calls.append((h, v))), \
             patch("fun_time.windows_bridge_sequencer.minimize_window", side_effect=lambda h, **kw: hide_calls.append(h)), \
             patch("fun_time.windows_bridge_sequencer.disable_window_transitions"), \
             patch("fun_time.windows_bridge_orchestrator.iter_zorder", return_value=[]), \
             patch("fun_time.windows_bridge_orchestrator.wait_for_window_by_title", side_effect=lambda title, **kw: title_to_hwnd.get(title, 0)):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        # nau startup mode: the inactive slot-mate (Genau) is minimized.
        assert GENAU_HWND in hide_calls, f"Genau not minimized: {hide_calls}"

        # nau startup mode: the windows that own a rect are promoted to topmost,
        # Nau (hwnd 2020) included — it floats above the desktop like the main player
        # player always has.  Genau, the hidden slot-mate, is held out of the
        # band: it is promoted last, so joining it would put it over Nau.
        promoted = {h for h, v in topmost_calls if v}
        assert {DASH_HWND, 2020, 3030, 4040, 55555} <= promoted, (
            f"Wrong promotions: {topmost_calls}"
        )
        assert GENAU_HWND not in promoted, f"Genau promoted over Nau: {topmost_calls}"


class TestNauObstructionLog:
    """After the bands are re-applied, the orchestrator walks the real z-order
    and names whatever still covers Nau — the diagnostic that turns a "Nau isn't
    on top" report into the exact culprit window, since the topmost flag alone
    reads True even when Nau is buried."""

    def test_names_the_window_covering_nau(self, caplog):
        stack = [
            StackedWindow(hwnd=99, title="Claude", topmost=False, rect=(2560, 2500, 1440, 900)),
            StackedWindow(hwnd=2020, title="Nau", topmost=True, rect=(2560, 2500, 1440, 900)),
        ]
        with patch("fun_time.windows_bridge_orchestrator.iter_zorder", return_value=stack), \
             caplog.at_level("WARNING", logger="fun_time.windows_bridge_orchestrator"):
            _log_window_obstruction("Nau", 2020)
        assert "covered at startup" in caplog.text
        assert "Claude" in caplog.text
        assert "topmost=False" in caplog.text  # a non-topmost window over topmost Nau

    def test_quiet_when_nau_is_frontmost(self, caplog):
        stack = [StackedWindow(hwnd=2020, title="Nau", topmost=True, rect=(2560, 2500, 1440, 900))]
        with patch("fun_time.windows_bridge_orchestrator.iter_zorder", return_value=stack), \
             caplog.at_level("INFO", logger="fun_time.windows_bridge_orchestrator"):
            _log_window_obstruction("Nau", 2020)
        assert "frontmost over its rect" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= 30]  # no WARNING

    def test_warns_when_nau_unresolved(self, caplog):
        with patch("fun_time.windows_bridge_orchestrator.iter_zorder") as it, \
             caplog.at_level("WARNING", logger="fun_time.windows_bridge_orchestrator"):
            _log_window_obstruction("Nau", 0)
        it.assert_not_called()  # nothing to walk if Nau never resolved
        assert "unresolved" in caplog.text

    def test_quiet_when_only_the_sessions_own_genau_layer_covers_nau(self, caplog):
        """In hybrid, Genau's window is the transparent HUD layer over Nau's
        video — over it on purpose.  Warning on that toasted every hybrid
        startup with a covering window that covers nothing visible."""
        stack = [
            StackedWindow(hwnd=1010, title="Hybrid Nau+Genau", topmost=True,
                          rect=(2560, 2483, 1440, 930)),
            StackedWindow(hwnd=2020, title="Nau", topmost=True, rect=(2560, 2500, 1440, 900)),
        ]
        with patch("fun_time.windows_bridge_orchestrator.iter_zorder", return_value=stack), \
             caplog.at_level("INFO", logger="fun_time.windows_bridge_orchestrator"):
            _log_window_obstruction("Nau", 2020, expected_over=1010)
        assert "frontmost over its rect" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= 30]  # no WARNING

    def test_a_third_window_still_warns_past_the_expected_layer(self, caplog):
        stack = [
            StackedWindow(hwnd=99, title="Claude", topmost=False, rect=(2560, 2500, 1440, 900)),
            StackedWindow(hwnd=1010, title="Hybrid Nau+Genau", topmost=True,
                          rect=(2560, 2483, 1440, 930)),
            StackedWindow(hwnd=2020, title="Nau", topmost=True, rect=(2560, 2500, 1440, 900)),
        ]
        with patch("fun_time.windows_bridge_orchestrator.iter_zorder", return_value=stack), \
             caplog.at_level("WARNING", logger="fun_time.windows_bridge_orchestrator"):
            _log_window_obstruction("Nau", 2020, expected_over=1010)
        assert "covered at startup" in caplog.text
        assert "Claude" in caplog.text
        assert "Hybrid Nau+Genau" not in caplog.text


class TestVoiceControlIntegration:
    def test_voice_controller_started_when_enabled(self, cfg_factory, tmp_path):
        path = cfg_factory({"voice_control": {"enabled": True, "model_path": "test-model"}})
        cfg = load_config(path)
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            if "loading_screen" in str(cmd):
                return fake_loading_proc
            return fake_ahk_proc

        mock_vc = MagicMock()

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", return_value=_fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.VOICE_AVAILABLE", True), \
             patch("fun_time.windows_bridge_orchestrator.VoiceController", return_value=mock_vc):

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        mock_vc.stop.assert_called_once()

    def test_voice_controller_skipped_when_not_available(self, cfg_factory, tmp_path):
        path = cfg_factory({"voice_control": {"enabled": True, "model_path": "test-model"}})
        cfg = load_config(path)
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            if "loading_screen" in str(cmd):
                return fake_loading_proc
            return fake_ahk_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", return_value=_fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.VOICE_AVAILABLE", False), \
             patch("fun_time.windows_bridge_orchestrator.VoiceController") as mock_vc_class:

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        mock_vc_class.assert_not_called()

    def test_voice_controller_skipped_when_disabled(self, cfg_factory, tmp_path):
        # voice_control section absent → defaults to disabled
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.return_value = 0

        def fake_popen(cmd, **kwargs):
            if "loading_screen" in str(cmd):
                return fake_loading_proc
            return fake_ahk_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", return_value=_fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.VOICE_AVAILABLE", True), \
             patch("fun_time.windows_bridge_orchestrator.VoiceController") as mock_vc_class:

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        mock_vc_class.assert_not_called()


class TestOpenEventLog:
    def test_truncates_the_previous_session_and_tails_every_fun_time_logger(self, tmp_path):
        """One handler on the package logger catches every fun_time.* module by
        propagation, and the package level is opened all the way down: the file
        carries everything and the log panel picks the verbosity."""
        import logging

        from fun_time.event_log import EventLogHandler, event_log_path, read_events

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        event_log_path(state_dir).write_text('{"ts":1,"level":20,"source":"dash","msg":"stale"}\n',
                                             encoding="utf-8")
        package_logger = logging.getLogger("fun_time")
        original_handlers = list(package_logger.handlers)
        original_level = package_logger.level
        try:
            _open_event_log(state_dir)

            assert package_logger.level == logging.DEBUG
            assert any(isinstance(h, EventLogHandler) for h in package_logger.handlers)

            logging.getLogger("fun_time.some_module").debug("chatter")
            records, _offset = read_events(event_log_path(state_dir))
            assert [r.message for r in records] == ["chatter"]
        finally:
            for handler in package_logger.handlers[:]:
                if handler not in original_handlers:
                    package_logger.removeHandler(handler)
            package_logger.setLevel(original_level)

    def test_re_opening_replaces_the_handler_rather_than_stacking_one(self, tmp_path):
        import logging

        from fun_time.event_log import EventLogHandler

        package_logger = logging.getLogger("fun_time")
        original_handlers = list(package_logger.handlers)
        original_level = package_logger.level
        try:
            _open_event_log(tmp_path / "one")
            _open_event_log(tmp_path / "two")

            installed = [h for h in package_logger.handlers if isinstance(h, EventLogHandler)]
            assert len(installed) == 1
            assert installed[0].path.parent == tmp_path / "two"
        finally:
            for handler in package_logger.handlers[:]:
                if handler not in original_handlers:
                    package_logger.removeHandler(handler)
            package_logger.setLevel(original_level)

    def test_the_orchestrator_logger_is_enrolled_even_though_it_does_not_propagate(self, tmp_path):
        """configure_logging turns propagation off for the console logger, so the
        one handler on the package would never see its lines."""
        import logging

        from fun_time.event_log import event_log_path, read_events

        orch_logger = logging.getLogger("fun_time.orchestrator")
        package_logger = logging.getLogger("fun_time")
        original = (list(package_logger.handlers), list(orch_logger.handlers),
                    package_logger.level, orch_logger.level, orch_logger.propagate)
        try:
            orch_logger.propagate = False
            orch_logger.setLevel(logging.INFO)
            _open_event_log(tmp_path)

            orch_logger.info("bridge exited")

            records, _offset = read_events(event_log_path(tmp_path))
            assert [r.message for r in records] == ["bridge exited"]
        finally:
            package_logger.handlers[:] = original[0]
            orch_logger.handlers[:] = original[1]
            package_logger.setLevel(original[2])
            orch_logger.setLevel(original[3])
            orch_logger.propagate = original[4]


class TestOrigeneratorGracefulClose:
    def test_teardown_closes_the_window_before_the_kill_sweep(self):
        """Its closeEvent persists the session and queues the absence
        experiments, so the window is asked to close first; the taskkill that
        follows is the backstop, not the normal death."""
        closed: list[int] = []
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            side_effect=[8000, None],  # recorded alive, then exited after the close
        ), patch(
            "fun_time.windows_bridge_orchestrator.find_window_for_process",
            return_value=4242,
        ), patch("fun_time.windows_bridge_orchestrator.close_window",
                 side_effect=closed.append):
            child = ChildProcess(pid=800, created_at=8000)
            _close_origenerator_gracefully(child)
        assert closed == [4242]

    def test_no_window_means_nothing_to_close(self):
        with patch(
            "fun_time.windows_bridge_orchestrator.find_window_for_process",
            return_value=0,
        ), patch("fun_time.windows_bridge_orchestrator.close_window") as close:
            _close_origenerator_gracefully(ChildProcess(pid=800, created_at=8000))
        close.assert_not_called()

    def test_a_session_without_origenerator_skips_the_close(self):
        with patch("fun_time.windows_bridge_orchestrator.close_window") as close:
            _close_origenerator_gracefully(ChildProcess(pid=0, created_at=0))
            _close_origenerator_gracefully(None)
        close.assert_not_called()


class TestTheFinishingPassFitsBehindTheCover:
    def test_it_cannot_outlast_the_covers_staleness_guard(self):
        """The room is banded and settled behind the cover, and the cover comes
        down on the DONE written at the end of that.  Nothing writes the progress
        file in between, so the cover's staleness guard — its protection against
        an orchestrator that died holding the screen — is running the whole time.
        Outlast it and the cover takes itself down mid-pass, which is the user
        watching the z-order sort itself out: the exact thing it is up for.

        Every wait that pass can take, added up, has to clear that guard.  Five
        window resolutions: the dashboard, Nau, Genau, and the two satellites.
        """
        budget = (
            HUD_PRIME_TIMEOUT_S
            + 5 * POST_LOADING_RESOLVE_TIMEOUT_S
            + SETTLE_PASSES * SETTLE_WAIT_S
        )
        assert budget < STALE_TIMEOUT_S


class TestThePlayersStartWhenTheCoverIsGone:
    """Nau's video and Genau's audio must not run behind the cover.

    The phase walk used to release them as its last act, which was also the
    moment the cover came down {D} so they lined up.  Now the cover is held
    through the finishing pass, and releasing with the phases would mean the
    video (and the audio, which he can hear through nothing) running for seconds
    behind a scrim, its opening spent before he can see it.  So the release is
    the orchestrator's, and it comes after the cover's process is gone.
    """

    def _run(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        events: list[str] = []

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0
        fake_loading_proc = MagicMock()
        fake_loading_proc.wait.side_effect = lambda **_kw: events.append("cover gone")

        def fake_popen(cmd, **kwargs):
            return fake_loading_proc if "loading_screen" in str(cmd) else fake_ahk_proc

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence",
                   return_value=_fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen",
                   side_effect=fake_popen), \
             patch("fun_time.windows_bridge_orchestrator._fix_post_loading_windows",
                   return_value={}), \
             patch("fun_time.windows_bridge_orchestrator.release_the_players",
                   side_effect=lambda *_a: events.append("players released")), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"):
            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )
        return events

    def test_the_release_waits_for_the_cover_to_go(self, cfg_factory, tmp_path):
        events = self._run(cfg_factory, tmp_path)

        assert "players released" in events, "the players were never started"
        assert events.index("cover gone") < events.index("players released"), (
            "the players were started while the cover was still up"
        )

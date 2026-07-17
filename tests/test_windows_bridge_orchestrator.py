from __future__ import annotations

import configparser
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from fun_time.config import load_config
from fun_time.manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.windows_bridge_orchestrator import (
    ChildProcess,
    _log_nau_obstruction,
    _open_event_log,
    _shutdown_children,
    identify_children,
    kill_process_tree,
    kill_recorded_child,
    write_pids_file,
    run_python_orchestrated_bridge,
)
from fun_time.win32 import StackedWindow
from fun_time.windows_bridge_sequencer import StartupResult
from fun_time.window_layout import WindowLayoutPlan, WindowRect
from fun_time.startup_progress import (
    PROGRESS_FILENAME,
    StartupCancelled,
    cancel_file_for,
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
        layout_plan=_fake_plan(),
    )


class TestKillProcessTree:
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

    def test_records_the_lock_hud_so_teardown_can_kill_it(self):
        """The HUD is an always-on-top overlay — it must never outlive the session."""
        result = StartupResult(
            nau_pid=200, portrait_pid=300, landscape_pid=400,
            dashboard_pid=500, genau_pid=600, audio_pid=700,
            lock_hud_pid=555, layout_plan=_fake_plan(),
        )
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            side_effect=lambda pid: pid * 10,
        ):
            children = identify_children(result)

        assert children["lock_hud_pid"] == ChildProcess(pid=555, created_at=5550)


class TestShutdownChildren:
    def test_closes_rfb_window(self):
        with patch("fun_time.windows_bridge_orchestrator.kill_recorded_child"), \
             patch("fun_time.windows_bridge_orchestrator.close_window") as mock_close:
            _shutdown_children(88888, {})

        mock_close.assert_called_once_with(88888)

    def test_skips_rfb_close_when_no_hwnd(self):
        with patch("fun_time.windows_bridge_orchestrator.kill_recorded_child"), \
             patch("fun_time.windows_bridge_orchestrator.close_window") as mock_close:
            _shutdown_children(0, {})

        mock_close.assert_called_once_with(0)

    def test_kills_the_recorded_children_but_never_a_recycled_pid(self):
        children = {
            "nau_pid": ChildProcess(pid=200, created_at=111),
            "portrait_pid": ChildProcess(pid=300, created_at=222),
        }
        live_creation_times = {200: 111, 300: 999}  # 300 was recycled
        with patch(
            "fun_time.windows_bridge_orchestrator.get_process_creation_time",
            side_effect=live_creation_times.get,
        ), patch("fun_time.windows_bridge_orchestrator.kill_process_tree") as mock_kill, \
             patch("fun_time.windows_bridge_orchestrator.close_window"):
            _shutdown_children(0, children)

        mock_kill.assert_called_once_with(200)

    def test_the_broker_is_never_a_recorded_child_so_teardown_leaves_it_running(self):
        """A session's teardown taskkills only the children it recorded at
        startup.  The broker is deliberately not one of them — it is a service
        that outlives the session (harem and the user's direct VLC+MFP use keep
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
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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

    def test_no_suspend_command_outside_integration(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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
    def test_runs_startup_then_launches_ahk_then_shuts_down(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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

        assert calls == ["launch_loading", "startup_sequence", "launch_ahk"]
        assert code == 0

        # Should have killed all 6 child processes
        assert 200 in killed_pids  # nau
        assert 300 in killed_pids  # portrait
        assert 400 in killed_pids  # landscape
        assert 500 in killed_pids  # dashboard
        assert 600 in killed_pids  # genau
        assert 700 in killed_pids  # audio

    def test_holds_loading_screen_until_the_hud_signals_ready(self, cfg_factory, tmp_path, monkeypatch):
        """The loading-screen reveal blocks on the HUD's ready flag, so Fun Time
        never appears with its maps still blank."""
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        state_dir = tmp_path / "state"

        def fake_sequence(**kwargs):
            return replace(_fake_startup_result(), lock_hud_pid=555)

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", side_effect=fake_sequence), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_proc), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.wait_for_hud_ready", return_value=True) as mock_wait:
            run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=state_dir, project_dir=tmp_path,
            )

        mock_wait.assert_called_once()
        assert mock_wait.call_args.args[0] == state_dir / "lock_hud_ready.txt"

    def test_does_not_wait_on_the_hud_when_it_was_not_launched(self, cfg_factory, tmp_path, monkeypatch):
        """With the HUD disabled (lock_hud_pid 0), there is no flag to wait on, so
        the reveal must not block on one."""
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        fake_proc = MagicMock()
        fake_proc.wait.return_value = 0

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence",
                   side_effect=lambda **kwargs: _fake_startup_result()), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_proc), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.wait_for_hud_ready") as mock_wait:
            run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state", project_dir=tmp_path,
            )

        mock_wait.assert_not_called()

    def test_passes_manifest_and_pids_file_to_ahk(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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

    def test_loading_screen_launched_in_normal_mode(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        result_with_hwnds = StartupResult(
            nau_pid=200, portrait_pid=300, landscape_pid=400,
            dashboard_pid=500, genau_pid=600, audio_pid=700,
            layout_plan=_fake_plan(),
            core_hwnds=[1010, 2020, 3030, 4040],
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
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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


class TestStartupCancellation:
    """Pressing Esc on the loading screen aborts startup: the half-built session
    is torn down, and AHK / the dispatch loop never start."""

    def test_cancel_mid_startup_kills_launched_children_and_skips_ahk(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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
        # AHK is never launched, and the dispatch loop never starts.
        assert not [c for c in popen_cmds if "ahk.exe" in str(c)]
        mock_runner.assert_not_called()
        # The overlay is brought down after teardown.
        fake_loading_proc.wait.assert_called()

    def test_cancel_flag_after_a_finished_sequence_tears_down_the_full_result(self, cfg_factory, tmp_path, monkeypatch):
        """The user can hit Esc in the sliver between the last checkpoint and the
        reveal: the sequence returns a full result but the flag is set, so the
        whole result is torn down and the reveal never happens."""
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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
             patch("fun_time.windows_bridge_orchestrator.wait_for_hud_ready") as mock_wait, \
             patch("fun_time.windows_bridge_orchestrator.DispatchLoopRunner") as mock_runner:

            code = run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=state_dir, project_dir=tmp_path,
            )

        assert code == 0
        assert {200, 300, 400, 500, 600, 700} <= set(killed)
        assert not [c for c in popen_cmds if "ahk.exe" in str(c)]
        mock_runner.assert_not_called()
        mock_wait.assert_not_called()  # bailed before the reveal / HUD wait

    def test_stale_cancel_flag_is_cleared_before_startup(self, cfg_factory, tmp_path, monkeypatch):
        """A cancel flag left over from a previous session must not abort this
        one — it is cleared before the loading screen launches."""
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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
             patch("fun_time.windows_bridge_orchestrator.wait_for_hud_ready", return_value=True), \
             patch("fun_time.windows_bridge_orchestrator.DispatchLoopRunner"):

            code = run_python_orchestrated_bridge(
                manifest_path=manifest_path, ahk_exe="ahk.exe", hotkey_script="hotkeys.ahk",
                state_dir=state_dir, project_dir=tmp_path,
            )

        assert code == 0
        assert not stale.exists()  # cleared before startup
        # Startup ran to completion: the stale flag was not honored as a cancel.
        assert [c for c in popen_cmds if "ahk.exe" in str(c)]


class TestPostLoadingWindowState:
    """Z-order must be re-asserted AFTER the loading screen closes.

    Phase 4 sets topmost while the loading screen overlay is still up.
    When the overlay is destroyed, the OS may rearrange z-order.  The
    orchestrator must correct this after the loading screen exits.
    """

    def test_window_state_reasserted_after_loading_closes(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        result_with_hwnds = StartupResult(
            nau_pid=200, portrait_pid=300, landscape_pid=400,
            dashboard_pid=500, genau_pid=600, audio_pid=700,
            layout_plan=_fake_plan(),
            core_hwnds=[1010, 2020, 3030, 4040],
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

        # nau startup mode: every managed window is promoted to topmost, Nau
        # (hwnd 2020) included — it floats above the desktop like the primary
        # player always has.
        promoted = {h for h, v in topmost_calls if v}
        assert {DASH_HWND, GENAU_HWND, 2020, 3030, 4040, 55555} <= promoted, (
            f"Wrong promotions: {topmost_calls}"
        )


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
            _log_nau_obstruction(2020)
        assert "covered at startup" in caplog.text
        assert "Claude" in caplog.text
        assert "topmost=False" in caplog.text  # a non-topmost window over topmost Nau

    def test_quiet_when_nau_is_frontmost(self, caplog):
        stack = [StackedWindow(hwnd=2020, title="Nau", topmost=True, rect=(2560, 2500, 1440, 900))]
        with patch("fun_time.windows_bridge_orchestrator.iter_zorder", return_value=stack), \
             caplog.at_level("INFO", logger="fun_time.windows_bridge_orchestrator"):
            _log_nau_obstruction(2020)
        assert "frontmost over its rect" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= 30]  # no WARNING

    def test_warns_when_nau_unresolved(self, caplog):
        with patch("fun_time.windows_bridge_orchestrator.iter_zorder") as it, \
             caplog.at_level("WARNING", logger="fun_time.windows_bridge_orchestrator"):
            _log_nau_obstruction(0)
        it.assert_not_called()  # nothing to walk if Nau never resolved
        assert "unresolved" in caplog.text


class TestVoiceControlIntegration:
    def test_voice_controller_started_when_enabled(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        path = cfg_factory({"voice_control": {"enabled": True, "model_path": "test-model"}})
        cfg = load_config(path)
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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

    def test_voice_controller_skipped_when_not_available(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        path = cfg_factory({"voice_control": {"enabled": True, "model_path": "test-model"}})
        cfg = load_config(path)
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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

    def test_voice_controller_skipped_when_disabled(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        # voice_control section absent → defaults to disabled
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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

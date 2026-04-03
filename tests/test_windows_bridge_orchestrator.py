from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from fun_time.config import load_config
from fun_time.manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.windows_bridge_orchestrator import (
    _minimize_all_windows,
    _shutdown_children,
    write_pids_file,
    run_python_orchestrated_bridge,
)
from fun_time.windows_bridge_sequencer import StartupResult
from fun_time.window_layout import WindowLayoutPlan, WindowRect


def _fake_plan() -> WindowLayoutPlan:
    r = WindowRect(0, 0, 100, 100)
    return WindowLayoutPlan(
        portrait=r, primary=r, landscape=r, mfp=r,
        dashboard=r, random_favs_browser=r, genau=r,
    )


def _fake_startup_result() -> StartupResult:
    return StartupResult(
        primary_pid=100,
        mfp_pid=200,
        portrait_pid=300,
        landscape_pid=400,
        dashboard_pid=500,
        genau_pid=600,
        audio_pid=700,
        layout_plan=_fake_plan(),
    )


class TestShutdownChildren:
    def test_closes_rfb_window(self):
        result = StartupResult(
            primary_pid=100, mfp_pid=200, portrait_pid=300, landscape_pid=400,
            dashboard_pid=500, genau_pid=600, audio_pid=700,
            layout_plan=_fake_plan(), rfb_hwnd=88888,
        )
        with patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.close_window") as mock_close:
            _shutdown_children(result)

        mock_close.assert_called_once_with(88888)

    def test_skips_rfb_close_when_no_hwnd(self):
        result = _fake_startup_result()  # rfb_hwnd defaults to 0
        with patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator.close_window") as mock_close:
            _shutdown_children(result)

        mock_close.assert_called_once_with(0)


class TestWritePidsFile:
    def test_writes_all_pids(self, tmp_path):
        result = _fake_startup_result()
        pids_path = tmp_path / "pids.ini"
        write_pids_file(pids_path, result)

        parser = configparser.ConfigParser()
        parser.read(str(pids_path), encoding="utf-8")

        assert parser.getint("pids", "primary_pid") == 100
        assert parser.getint("pids", "mfp_pid") == 200
        assert parser.getint("pids", "portrait_pid") == 300
        assert parser.getint("pids", "landscape_pid") == 400
        assert parser.getint("pids", "dashboard_pid") == 500
        assert parser.getint("pids", "genau_pid") == 600
        assert parser.getint("pids", "audio_pid") == 700


class TestMinimizeAllWindows:
    def test_minimizes_windows_for_all_visible_pids(self):
        result = _fake_startup_result()
        minimized_hwnds: list[int] = []

        def fake_find(pid):
            return pid * 10  # e.g. pid 100 -> hwnd 1000

        def fake_minimize(hwnd):
            minimized_hwnds.append(hwnd)

        with patch("fun_time.windows_bridge_orchestrator.find_window_by_pid", side_effect=fake_find), \
             patch("fun_time.windows_bridge_orchestrator.minimize_window", side_effect=fake_minimize):
            _minimize_all_windows(result)

        assert 1000 in minimized_hwnds  # primary
        assert 2000 in minimized_hwnds  # mfp
        assert 3000 in minimized_hwnds  # portrait
        assert 4000 in minimized_hwnds  # landscape
        assert 6000 in minimized_hwnds  # genau

    def test_skips_pids_without_windows(self):
        result = _fake_startup_result()
        minimized_hwnds: list[int] = []

        def fake_find(pid):
            return pid * 10 if pid != 200 else 0  # mfp has no window

        def fake_minimize(hwnd):
            minimized_hwnds.append(hwnd)

        with patch("fun_time.windows_bridge_orchestrator.find_window_by_pid", side_effect=fake_find), \
             patch("fun_time.windows_bridge_orchestrator.minimize_window", side_effect=fake_minimize):
            _minimize_all_windows(result)

        assert 2000 not in minimized_hwnds
        assert len(minimized_hwnds) == 4

    def test_called_during_integration_mode(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        def fake_sequence(**kwargs):
            return _fake_startup_result()

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", side_effect=fake_sequence), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_ahk_proc), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator._minimize_all_windows") as mock_minimize:

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        mock_minimize.assert_called_once()

    def test_not_called_outside_integration_mode(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_factory())
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        def fake_sequence(**kwargs):
            return _fake_startup_result()

        fake_ahk_proc = MagicMock()
        fake_ahk_proc.wait.return_value = 0

        with patch("fun_time.windows_bridge_orchestrator.run_startup_sequence", side_effect=fake_sequence), \
             patch("fun_time.windows_bridge_orchestrator.subprocess.Popen", return_value=fake_ahk_proc), \
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator._minimize_all_windows") as mock_minimize:

            run_python_orchestrated_bridge(
                manifest_path=manifest_path,
                ahk_exe="ahk.exe",
                hotkey_script="hotkeys.ahk",
                state_dir=tmp_path / "state",
                project_dir=tmp_path,
            )

        mock_minimize.assert_not_called()


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
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator._minimize_all_windows"):

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

        # Should have killed all 7 child processes
        assert 100 in killed_pids  # primary
        assert 200 in killed_pids  # mfp
        assert 300 in killed_pids  # portrait
        assert 400 in killed_pids  # landscape
        assert 500 in killed_pids  # dashboard
        assert 600 in killed_pids  # genau
        assert 700 in killed_pids  # audio

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
            primary_pid=100, mfp_pid=200, portrait_pid=300, landscape_pid=400,
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
             patch("fun_time.windows_bridge_orchestrator.kill_process_tree"), \
             patch("fun_time.windows_bridge_orchestrator._minimize_all_windows"):

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

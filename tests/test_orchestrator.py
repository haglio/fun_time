"""Tests for fun_time.orchestrator."""
from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fun_time.controller_manifest import (
    CONTROLLER_MANIFEST_FILENAME,
    build_controller_manifest,
    write_controller_manifest,
)
from fun_time.orchestrator import (
    build_parser,
    ensure_broker_running,
    is_broker_tray_running,
    is_broker_running,
    main,
    require_dir,
    require_file,
    resolve_vlc_http_password,
    run_controller,
    start_broker,
    validate_config,
    vlc_http_password_from_vlcrc,
)
from fun_time.config import load_config


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------

class TestBuildParser:
    def test_defaults(self):
        args = build_parser().parse_args([])
        assert args.config is None
        assert args.check is False

    def test_check_flag(self):
        args = build_parser().parse_args(["--check"])
        assert args.check is True

    def test_config_argument(self):
        args = build_parser().parse_args(["--config", "/path/config.json"])
        assert args.config == "/path/config.json"


# ---------------------------------------------------------------------------
# require_file / require_dir
# ---------------------------------------------------------------------------

class TestRequireFile:
    def test_passes_for_existing_file(self, tmp_path: Path):
        f = tmp_path / "exists.txt"
        f.touch()
        require_file(f)  # should not raise

    def test_raises_for_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Missing file"):
            require_file(tmp_path / "missing.exe")

    def test_raises_for_directory(self, tmp_path: Path):
        # A directory is not a file
        with pytest.raises(FileNotFoundError):
            require_file(tmp_path)


class TestRequireDir:
    def test_passes_for_existing_dir(self, tmp_path: Path):
        d = tmp_path / "mydir"
        d.mkdir()
        require_dir(d)  # should not raise

    def test_raises_for_missing_dir(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Missing directory"):
            require_dir(tmp_path / "nope")

    def test_raises_for_file(self, tmp_path: Path):
        f = tmp_path / "notadir.txt"
        f.touch()
        with pytest.raises(FileNotFoundError):
            require_dir(f)


# ---------------------------------------------------------------------------
# controller manifest
# ---------------------------------------------------------------------------

class TestControllerManifest:
    def test_returns_sections(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_manifest(cfg, "testpass")
        assert isinstance(result, dict)

    def test_vlc_pass_is_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_manifest(cfg, "mysecret")
        assert result["controller"]["vlc_pass"] == "mysecret"

    def test_vlc_ports_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_manifest(cfg, "pw")
        assert result["controller"]["vlc2_port"] == "8091"
        assert result["controller"]["vlc3_port"] == "8092"
        assert result["layout"]["main_monitor"] == "1"
        assert result["layout"]["secondary_monitor"] == "2"

    def test_runtime_section_includes_config_path(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_manifest(cfg, "pw")
        assert result["runtime"]["config_path"] == str(cfg.config_path)

    def test_primary_vlc_dirs_joined_with_pipe(self, tmp_path: Path, cfg_factory):
        extra = tmp_path / "extra_vlc"
        extra.mkdir()
        path = cfg_factory({"paths": {"primary_vlc_dirs": [
            str(tmp_path / "vlc_primary"),
            str(extra),
        ]}})
        cfg = load_config(path)
        manifest = build_controller_manifest(cfg, "pw")
        joined = manifest["media"]["primary_vlc_sources"]
        assert str(tmp_path / "vlc_primary") in joined
        assert str(extra) in joined
        assert "|" in joined

    def test_portrait_and_landscape_dirs_joined_with_pipe(self, tmp_path: Path, cfg_factory):
        portrait_extra = tmp_path / "portrait_extra"
        landscape_extra = tmp_path / "landscape_extra"
        portrait_extra.mkdir()
        landscape_extra.mkdir()
        path = cfg_factory({"paths": {
            "portrait_dirs": [str(tmp_path / "portrait"), str(portrait_extra)],
            "landscape_dirs": [str(tmp_path / "landscape"), str(landscape_extra)],
        }})
        cfg = load_config(path)
        manifest = build_controller_manifest(cfg, "pw")
        assert str(tmp_path / "portrait") in manifest["media"]["portrait_dirs"]
        assert str(portrait_extra) in manifest["media"]["portrait_dirs"]
        assert "|" in manifest["media"]["portrait_dirs"]
        assert str(tmp_path / "landscape") in manifest["media"]["landscape_dirs"]
        assert str(landscape_extra) in manifest["media"]["landscape_dirs"]
        assert "|" in manifest["media"]["landscape_dirs"]

    def test_robot_hand_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_manifest(cfg, "pw")
        assert result["modules"]["robot_hand_module"] == "fun_time.robot_hand.app"

    def test_audio_companion_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_manifest(cfg, "pw")
        assert result["modules"]["audio_module"] == "fun_time.audio_companion_app"

    def test_dashboard_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_manifest(cfg, "pw")
        assert result["modules"]["dashboard_module"] == "fun_time.dashboard_app"

    def test_chrome_overlay_paths_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_controller_manifest(cfg, "pw")
        assert result["chrome_overlay"]["shortcut_path"] == str(cfg.chrome_overlay.shortcut_path)
        assert result["chrome_overlay"]["manifest_file"] == str(cfg.chrome_overlay_manifest_file)

    def test_write_controller_manifest_writes_expected_ini(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        manifest_path = write_controller_manifest(cfg, "pw", tmp_path / CONTROLLER_MANIFEST_FILENAME)

        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(manifest_path, encoding="utf-8")

        assert manifest_path.name == CONTROLLER_MANIFEST_FILENAME
        assert parser["runtime"]["project_dir"] == str(cfg.project_dir)
        assert parser["controller"]["vlc_pass"] == "pw"
        assert parser["modules"]["audio_module"] == "fun_time.audio_companion_app"
        assert parser["modules"]["dashboard_module"] == "fun_time.dashboard_app"
        assert parser["commands"]["robot_hand_enabled_file"] == str(cfg.robot_hand_enabled_file)
        assert parser["commands"]["robot_hand_paused_file"] == str(cfg.robot_hand_paused_file)
        assert parser["commands"]["audio_paused_file"] == str(cfg.audio_paused_file)
        assert parser["commands"]["dashboard_state_file"] == str(cfg.paths.state_dir / "dashboard_state.ini")
        assert parser["commands"]["dashboard_cmd_file"] == str(cfg.paths.state_dir / "dashboard_cmd.txt")
        assert parser["chrome_overlay"]["manifest_file"] == str(cfg.chrome_overlay_manifest_file)


# ---------------------------------------------------------------------------
# validate_config – only tests logic that doesn't require real binaries
# ---------------------------------------------------------------------------

class TestValidateConfig:
    def _make_config_with_stubs(self, cfg_path: Path, tmp_path: Path):
        """Load config and create all stub files validate_config needs."""
        cfg = load_config(cfg_path)
        # Create stub executable files
        for p in (cfg.paths.vlc_exe, cfg.paths.mfp_exe, cfg.paths.ahk_exe, cfg.paths.python_exe):
            p.touch()
        # Create AHK script
        (cfg.project_dir / "controller.ahk").touch()
        # Create Python entry points
        broker_py = cfg.project_dir / "fun_time" / "broker_app.py"
        broker_py.parent.mkdir(parents=True, exist_ok=True)
        broker_py.touch()
        rh_py = cfg.project_dir / "fun_time" / "robot_hand" / "app.py"
        rh_py.parent.mkdir(parents=True, exist_ok=True)
        rh_py.touch()
        ac_py = cfg.project_dir / "fun_time" / "audio_companion_app.py"
        ac_py.touch()
        return cfg

    def test_raises_when_vlc_exe_missing(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        # Do NOT create vlc_exe stub
        with pytest.raises(FileNotFoundError):
            validate_config(cfg)

    def test_raises_when_chrome_shortcut_missing_if_overlay_enabled(self, cfg_factory: Path):
        cfg = load_config(
            cfg_factory(
                {
                    "chrome_overlay": {
                        "enabled": True,
                        "shortcut_path": "missing_chrome.lnk",
                    }
                }
            )
        )
        with pytest.raises(FileNotFoundError):
            validate_config(cfg)


class TestBrokerHelpers:
    def test_is_broker_running_false_when_probe_finds_nothing(self):
        completed = subprocess_result(stdout="", returncode=0)
        with patch("fun_time.orchestrator.sys.platform", "win32"), \
             patch("fun_time.orchestrator.subprocess.run", return_value=completed):
            assert is_broker_running() is False

    def test_is_broker_running_true_when_probe_finds_process(self):
        completed = subprocess_result(stdout="RUNNING\r\n", returncode=0)
        with patch("fun_time.orchestrator.sys.platform", "win32"), \
             patch("fun_time.orchestrator.subprocess.run", return_value=completed):
            assert is_broker_running() is True

    def test_is_broker_tray_running_false_when_probe_finds_nothing(self):
        completed = subprocess_result(stdout="", returncode=0)
        with patch("fun_time.orchestrator.sys.platform", "win32"), \
             patch("fun_time.orchestrator.subprocess.run", return_value=completed):
            assert is_broker_tray_running() is False

    def test_is_broker_tray_running_true_when_probe_finds_process(self):
        completed = subprocess_result(stdout="RUNNING\r\n", returncode=0)
        with patch("fun_time.orchestrator.sys.platform", "win32"), \
             patch("fun_time.orchestrator.subprocess.run", return_value=completed):
            assert is_broker_tray_running() is True

    def test_start_broker_launches_tray_launcher(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.sys.platform", "win32"), \
             patch("fun_time.orchestrator.subprocess.Popen") as popen:
            start_broker(cfg, logger)

        popen.assert_called_once()
        command = popen.call_args.args[0]
        assert command == [
            "wscript.exe",
            str(cfg.project_dir / "launch_broker_tray.vbs"),
        ]

    def test_ensure_broker_running_starts_when_missing(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.is_broker_running", side_effect=[False, False, False, True]) as broker_probe, \
             patch("fun_time.orchestrator.is_broker_tray_running", side_effect=[True]) as tray_probe, \
             patch("fun_time.orchestrator.start_broker") as starter, \
             patch("fun_time.orchestrator.time.sleep") as sleeper:
            result = ensure_broker_running(cfg, logger, attempts=3, delay_seconds=0.01)

        assert result is True
        assert broker_probe.call_count == 4
        assert tray_probe.call_count == 1
        starter.assert_called_once_with(cfg, logger)
        sleeper.assert_called()

    def test_ensure_broker_running_skips_start_when_present(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.is_broker_running", return_value=True) as broker_probe, \
             patch("fun_time.orchestrator.is_broker_tray_running", return_value=True) as tray_probe, \
             patch("fun_time.orchestrator.start_broker") as starter:
            result = ensure_broker_running(cfg, logger)

        assert result is True
        broker_probe.assert_called_once_with()
        tray_probe.assert_called_once_with()
        starter.assert_not_called()

    def test_main_ensures_mfp_serial_port_before_broker_and_controller(self, cfg_path: Path):
        with patch("fun_time.orchestrator.configure_logging", return_value=MagicMock()), \
             patch("fun_time.orchestrator.install_exception_logging"), \
             patch("fun_time.orchestrator.ensure_runtime_files"), \
             patch("fun_time.orchestrator.validate_config"), \
             patch("fun_time.orchestrator.ensure_mfp_serial_port") as ensure_mfp_port, \
             patch("fun_time.orchestrator.ensure_mfp_vlc_endpoint") as ensure_mfp_vlc_endpoint, \
             patch("fun_time.orchestrator.ensure_broker_running") as ensure_broker, \
             patch("fun_time.orchestrator.run_controller", return_value=0) as run_controller:
            result = main(["--config", str(cfg_path)])

        assert result == 0
        ensure_mfp_port.assert_called_once()
        ensure_mfp_vlc_endpoint.assert_called_once()
        ensure_broker.assert_called_once()
        run_controller.assert_called_once()

    def test_ensure_broker_running_starts_when_service_exists_but_tray_is_missing(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.is_broker_running", side_effect=[True, True]) as broker_probe, \
             patch("fun_time.orchestrator.is_broker_tray_running", side_effect=[False, True]) as tray_probe, \
             patch("fun_time.orchestrator.start_broker") as starter, \
             patch("fun_time.orchestrator.time.sleep") as sleeper:
            result = ensure_broker_running(cfg, logger, attempts=1, delay_seconds=0.01)

        assert result is True
        assert broker_probe.call_count == 2
        assert tray_probe.call_count == 2
        starter.assert_called_once_with(cfg, logger)
        sleeper.assert_called_once()

    def test_ensure_broker_running_starts_when_tray_exists_but_service_is_missing(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.is_broker_running", side_effect=[False, True]) as broker_probe, \
             patch("fun_time.orchestrator.is_broker_tray_running", side_effect=[True, True]) as tray_probe, \
             patch("fun_time.orchestrator.start_broker") as starter, \
             patch("fun_time.orchestrator.time.sleep") as sleeper:
            result = ensure_broker_running(cfg, logger, attempts=1, delay_seconds=0.01)

        assert result is True
        assert broker_probe.call_count == 2
        assert tray_probe.call_count == 1
        starter.assert_called_once_with(cfg, logger)
        sleeper.assert_called_once()


class TestRunController:
    def test_prefers_vlcrc_http_password(self):
        with patch("fun_time.orchestrator.vlc_http_password_from_vlcrc", return_value="from-vlcrc"), \
             patch("fun_time.orchestrator.secrets.token_hex", return_value="abc123"):
            assert resolve_vlc_http_password() == "from-vlcrc"

    def test_falls_back_to_generated_http_password(self):
        with patch("fun_time.orchestrator.vlc_http_password_from_vlcrc", return_value=None), \
             patch("fun_time.orchestrator.secrets.token_hex", return_value="abc123"):
            assert resolve_vlc_http_password() == "fun_time_abc123"

    def test_uses_manifest_path_for_controller_launch(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.resolve_vlc_http_password", return_value="pw-from-config"), \
             patch("fun_time.orchestrator.write_controller_manifest", return_value=cfg.paths.state_dir / CONTROLLER_MANIFEST_FILENAME) as writer, \
             patch("fun_time.orchestrator.subprocess.run") as run:
            run.return_value.returncode = 0
            result = run_controller(cfg, logger)

        assert result == 0
        writer.assert_called_once_with(cfg, "pw-from-config")
        command = run.call_args.args[0]
        assert command == [
            str(cfg.paths.ahk_exe),
            str(cfg.project_dir / "controller.ahk"),
            str(cfg.paths.state_dir / CONTROLLER_MANIFEST_FILENAME),
        ]


def subprocess_result(*, stdout: str, returncode: int):
    mock = MagicMock()
    mock.stdout = stdout
    mock.returncode = returncode
    return mock

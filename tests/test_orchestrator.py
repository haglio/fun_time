"""Tests for fun_time.orchestrator."""
from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    build_windows_bridge_manifest,
    write_windows_bridge_manifest,
)
from fun_time.orchestrator import (
    build_parser,
    ensure_broker_running,
    is_broker_running,
    main,
    require_dir,
    require_file,
    resolve_vlc_http_password,
    run_windows_bridge,
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
# windows bridge manifest
# ---------------------------------------------------------------------------

class TestControllerManifest:
    def test_returns_sections(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "testpass")
        assert isinstance(result, dict)

    def test_vlc_pass_is_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "mysecret")
        assert result["vlc"]["vlc_pass"] == "mysecret"

    def test_vlc_ports_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["vlc"]["vlc2_port"] == "8091"
        assert result["vlc"]["vlc3_port"] == "8092"
        assert result["layout"]["main_monitor"] == "1"
        assert result["layout"]["secondary_monitor"] == "2"

    def test_runtime_section_includes_config_path(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["runtime"]["config_path"] == str(cfg.config_path)

    def test_nau_library_dirs_joined_with_pipe(self, tmp_path: Path, cfg_factory):
        extra = tmp_path / "extra"
        extra.mkdir()
        path = cfg_factory({"paths": {"nau_library_dirs": [
            str(tmp_path / "nau_library"),
            str(extra),
        ]}})
        cfg = load_config(path)
        manifest = build_windows_bridge_manifest(cfg, "pw")
        joined = manifest["media"]["nau_library_sources"]
        assert str(tmp_path / "nau_library") in joined
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
        manifest = build_windows_bridge_manifest(cfg, "pw")
        assert str(tmp_path / "portrait") in manifest["media"]["portrait_dirs"]
        assert str(portrait_extra) in manifest["media"]["portrait_dirs"]
        assert "|" in manifest["media"]["portrait_dirs"]
        assert str(tmp_path / "landscape") in manifest["media"]["landscape_dirs"]
        assert str(landscape_extra) in manifest["media"]["landscape_dirs"]
        assert "|" in manifest["media"]["landscape_dirs"]

    def test_genau_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["modules"]["genau_module"] == "genau"

    def test_audio_companion_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["modules"]["audio_module"] == "fun_time.audio_companion_app"

    def test_dashboard_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["modules"]["dashboard_module"] == "fun_time.dashboard_app"

    def test_lock_hud_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["modules"]["lock_hud_module"] == "fun_time.lock_hud_app"

    def test_dashboard_enabled_defaults_true(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["dashboard"]["enabled"] == "1"

    def test_dashboard_enabled_can_be_disabled_for_integration(self, cfg_path: Path, monkeypatch):
        cfg = load_config(cfg_path)
        monkeypatch.setenv("FUN_TIME_DISABLE_DASHBOARD", "1")
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["dashboard"]["enabled"] == "0"

    def test_media_actions_module_removed_from_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert "media_actions_module" not in result["modules"]

    def test_windows_bridge_lock_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert "windows_bridge_lock_module" not in result["modules"]

    def test_removed_modules_are_not_in_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert "windows_bridge_runtime_flow_module" not in result["modules"]
        assert "windows_bridge_vlc_actions_module" not in result["modules"]

    def test_dead_app_modules_absent_from_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        for dead_key in (
            "windows_bridge_window_layout_module",
            "windows_bridge_random_favs_browser_module",
            "windows_bridge_startup_module",
            "windows_bridge_dashboard_bridge_module",
        ):
            assert dead_key not in result["modules"]

    def test_random_favs_browser_paths_included(self, cfg_factory):
        cfg = load_config(cfg_factory({"random_favs_browser": {"enabled": True}}))
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["random_favs_browser"]["enabled"] == "1"
        assert result["random_favs_browser"]["shortcut_path"] == str(cfg.random_favs_browser.shortcut_path)
        assert result["random_favs_browser"]["manifest_file"] == str(cfg.random_favs_browser_manifest_file)

    def test_regen_section_included(self, cfg_factory, tmp_path: Path):
        path = cfg_factory({"regen": {
            "media_root": str(tmp_path / "media"),
            "metadata_root": str(tmp_path / "meta"),
        }})
        cfg = load_config(path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["regen"]["generate_video_url"] == "https://example.com/video"
        assert result["regen"]["generate_image_url"] == "https://example.com/create"
        assert result["regen"]["media_root"] == str(tmp_path / "media")
        assert result["regen"]["metadata_root"] == str(tmp_path / "meta")

    def test_regen_roots_blank_when_unset(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, "pw")
        assert result["regen"]["media_root"] == ""
        assert result["regen"]["metadata_root"] == ""

    def test_write_windows_bridge_manifest_writes_expected_ini(self, cfg_factory, tmp_path: Path):
        cfg = load_config(cfg_factory({"random_favs_browser": {"enabled": True}}))
        manifest_path = write_windows_bridge_manifest(cfg, "pw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(manifest_path, encoding="utf-8")

        assert manifest_path.name == WINDOWS_BRIDGE_MANIFEST_FILENAME
        assert parser["runtime"]["project_dir"] == str(cfg.project_dir)
        assert parser["vlc"]["vlc_pass"] == "pw"
        assert parser["modules"]["audio_module"] == "fun_time.audio_companion_app"
        assert parser["modules"]["dashboard_module"] == "fun_time.dashboard_app"
        assert "windows_bridge_lock_module" not in parser["modules"]
        assert "windows_bridge_runtime_flow_module" not in parser["modules"]
        assert "windows_bridge_window_layout_module" not in parser["modules"]
        assert "windows_bridge_vlc_actions_module" not in parser["modules"]
        assert "windows_bridge_random_favs_browser_module" not in parser["modules"]
        assert "windows_bridge_startup_module" not in parser["modules"]
        assert "windows_bridge_dashboard_bridge_module" not in parser["modules"]
        assert parser["commands"]["genau_paused_file"] == str(cfg.genau_paused_file)
        assert parser["commands"]["audio_paused_file"] == str(cfg.audio_paused_file)
        assert parser["commands"]["dashboard_state_file"] == str(cfg.paths.state_dir / "dashboard_state.ini")
        assert parser["commands"]["dashboard_cmd_file"] == str(cfg.paths.state_dir / "dashboard_cmd.txt")
        assert parser["random_favs_browser"]["enabled"] == "1"
        assert parser["random_favs_browser"]["manifest_file"] == str(cfg.random_favs_browser_manifest_file)


# ---------------------------------------------------------------------------
# validate_config – only tests logic that doesn't require real binaries
# ---------------------------------------------------------------------------

class TestValidateConfig:
    def _make_config_with_stubs(self, cfg_path: Path, tmp_path: Path):
        """Load config and create all stub files validate_config needs."""
        cfg = load_config(cfg_path)
        # Create stub executable files
        for p in (cfg.paths.vlc_exe, cfg.paths.ahk_exe, cfg.paths.python_exe):
            p.touch()
        # Create AHK scripts
        (cfg.project_dir / "windows_bridge_hotkeys.ahk").touch()
        # Create Python entry points
        rh_py = cfg.project_dir / "fun_time" / "genau" / "app.py"
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

    def test_raises_when_random_favs_browser_shortcut_missing_if_enabled(self, cfg_factory: Path):
        cfg = load_config(
            cfg_factory(
                {
                    "random_favs_browser": {
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

    def test_start_broker_launches_configured_tray_launcher(self, cfg_factory, tmp_path: Path):
        launcher = tmp_path / "osr2_broker" / "launch_broker_tray.vbs"
        launcher.parent.mkdir()
        launcher.touch()
        cfg = load_config(cfg_factory({"paths": {"broker_tray_launcher": str(launcher)}}))
        logger = MagicMock()

        with patch("fun_time.orchestrator.sys.platform", "win32"), \
             patch("fun_time.orchestrator.subprocess.Popen") as popen, \
             patch("fun_time.orchestrator.orchestrator_broker.broker_launch_kwargs", return_value={"creationflags": 1}):
            start_broker(cfg, logger)

        popen.assert_called_once()
        command = popen.call_args.args[0]
        assert command == ["wscript.exe", str(launcher)]
        assert popen.call_args.kwargs.get("cwd") == launcher.parent
        # The broker must outlive an integration run's job object.
        assert popen.call_args.kwargs.get("creationflags") == 1

    def test_start_broker_skips_when_launcher_not_configured(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.sys.platform", "win32"), \
             patch("fun_time.orchestrator.subprocess.Popen") as popen:
            result = start_broker(cfg, logger)

        popen.assert_not_called()
        assert result is None

    def test_ensure_broker_running_starts_when_missing(self, cfg_path: Path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.is_broker_running", side_effect=[False, False, True]) as broker_probe, \
             patch("fun_time.orchestrator.start_broker") as starter, \
             patch("fun_time.orchestrator.time.sleep") as sleeper:
            result = ensure_broker_running(cfg, logger, attempts=3, delay_seconds=0.01)

        assert result is True
        assert broker_probe.call_count == 3
        starter.assert_called_once_with(cfg, logger)
        sleeper.assert_called()

    def test_ensure_broker_running_skips_poll_when_launch_not_configured(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.is_broker_running", return_value=False), \
             patch("fun_time.orchestrator.start_broker", return_value=None), \
             patch("fun_time.orchestrator.time.sleep") as sleeper:
            result = ensure_broker_running(cfg, logger)

        assert result is False
        sleeper.assert_not_called()

    def test_ensure_broker_running_skips_start_when_present(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.is_broker_running", return_value=True) as broker_probe, \
             patch("fun_time.orchestrator.start_broker") as starter:
            result = ensure_broker_running(cfg, logger)

        assert result is True
        broker_probe.assert_called_once_with()
        starter.assert_not_called()

    def test_main_ensures_broker_before_bridge(self, cfg_path: Path):
        with patch("fun_time.orchestrator.configure_logging", return_value=MagicMock()), \
             patch("fun_time.orchestrator.install_exception_logging"), \
             patch("fun_time.single_instance.try_acquire_mutex", return_value=42), \
             patch("fun_time.orchestrator.ensure_runtime_files"), \
             patch("fun_time.orchestrator.validate_config"), \
             patch("fun_time.orchestrator.ensure_broker_running") as ensure_broker, \
             patch("fun_time.orchestrator.run_windows_bridge", return_value=0) as run_windows_bridge:
            result = main(["--config", str(cfg_path)])

        assert result == 0
        ensure_broker.assert_called_once()
        run_windows_bridge.assert_called_once()



class TestRunController:
    def test_prefers_vlcrc_http_password(self):
        with patch("fun_time.orchestrator.vlc_http_password_from_vlcrc", return_value="from-vlcrc"), \
             patch("fun_time.orchestrator.secrets.token_hex", return_value="abc123"):
            assert resolve_vlc_http_password() == "from-vlcrc"

    def test_falls_back_to_generated_http_password(self):
        with patch("fun_time.orchestrator.vlc_http_password_from_vlcrc", return_value=None), \
             patch("fun_time.orchestrator.secrets.token_hex", return_value="abc123"):
            assert resolve_vlc_http_password() == "fun_time_abc123"

    def test_uses_manifest_path_for_bridge_launch(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.resolve_vlc_http_password", return_value="pw-from-config"), \
             patch("fun_time.orchestrator.write_windows_bridge_manifest", return_value=cfg.paths.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME) as writer, \
             patch("fun_time.orchestrator.run_python_orchestrated_bridge", return_value=0) as bridge:
            result = run_windows_bridge(cfg, logger)

        assert result == 0
        writer.assert_called_once_with(cfg, "pw-from-config")
        bridge.assert_called_once()
        call_kwargs = bridge.call_args.kwargs
        assert call_kwargs["manifest_path"] == cfg.paths.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME
        assert call_kwargs["ahk_exe"] == str(cfg.paths.ahk_exe)
        assert call_kwargs["hotkey_script"] == str(cfg.project_dir / "windows_bridge_hotkeys.ahk")
        assert call_kwargs["state_dir"] == cfg.paths.state_dir
        assert call_kwargs["project_dir"] == cfg.project_dir


def subprocess_result(*, stdout: str, returncode: int):
    mock = MagicMock()
    mock.stdout = stdout
    mock.returncode = returncode
    return mock


# --- vlc_http_password_from_vlcrc ---


class TestVlcHttpPasswordFromVlcrc:
    def test_reads_password_from_vlcrc(self, tmp_path: Path, monkeypatch):
        vlcrc = tmp_path / "vlc" / "vlcrc"
        vlcrc.parent.mkdir(parents=True)
        vlcrc.write_text("# comment\n\nhttp-password=mysecret\n", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert vlc_http_password_from_vlcrc() == "mysecret"

    def test_returns_none_when_appdata_unset(self, monkeypatch):
        monkeypatch.delenv("APPDATA", raising=False)
        assert vlc_http_password_from_vlcrc() is None

    def test_returns_none_when_vlcrc_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert vlc_http_password_from_vlcrc() is None

    def test_returns_none_when_no_password_line(self, tmp_path: Path, monkeypatch):
        vlcrc = tmp_path / "vlc" / "vlcrc"
        vlcrc.parent.mkdir(parents=True)
        vlcrc.write_text("# just comments\nsome-other-setting=value\n", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert vlc_http_password_from_vlcrc() is None

    def test_skips_comment_and_blank_lines(self, tmp_path: Path, monkeypatch):
        vlcrc = tmp_path / "vlc" / "vlcrc"
        vlcrc.parent.mkdir(parents=True)
        vlcrc.write_text("# http-password=wrong\n\nhttp-password=correct\n", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert vlc_http_password_from_vlcrc() == "correct"

    def test_returns_none_for_empty_password_value(self, tmp_path: Path, monkeypatch):
        vlcrc = tmp_path / "vlc" / "vlcrc"
        vlcrc.parent.mkdir(parents=True)
        vlcrc.write_text("http-password=\n", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert vlc_http_password_from_vlcrc() is None


# --- main() --check flag ---


class TestMainCheckFlag:
    def test_main_check_returns_zero_without_launching_bridge(self, cfg_path: Path):
        with patch("fun_time.orchestrator.configure_logging", return_value=MagicMock()), \
             patch("fun_time.orchestrator.install_exception_logging"), \
             patch("fun_time.single_instance.try_acquire_mutex", return_value=42), \
             patch("fun_time.orchestrator.ensure_runtime_files"), \
             patch("fun_time.orchestrator.validate_config"), \
             patch("fun_time.orchestrator.run_windows_bridge") as run_bridge:
            result = main(["--config", str(cfg_path), "--check"])

        assert result == 0
        run_bridge.assert_not_called()


class TestOrchestratorSingleInstance:
    def test_shows_message_and_exits_when_already_running(self, cfg_path: Path):
        with patch("fun_time.orchestrator.configure_logging", return_value=MagicMock()), \
             patch("fun_time.orchestrator.install_exception_logging"), \
             patch("fun_time.single_instance.try_acquire_mutex", return_value=None), \
             patch("fun_time.single_instance.show_already_running_message") as show_msg, \
             patch("fun_time.orchestrator.run_windows_bridge") as run_bridge:
            result = main(["--config", str(cfg_path)])

        assert result == 1
        show_msg.assert_called_once()
        assert "already running" in show_msg.call_args[0][0]
        run_bridge.assert_not_called()


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
    run_windows_bridge,
    start_broker,
    validate_config,
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
        result = build_windows_bridge_manifest(cfg)
        assert isinstance(result, dict)

    def test_no_vlc_section(self, cfg_path: Path):
        """The native satellites replaced the VLC ones: there is no VLC HTTP
        port or password to publish, so the manifest carries no [vlc] section."""
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert "vlc" not in result

    def test_layout_monitors_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["layout"]["main_monitor"] == "1"
        assert result["layout"]["secondary_monitor"] == "2"

    def test_runtime_section_includes_config_path(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["runtime"]["config_path"] == str(cfg.config_path)

    def test_satellite_module_in_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["modules"]["satellite_module"] == "satellite"

    def test_satellite_file_quartet_in_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        commands = build_windows_bridge_manifest(cfg)["commands"]
        state = cfg.paths.state_dir
        for side in ("portrait", "landscape"):
            assert commands[f"{side}_cmd_file"] == str(state / f"{side}_cmd.txt")
            assert commands[f"{side}_paused_file"] == str(state / f"{side}_paused.txt")
            assert commands[f"{side}_status_file"] == str(state / f"{side}_status.txt")
            assert commands[f"{side}_playlist_file"] == str(state / f"{side}_playlist.tsv")

    def test_nau_library_dirs_joined_with_pipe(self, tmp_path: Path, cfg_factory):
        extra = tmp_path / "extra"
        extra.mkdir()
        path = cfg_factory({"paths": {"nau_library_dirs": [
            str(tmp_path / "nau_library"),
            str(extra),
        ]}})
        cfg = load_config(path)
        manifest = build_windows_bridge_manifest(cfg)
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
        manifest = build_windows_bridge_manifest(cfg)
        assert str(tmp_path / "portrait") in manifest["media"]["portrait_dirs"]
        assert str(portrait_extra) in manifest["media"]["portrait_dirs"]
        assert "|" in manifest["media"]["portrait_dirs"]
        assert str(tmp_path / "landscape") in manifest["media"]["landscape_dirs"]
        assert str(landscape_extra) in manifest["media"]["landscape_dirs"]
        assert "|" in manifest["media"]["landscape_dirs"]

    def test_genau_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["modules"]["genau_module"] == "genau"

    def test_audio_companion_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["modules"]["audio_module"] == "fun_time.audio_companion_app"

    def test_dashboard_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["modules"]["dashboard_module"] == "fun_time.dashboard_app"

    def test_dashboard_enabled_defaults_true(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["dashboard"]["enabled"] == "1"

    def test_dashboard_enabled_can_be_disabled_for_integration(self, cfg_path: Path, monkeypatch):
        cfg = load_config(cfg_path)
        monkeypatch.setenv("FUN_TIME_DISABLE_DASHBOARD", "1")
        result = build_windows_bridge_manifest(cfg)
        assert result["dashboard"]["enabled"] == "0"

    def test_media_actions_module_removed_from_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert "media_actions_module" not in result["modules"]

    def test_windows_bridge_lock_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert "windows_bridge_lock_module" not in result["modules"]

    def test_removed_modules_are_not_in_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert "windows_bridge_runtime_flow_module" not in result["modules"]
        assert "windows_bridge_vlc_actions_module" not in result["modules"]

    def test_dead_app_modules_absent_from_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        for dead_key in (
            "windows_bridge_window_layout_module",
            "windows_bridge_random_favs_browser_module",
            "windows_bridge_startup_module",
            "windows_bridge_dashboard_bridge_module",
        ):
            assert dead_key not in result["modules"]

    def test_random_favs_browser_paths_included(self, cfg_factory):
        cfg = load_config(cfg_factory({"random_favs_browser": {"enabled": True}}))
        result = build_windows_bridge_manifest(cfg)
        assert result["random_favs_browser"]["enabled"] == "1"
        assert result["random_favs_browser"]["shortcut_path"] == str(cfg.random_favs_browser.shortcut_path)
        assert result["random_favs_browser"]["manifest_file"] == str(cfg.random_favs_browser_manifest_file)

    def test_provider_regen_section_included(self, cfg_factory, tmp_path: Path):
        path = cfg_factory({"provider_regen": {
            "media_root": str(tmp_path / "media"),
            "metadata_root": str(tmp_path / "meta"),
        }})
        cfg = load_config(path)
        result = build_windows_bridge_manifest(cfg)
        assert result["provider_regen"]["generate_video_url"] == "https://example.com/video"
        assert result["provider_regen"]["generate_image_url"] == "https://example.com/create"
        assert result["provider_regen"]["media_root"] == str(tmp_path / "media")
        assert result["provider_regen"]["metadata_root"] == str(tmp_path / "meta")

    def test_provider_regen_roots_blank_when_unset(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["provider_regen"]["media_root"] == ""
        assert result["provider_regen"]["metadata_root"] == ""

    def test_write_windows_bridge_manifest_writes_expected_ini(self, cfg_factory, tmp_path: Path):
        cfg = load_config(cfg_factory({"random_favs_browser": {"enabled": True}}))
        manifest_path = write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(manifest_path, encoding="utf-8")

        assert manifest_path.name == WINDOWS_BRIDGE_MANIFEST_FILENAME
        assert parser["runtime"]["project_dir"] == str(cfg.project_dir)
        assert "vlc" not in parser
        # The native satellites are wired through their command/paused/status/playlist
        # files, so the written INI carries the quartet instead of a VLC port/pass.
        assert parser["modules"]["satellite_module"] == "satellite"
        assert parser["commands"]["portrait_playlist_file"] == str(cfg.paths.state_dir / "portrait_playlist.tsv")
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
    def _make_config_with_stubs(self, cfg_path: Path):
        """Load config and stub the executables validate_config looks for.

        Only the exe paths need stubbing — they point inside the test's
        tmp_path.  The rest of what validate_config checks is addressed off
        ``config.project_dir``, which is always the real package directory
        (``config.PROJECT_DIR``), so those files are genuinely on disk.  A
        test must never create them: writing under project_dir drops files
        into the repo itself.
        """
        cfg = load_config(cfg_path)
        for p in (cfg.paths.ahk_exe, cfg.paths.python_exe):
            p.touch()
        return cfg

    def test_validates_on_a_machine_without_vlc(self, cfg_path: Path):
        """VLC is not a dependency any more.

        The native satellites replaced the VLC ones, so nothing launches VLC and
        startup must not refuse to run on a machine that has never installed it.
        The shared config fixture carries no ``paths.vlc_exe`` and stubs no
        vlc.exe, so a clean validate proves neither is still demanded.
        """
        cfg = self._make_config_with_stubs(cfg_path)

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
    def test_uses_manifest_path_for_bridge_launch(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        logger = MagicMock()

        with patch("fun_time.orchestrator.write_windows_bridge_manifest", return_value=cfg.paths.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME) as writer, \
             patch("fun_time.orchestrator.run_python_orchestrated_bridge", return_value=0) as bridge:
            result = run_windows_bridge(cfg, logger)

        assert result == 0
        # The native satellites need no VLC HTTP password, so the manifest is
        # written from the config alone.
        writer.assert_called_once_with(cfg)
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


"""Tests for fun_time.config."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fun_time.loopback_server import LOOPBACK_PORT
from fun_time.config import (
    ProjectConfig,
    _require_dict,
    _require_value,
    _resolve_path,
    load_config,
)


# ---------------------------------------------------------------------------
# _resolve_path
# ---------------------------------------------------------------------------

class TestResolvePath:
    def test_absolute_path_returned_unchanged(self, tmp_path: Path):
        abs_path = tmp_path / "some" / "file.exe"
        result = _resolve_path(tmp_path, str(abs_path))
        assert result == abs_path

    def test_relative_path_resolved_against_project_dir(self, tmp_path: Path):
        result = _resolve_path(tmp_path, "sub/thing.exe")
        assert result == (tmp_path / "sub" / "thing.exe").resolve()

    def test_simple_filename_resolves(self, tmp_path: Path):
        result = _resolve_path(tmp_path, "favs.csv")
        assert result == (tmp_path / "favs.csv").resolve()


# ---------------------------------------------------------------------------
# _require_dict
# ---------------------------------------------------------------------------

class TestRequireDict:
    def test_returns_nested_dict(self, tmp_path: Path):
        parent = {"section": {"key": "val"}}
        result = _require_dict(parent, "section", tmp_path)
        assert result == {"key": "val"}

    def test_raises_on_missing_key(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Missing required config section"):
            _require_dict({}, "missing", tmp_path)

    def test_raises_on_wrong_type(self, tmp_path: Path):
        parent = {"section": "not-a-dict"}
        with pytest.raises(TypeError, match="Expected object"):
            _require_dict(parent, "section", tmp_path)

    def test_dotted_context_in_error(self, tmp_path: Path):
        with pytest.raises(ValueError, match=r"config\.paths"):
            _require_dict({}, "paths", tmp_path, context="config")


# ---------------------------------------------------------------------------
# _require_value
# ---------------------------------------------------------------------------

class TestRequireValue:
    def test_returns_value(self, tmp_path: Path):
        parent = {"port": 8080}
        assert _require_value(parent, "port", tmp_path, "config") == 8080

    def test_raises_on_missing(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Missing required config value"):
            _require_value({}, "port", tmp_path, "config")

    def test_dotted_name_in_message(self, tmp_path: Path):
        with pytest.raises(ValueError, match=r"config\.port"):
            _require_value({}, "port", tmp_path, "config")


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_loads_valid_config(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert isinstance(cfg, ProjectConfig)

    def test_raises_on_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.json")

    def test_raises_on_missing_section(self, tmp_path: Path):
        cfg_file = tmp_path / "bad.json"
        cfg_file.write_text(json.dumps({"paths": {}}), encoding="utf-8")
        with pytest.raises((ValueError, KeyError, TypeError)):
            load_config(cfg_file)

    def test_raises_when_paths_is_not_dict(self, tmp_path: Path):
        cfg_file = tmp_path / "bad.json"
        cfg_file.write_text(json.dumps({"paths": "nope"}), encoding="utf-8")
        with pytest.raises(TypeError):
            load_config(cfg_file)

    def test_raises_when_nau_library_dirs_empty(self, cfg_factory):
        path = cfg_factory({"paths": {"nau_library_dirs": []}})
        with pytest.raises(ValueError, match="nau_library_dirs"):
            load_config(path)

    def test_nau_library_dirs_not_a_list(self, tmp_path: Path, cfg_factory):
        path = cfg_factory({"paths": {"nau_library_dirs": "not-a-list"}})
        with pytest.raises(TypeError, match="nau_library_dirs"):
            load_config(path)

    def test_loads_paths_correctly(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.paths.ahk_exe == tmp_path / "ahk.exe"
        assert cfg.paths.state_dir == (tmp_path / "state").resolve()

    def test_loads_layout(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.layout.main_monitor == 1
        assert cfg.layout.secondary_monitor == 2

    def test_loads_audio_companion(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.audio_companion.host == "127.0.0.1"
        assert cfg.audio_companion.port == 50556

    def test_broker_tray_launcher_defaults_to_none(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.paths.broker_tray_launcher is None

    def test_broker_tray_launcher_resolves_when_set(self, cfg_factory, tmp_path: Path):
        launcher = tmp_path / "launch_broker_tray.vbs"
        path = cfg_factory({"paths": {"broker_tray_launcher": str(launcher)}})
        cfg = load_config(path)
        assert cfg.paths.broker_tray_launcher == launcher

    def test_missing_random_favs_browser_section_defaults_disabled(self, cfg_factory):
        path = cfg_factory()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("random_favs_browser", None)
        raw.pop("chrome_overlay", None)
        path.write_text(json.dumps(raw), encoding="utf-8")

        cfg = load_config(path)
        assert cfg.random_favs_browser.enabled is False


class TestRegenConfig:
    def test_defaults_when_section_absent(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.regen.generate_video_url == "https://example.com/video"
        assert cfg.regen.generate_image_url == "https://example.com/create"
        assert cfg.regen.media_root is None
        assert cfg.regen.metadata_root is None

    def test_reads_values_when_present(self, cfg_factory, tmp_path: Path):
        path = cfg_factory({"regen": {
            "media_root": str(tmp_path / "media"),
            "metadata_root": str(tmp_path / "meta"),
        }})
        cfg = load_config(path)
        assert cfg.regen.media_root == tmp_path / "media"
        assert cfg.regen.metadata_root == tmp_path / "meta"
        assert cfg.random_favs_browser.profile_name == "Blair"

    def test_loads_random_favs_browser_settings(self, cfg_factory):
        path = cfg_factory(
            {
                "random_favs_browser": {
                    "enabled": True,
                    "shortcut_path": "chrome.exe",
                    "user_data_dir": "chrome_data",
                    "profile_name": "Blair",

                    "open_count": 7,
                }
            }
        )
        cfg = load_config(path)
        assert cfg.random_favs_browser.enabled is True
        assert cfg.random_favs_browser.shortcut_path.name == "chrome.exe"
        assert cfg.random_favs_browser.user_data_dir.name == "chrome_data"
        assert cfg.random_favs_browser.open_count == 7
        assert cfg.random_favs_browser.lazy_load is False
        assert not hasattr(cfg.random_favs_browser, "bookmarks_folder_name")

    def test_loads_random_favs_browser_lazy_load(self, cfg_factory):
        path = cfg_factory(
            {
                "random_favs_browser": {
                    "enabled": True,
                    "shortcut_path": "chrome.exe",
                    "user_data_dir": "chrome_data",
                    "profile_name": "Blair",

                    "open_count": 7,
                    "lazy_load": True,
                }
            }
        )
        cfg = load_config(path)
        assert cfg.random_favs_browser.lazy_load is True

    def test_legacy_chrome_overlay_section_still_loads_random_favs_browser_settings(self, cfg_factory):
        path = cfg_factory(
            {
                "chrome_overlay": {
                    "enabled": True,
                    "shortcut_path": "chrome.exe",
                    "user_data_dir": "chrome_data",
                    "profile_name": "Blair",

                    "open_count": 7,
                }
            }
        )
        cfg = load_config(path)
        assert cfg.random_favs_browser.enabled is True
        assert cfg.random_favs_browser.shortcut_path.name == "chrome.exe"

    def test_nau_library_dir_property(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.paths.nau_library_dir == (tmp_path / "videos" / "videos" / "nau_library").resolve()

    def test_multiple_nau_library_dirs(self, tmp_path: Path, cfg_factory):
        extra = tmp_path / "extra"
        extra.mkdir()
        path = cfg_factory({"paths": {"nau_library_dirs": [
            str(tmp_path / "nau_library"),
            str(extra),
        ]}})
        cfg = load_config(path)
        assert len(cfg.paths.nau_library_dirs) == 2

    def test_multiple_portrait_and_landscape_dirs(self, tmp_path: Path, cfg_factory):
        portrait_extra = tmp_path / "portrait_extra"
        landscape_extra = tmp_path / "landscape_extra"
        portrait_extra.mkdir()
        landscape_extra.mkdir()
        path = cfg_factory({"paths": {
            "portrait_dirs": [str(tmp_path / "portrait"), str(portrait_extra)],
            "landscape_dirs": [str(tmp_path / "landscape"), str(landscape_extra)],
        }})
        cfg = load_config(path)
        assert len(cfg.paths.portrait_dirs) == 2
        assert len(cfg.paths.landscape_dirs) == 2
        assert cfg.paths.portrait_dir == (tmp_path / "portrait").resolve()
        assert cfg.paths.landscape_dir == (tmp_path / "landscape").resolve()


# ---------------------------------------------------------------------------
# ProjectConfig derived properties
# ---------------------------------------------------------------------------

class TestProjectConfigProperties:
    def test_log_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.log_file("broker") == (tmp_path / "state" / "broker.log").resolve()

    def test_genau_mode_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.genau_mode_file == (tmp_path / "state" / "genau_mode.txt").resolve()

    def test_genau_cmd_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.genau_cmd_file == (tmp_path / "state" / "genau_cmd.txt").resolve()

    def test_genau_paused_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.genau_paused_file == (tmp_path / "state" / "genau_paused.txt").resolve()

    def test_audio_paused_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.audio_paused_file == (tmp_path / "state" / "audio_paused.txt").resolve()

    def test_audio_volume_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.audio_volume_file == (tmp_path / "state" / "audio_volume.txt").resolve()

    def test_logs_dir(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.logs_dir == (tmp_path / "state").resolve()

    def test_random_favs_browser_manifest_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.random_favs_browser_manifest_file == (tmp_path / "state" / "random_favs_browser_urls.txt").resolve()


# ---------------------------------------------------------------------------
# VoiceControlConfig
# ---------------------------------------------------------------------------

class TestVoiceControlConfig:
    def test_missing_section_defaults_disabled(self, cfg_factory):
        path = cfg_factory()
        cfg = load_config(path)
        assert cfg.voice_control.enabled is False

    def test_loads_when_present(self, cfg_factory, tmp_path: Path):
        path = cfg_factory({
            "voice_control": {
                "enabled": True,
                "model_path": "vosk-model-small-en-us-0.15",
                "sample_rate": 8000,
                "confidence_threshold": 0.6,
                "device_name": "Brio",
            },
        })
        cfg = load_config(path)
        assert cfg.voice_control.enabled is True
        assert cfg.voice_control.model_path == "vosk-model-small-en-us-0.15"
        assert cfg.voice_control.sample_rate == 8000
        assert cfg.voice_control.confidence_threshold == 0.6
        assert cfg.voice_control.device_name == "Brio"

    def test_raises_on_wrong_type(self, cfg_factory):
        path = cfg_factory({"voice_control": "not-a-dict"})
        with pytest.raises(TypeError):
            load_config(path)


# ---------------------------------------------------------------------------
# loopback_port
# ---------------------------------------------------------------------------

class TestLoopbackPort:
    """The loopback server's port is the last fixed port a session claims.

    It is machine-wide, so a second session — an integration run, above all —
    finds it busy and comes up without a loopback server at all: Tampermonkey
    stops auto-updating and the RFB tab pages never hear about OmniPause, with
    only a log line to say so.  Naming it in config is what lets a run take one
    of its own; the default stays the port the userscript's @updateURL is pinned
    to, so nothing about a real session changes.
    """

    def test_defaults_to_the_port_the_userscript_is_pinned_to(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.loopback_port == LOOPBACK_PORT

    def test_a_session_can_be_given_a_port_of_its_own(self, cfg_factory):
        path = cfg_factory({"loopback_port": 54321})
        cfg = load_config(path)
        assert cfg.loopback_port == 54321

"""Tests for fun_time.config."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

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

    def test_raises_when_primary_vlc_dirs_empty(self, cfg_factory):
        path = cfg_factory({"paths": {"primary_vlc_dirs": []}})
        with pytest.raises(ValueError, match="primary_vlc_dirs"):
            load_config(path)

    def test_primary_vlc_dirs_not_a_list(self, tmp_path: Path, cfg_factory):
        path = cfg_factory({"paths": {"primary_vlc_dirs": "not-a-list"}})
        with pytest.raises(TypeError, match="primary_vlc_dirs"):
            load_config(path)

    def test_loads_paths_correctly(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.paths.vlc_exe == tmp_path / "vlc.exe"
        assert cfg.paths.state_dir == (tmp_path / "state").resolve()

    def test_loads_controller_ports(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.controller.primary_vlc_http_port == 8090
        assert cfg.controller.vlc2_http_port == 8091
        assert cfg.controller.vlc3_http_port == 8092
        assert cfg.controller.layout.main_monitor == 1
        assert cfg.controller.layout.secondary_monitor == 2

    def test_loads_broker_settings(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.broker.baud == 115200
        assert cfg.broker.auto_stale_timeout == 8.0

    def test_loads_robot_hand_settings(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.robot_hand.beats_per_loop == 1.0
        assert cfg.robot_hand.clip_cache_size == 2

    def test_loads_audio_companion(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.audio_companion.host == "127.0.0.1"
        assert cfg.audio_companion.port == 50556

    def test_missing_chrome_overlay_section_defaults_disabled(self, cfg_factory):
        path = cfg_factory()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("chrome_overlay", None)
        path.write_text(json.dumps(raw), encoding="utf-8")

        cfg = load_config(path)
        assert cfg.chrome_overlay.enabled is False
        assert cfg.chrome_overlay.profile_name == "Blair"

    def test_loads_chrome_overlay_settings(self, cfg_factory):
        path = cfg_factory(
            {
                "chrome_overlay": {
                    "enabled": True,
                    "shortcut_path": "chrome.exe",
                    "user_data_dir": "chrome_data",
                    "profile_name": "Blair",
                    "bookmarks_folder_name": "Fun Time Favs",
                    "open_count": 7,
                }
            }
        )
        cfg = load_config(path)
        assert cfg.chrome_overlay.enabled is True
        assert cfg.chrome_overlay.shortcut_path.name == "chrome.exe"
        assert cfg.chrome_overlay.user_data_dir.name == "chrome_data"
        assert cfg.chrome_overlay.open_count == 7

    def test_primary_vlc_dir_property(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.paths.primary_vlc_dir == (tmp_path / "vlc_primary").resolve()

    def test_multiple_primary_vlc_dirs(self, tmp_path: Path, cfg_factory):
        extra = tmp_path / "vlc_extra"
        extra.mkdir()
        path = cfg_factory({"paths": {"primary_vlc_dirs": [
            str(tmp_path / "vlc_primary"),
            str(extra),
        ]}})
        cfg = load_config(path)
        assert len(cfg.paths.primary_vlc_dirs) == 2

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

    def test_robot_hand_mode_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.robot_hand_mode_file == (tmp_path / "state" / "robot_hand_mode.txt").resolve()

    def test_robot_hand_cmd_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.robot_hand_cmd_file == (tmp_path / "state" / "robot_hand_cmd.txt").resolve()

    def test_robot_hand_paused_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.robot_hand_paused_file == (tmp_path / "state" / "robot_hand_paused.txt").resolve()

    def test_broker_cmd_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.broker_cmd_file == (tmp_path / "state" / "broker_cmd.txt").resolve()

    def test_robot_hand_enabled_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.robot_hand_enabled_file == (tmp_path / "state" / "robot_hand_enabled.txt").resolve()

    def test_audio_cmd_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.audio_cmd_file == (tmp_path / "state" / "audio_cmd.txt").resolve()

    def test_audio_paused_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.audio_paused_file == (tmp_path / "state" / "audio_paused.txt").resolve()

    def test_logs_dir(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.logs_dir == (tmp_path / "state").resolve()

    def test_chrome_overlay_manifest_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.chrome_overlay_manifest_file == (tmp_path / "state" / "chrome_overlay_urls.txt").resolve()

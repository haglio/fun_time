from __future__ import annotations

import configparser
import re
from dataclasses import fields
from pathlib import Path

import pytest

from fun_time.config import LayoutConfig, load_config
from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    ChildModules,
    CommandFiles,
    Executables,
    LaunchManifest,
    ManifestKeyMissing,
    MediaSources,
    RandomFavsBrowserSettings,
    RegenSettings,
    RuntimePaths,
    build_windows_bridge_manifest,
    write_manifest_data,
    write_windows_bridge_manifest,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

_EXPECTED_KEYS: dict[str, set[str]] = {
    "runtime": {
        "config_path", "windows_bridge_log_file", "sibling_checkouts", "origenerator_dir",
    },
    "executables": {"python_exe", "origenerator_python_exe"},
    "media": {
        "main_funestra_library_sources", "portrait_dirs", "landscape_dirs", "trash_dir",
        "genau_flicks", "genau_audio", "vr_library_dirs", "genau_vr_flicks",
    },
    "modules": {
        "main_funestra_module", "satellite_module", "audio_module",
        "dashboard_module",
    },
    "commands": {
        "broker_mode_file", "genau_cmd_file", "genau_paused_file",
        "genau_status_file",
        "main_funestra_cmd_file", "main_funestra_paused_file", "main_funestra_status_file",
        "main_funestra_console_file", "main_funestra_playlist_file",
        "portrait_cmd_file", "portrait_paused_file", "portrait_status_file",
        "portrait_playlist_file", "portrait_hud_file", "portrait_notice_file",
        "portrait_origenerator_hud_file",
        "landscape_cmd_file", "landscape_paused_file", "landscape_status_file",
        "landscape_playlist_file", "landscape_hud_file", "landscape_notice_file",
        "landscape_origenerator_hud_file",
        "broker_cmd_file", "broker_state_dir",
        "broker_tray_launcher", "audio_paused_file", "audio_volume_file",
        "dashboard_state_file", "dashboard_cmd_file",
        "state_dir", "main_funestra_notice_file",
        "origenerator_frames_file", "origenerator_input_file",
        "origenerator_cmd_file", "origenerator_paused_file",
        "origenerator_status_file",
        "osr2_slot_port",
    },
    "dashboard": {"enabled"},
    "loopback": {"port"},
    "layout": {
        "primary_monitor", "secondary_monitor", "main_top_ratio",
        "landscape_width_ratio",
    },
    "random_favs_browser": {"enabled", "shortcut_path", "manifest_file"},
    "regen": {
        "generate_video_url", "generate_image_url", "media_root",
        "metadata_root",
    },
}


def test_the_manifest_carries_exactly_the_documented_schema(cfg_path):
    data = build_windows_bridge_manifest(load_config(cfg_path))

    assert {section: set(keys) for section, keys in data.items()} == _EXPECTED_KEYS


def test_every_key_the_ahk_shell_demands_is_written(cfg_path):
    script = (_REPO_ROOT / "windows_bridge_hotkeys.ahk").read_text(encoding="utf-8")
    demanded = re.findall(r'RequireManifestValue\("([^"]+)",\s*"([^"]+)"\)', script)
    assert demanded, "the AHK script no longer reads the manifest?"

    data = build_windows_bridge_manifest(load_config(cfg_path))
    for section, key in demanded:
        assert data.get(section, {}).get(key), (
            f"AHK demands [{section}] {key}, which the manifest does not carry"
        )


def test_the_written_ini_reads_back_byte_for_byte(cfg_path, tmp_path):
    cfg = load_config(cfg_path)
    data = build_windows_bridge_manifest(cfg)
    manifest_path = write_windows_bridge_manifest(
        cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
    )

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(manifest_path), encoding="utf-8")

    read_back = {section: dict(parser[section]) for section in parser.sections()}
    assert read_back == data


class TestReadingItBack:
    def test_every_key_the_writer_emits_has_a_field_to_land_in(self, cfg_path, tmp_path):
        covered = {section: {f.name for f in fields(record)} for section, record in (
            ("runtime", RuntimePaths),
            ("executables", Executables),
            ("media", MediaSources),
            ("modules", ChildModules),
            ("commands", CommandFiles),
            ("random_favs_browser", RandomFavsBrowserSettings),
            ("regen", RegenSettings),
            ("layout", LayoutConfig),
        )}
        covered["dashboard"] = {"enabled"}
        covered["loopback"] = {"port"}

        assert covered == _EXPECTED_KEYS

    def test_the_values_come_back_exactly_as_the_file_carries_them(self, cfg_path, tmp_path):
        cfg = load_config(cfg_path)
        path = write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
        raw = build_windows_bridge_manifest(cfg)

        manifest = LaunchManifest.read(path)

        assert manifest.commands.main_funestra_cmd_file == raw["commands"]["main_funestra_cmd_file"]
        assert manifest.media.main_funestra_library_sources == raw["media"]["main_funestra_library_sources"]
        assert manifest.executables.python_exe == raw["executables"]["python_exe"]
        assert manifest.runtime.config_path == raw["runtime"]["config_path"]
        assert manifest.modules.satellite_module == raw["modules"]["satellite_module"]

    def test_the_four_that_were_never_strings_come_back_typed(self, cfg_path, tmp_path):
        cfg = load_config(cfg_path)
        manifest = LaunchManifest.read(
            write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME))

        assert manifest.dashboard_enabled is True
        assert manifest.loopback_port == cfg.loopback_port
        assert manifest.layout == cfg.layout
        assert manifest.random_favs_browser.enabled is cfg.random_favs_browser.enabled

    def test_a_satellites_file_can_be_asked_for_by_that_satellite(self, cfg_path, tmp_path):
        cfg = load_config(cfg_path)
        commands = LaunchManifest.read(
            write_windows_bridge_manifest(
                cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)).commands

        assert commands.funestra_file("portrait", "hud") == commands.portrait_hud_file
        assert commands.funestra_file("landscape", "cmd") == commands.landscape_cmd_file
        assert commands.funestra_file("portrait", "playlist") == commands.portrait_playlist_file
        assert commands.funestra_file("landscape", "notice") == commands.landscape_notice_file
        assert (commands.funestra_file("landscape", "origenerator_hud")
                == commands.landscape_origenerator_hud_file)

    def test_a_missing_key_names_the_key_and_the_file(self, cfg_path, tmp_path):
        cfg = load_config(cfg_path)
        path = write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
        path.write_text(
            path.read_text(encoding="utf-8").replace("main_funestra_cmd_file = ", "main_funestra_cmd_typo = "),
            encoding="utf-8")

        with pytest.raises(ManifestKeyMissing) as raised:
            LaunchManifest.read(path)

        assert "main_funestra_cmd_file" in str(raised.value)
        assert str(path) in str(raised.value)

    def test_the_message_survives_str_without_repr_escaping(self):
        """str() of the error is the message verbatim, so a Windows manifest
        path keeps its single backslashes.  A KeyError base applies repr() and
        would double every one, and the file the error names is the point of it."""
        message = r"[runtime] main_funestra_cmd_file missing from D:\a\fun_time\state\bridge.ini"
        assert str(ManifestKeyMissing(message)) == message

    def test_a_section_this_session_does_not_read_is_left_alone(self, cfg_path, tmp_path):
        cfg = load_config(cfg_path)
        data = build_windows_bridge_manifest(cfg)
        data["vr"] = {"osr2_slot_port": "8000"}
        path = write_manifest_data(data, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        assert LaunchManifest.read(path).commands.main_funestra_cmd_file

    def test_the_keys_a_reader_has_always_defaulted_stay_defaulted(self, cfg_path, tmp_path):
        cfg = load_config(cfg_path)
        data = build_windows_bridge_manifest(cfg)
        for key in ("broker_state_dir", "broker_tray_launcher",
                    "origenerator_cmd_file", "origenerator_paused_file"):
            del data["commands"][key]
        del data["executables"]["origenerator_python_exe"]
        for key in ("sibling_checkouts", "origenerator_dir"):
            del data["runtime"][key]
        path = write_manifest_data(data, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        manifest = LaunchManifest.read(path)

        assert manifest.commands.broker_state_dir == ""
        assert manifest.executables.origenerator_python_exe == ""
        assert manifest.runtime.origenerator_dir == ""

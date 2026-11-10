from __future__ import annotations

import configparser
import re
from dataclasses import fields
from pathlib import Path

import pytest

from fun_time.config import LayoutConfig, SatelliteChannel, load_config
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    ChildModules,
    CommandFiles,
    Executables,
    LaunchManifest,
    ManifestKeyMissing,
    MediaSources,
    RandomFavesBrowserSettings,
    RegenSettings,
    RuntimePaths,
    build_windows_bridge_manifest,
    write_manifest_data,
    write_windows_bridge_manifest,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

_A_SATELLITES_CHANNEL = {
    "cmd_file", "paused_file", "status_file", "playlist_file", "notice_file", "hud_file",
    "origenerator_hud_file",
}

_KEYS_EVERY_READER_SPELLS_VERBATIM: dict[str, set[str]] = {
    "runtime": {
        "config_path", "windows_bridge_log_file", "sibling_checkouts", "origenerator_dir",
    },
    "executables": {"python_exe", "origenerator_python_exe"},
    "media": {
        "main_funestra_library_sources", "trash_dir",
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
        "broker_cmd_file", "broker_state_dir",
        "broker_tray_launcher", "audio_paused_file", "audio_volume_file",
        "dashboard_state_file", "dashboard_cmd_file",
        "state_dir", "main_funestra_notice_file",
        "origenerator_frames_file", "origenerator_input_file",
        "origenerator_cmd_file", "origenerator_paused_file",
        "origenerator_status_file",
        "osr2_slot_port",
    },
    "portrait": _A_SATELLITES_CHANNEL,
    "landscape": _A_SATELLITES_CHANNEL,
    "dashboard": {"enabled"},
    "loopback": {"port"},
    "layout": {
        "primary_monitor", "secondary_monitor", "main_top_ratio",
        "landscape_width_ratio",
    },
    "random_faves_browser": {"enabled", "shortcut_path", "manifest_file"},
    "regen": {
        "generate_video_url", "generate_image_url", "media_root",
        "metadata_root",
    },
}


def test_no_key_is_added_dropped_or_respelled_unnoticed(cfg_path):
    data = build_windows_bridge_manifest(load_config(cfg_path))

    assert {section: set(keys) for section, keys in data.items()} == _KEYS_EVERY_READER_SPELLS_VERBATIM


def test_every_key_the_ahk_shell_demands_is_written(cfg_path):
    script = (_REPO_ROOT / "windows_bridge_hotkeys.ahk").read_text(encoding="utf-8")
    demanded = re.findall(r'RequireManifestValue\("([^"]+)",\s*"([^"]+)"\)', script)
    assert demanded, "the AHK script no longer reads the manifest?"

    data = build_windows_bridge_manifest(load_config(cfg_path))
    for section, key in demanded:
        assert data.get(section, {}).get(key), (
            f"AHK demands [{section}] {key}, which the manifest does not carry"
        )


_READERS_THAT_SPELL_KEYS_THEMSELVES = ("fun_time/dashboard_app.py", "fun_time/library_browser.py")


@pytest.mark.parametrize("reader", _READERS_THAT_SPELL_KEYS_THEMSELVES)
def test_every_key_a_reader_spells_for_itself_is_written(reader, cfg_path):
    source = (_REPO_ROOT / reader).read_text(encoding="utf-8")
    a_read_of_the_manifest = (
        r'(?:\bparser|_read_manifest\([^)]*\))\.get(?:int|float)?\(\s*"([^"]+)",\s*"([^"]+)"')
    spelled = re.findall(a_read_of_the_manifest, source)
    assert spelled, f"{reader} no longer reads the manifest for itself?"

    data = build_windows_bridge_manifest(load_config(cfg_path))
    for section, key in spelled:
        assert key in data.get(section, {}), (
            f"{reader} reads [{section}] {key}, which the manifest never carries"
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
            ("random_faves_browser", RandomFavesBrowserSettings),
            ("regen", RegenSettings),
            ("layout", LayoutConfig),
            ("portrait", SatelliteChannel),
            ("landscape", SatelliteChannel),
        )}
        covered["dashboard"] = {"enabled"}
        covered["loopback"] = {"port"}

        assert covered == _KEYS_EVERY_READER_SPELLS_VERBATIM

    def test_a_value_reaches_a_childs_command_line_as_the_writer_wrote_it(self, cfg_path, tmp_path):
        raw = build_windows_bridge_manifest(load_config(cfg_path))
        one_a_path_round_trip_would_rewrite = "C:/example/films/|D:\\example\\more\\"
        raw["media"]["genau_flicks"] = one_a_path_round_trip_would_rewrite
        path = write_manifest_data(raw, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        manifest = LaunchManifest.read(path)

        assert manifest.media.genau_flicks == one_a_path_round_trip_would_rewrite
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
        assert manifest.random_faves_browser.enabled is cfg.random_faves_browser.enabled

    def test_a_satellites_channel_comes_back_as_the_config_spelled_it(self, cfg_path, tmp_path):
        cfg = load_config(cfg_path)
        manifest = LaunchManifest.read(
            write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME))

        for funestra in FunTimeFunestra.SATELLITES:
            assert manifest.satellite(funestra) == cfg.satellite(funestra)

    def test_a_missing_key_says_which_of_the_machines_manifests_lacks_it(self, cfg_path, tmp_path):
        cfg = load_config(cfg_path)
        path = write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
        path.write_text(
            path.read_text(encoding="utf-8").replace("main_funestra_cmd_file = ", "main_funestra_cmd_typo = "),
            encoding="utf-8")

        with pytest.raises(ManifestKeyMissing) as raised:
            LaunchManifest.read(path)

        assert "main_funestra_cmd_file" in str(raised.value)
        assert str(path) in str(raised.value)

    def test_unlike_a_keyerror_the_error_keeps_a_windows_paths_backslashes_single(self):
        message = r"[runtime] main_funestra_cmd_file missing from D:\a\fun_time\state\bridge.ini"
        assert str(ManifestKeyMissing(message)) == message
        assert r"D:\\a\\fun_time" in str(KeyError(message))

    def test_the_section_fun_time_vr_adds_for_itself_is_passed_over(self, cfg_path, tmp_path):
        cfg = load_config(cfg_path)
        data = build_windows_bridge_manifest(cfg)
        data["vr"] = {"osr2_slot_port": "8000"}
        path = write_manifest_data(data, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        assert LaunchManifest.read(path).commands.main_funestra_cmd_file

    def test_a_manifest_the_readers_before_this_one_accepted_is_not_refused(self, cfg_path, tmp_path):
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

"""The launch manifest: the schema four other processes read back.

``state/windows_bridge_launch.ini`` is written once per launch and read by the
AHK hotkey shell, the dashboard process, the dispatch loop and the VR
orchestrator, with ``optionxform = str`` making the exact spelling of every
key load-bearing.  These tests pin that surface: the whole key inventory (so
a rename is a deliberate, visible act), the round trip through the INI writer,
and the cross-language contract with the AHK script — the one consumer no
Python import can break at collection time.
"""
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

# The full schema, one entry per section.  A key added, dropped or respelled
# in build_windows_bridge_manifest must be added, dropped or respelled here in
# the same commit — which is the point: every consumer reads these spellings
# verbatim, so an accidental rename must fail somewhere before it fails on
# the next launch.
_EXPECTED_KEYS: dict[str, set[str]] = {
    "runtime": {
        "config_path", "windows_bridge_log_file", "genau_config_path",
        "genau_project_dirs", "origenerator_dir",
    },
    "executables": {"python_exe", "genau_python_exe", "origenerator_python_exe"},
    "media": {
        "nau_library_sources", "portrait_dirs", "landscape_dirs", "weird_dir",
        "favs_file", "genau_clips", "genau_audio",
    },
    "modules": {
        "genau_module", "nau_module", "satellite_module", "audio_module",
        "dashboard_module",
    },
    "commands": {
        "genau_mode_file", "genau_cmd_file", "genau_paused_file",
        "nau_cmd_file", "nau_paused_file", "nau_status_file",
        "nau_console_file", "nau_playlist_file",
        "portrait_cmd_file", "portrait_paused_file", "portrait_status_file",
        "portrait_playlist_file", "portrait_hud_file",
        "landscape_cmd_file", "landscape_paused_file", "landscape_status_file",
        "landscape_playlist_file", "landscape_hud_file",
        "broker_cmd_file", "broker_heartbeat_file", "broker_state_dir",
        "broker_tray_launcher", "audio_paused_file", "audio_volume_file",
        "dashboard_state_file", "dashboard_cmd_file",
        "origenerator_cmd_file", "origenerator_paused_file",
        "origenerator_status_file",
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
    """The hotkey shell pulls its values with RequireManifestValue(section,
    key) and dies on a missing one — and being AutoHotkey, no Python rename
    can break it at import time.  So the contract is pinned from the script's
    own source: every (section, key) it demands exists in the built manifest,
    non-empty."""
    script = (_REPO_ROOT / "windows_bridge_hotkeys.ahk").read_text(encoding="utf-8")
    demanded = re.findall(r'RequireManifestValue\("([^"]+)",\s*"([^"]+)"\)', script)
    assert demanded, "the AHK script no longer reads the manifest?"

    data = build_windows_bridge_manifest(load_config(cfg_path))
    for section, key in demanded:
        assert data.get(section, {}).get(key), (
            f"AHK demands [{section}] {key}, which the manifest does not carry"
        )


def test_the_written_ini_reads_back_byte_for_byte(cfg_path, tmp_path):
    """What the children read is the INI, not the dict — the writer must
    preserve every section, key casing and value on the way through."""
    cfg = load_config(cfg_path)
    data = build_windows_bridge_manifest(cfg)
    manifest_path = write_windows_bridge_manifest(
        cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
    )

    parser = configparser.ConfigParser()
    parser.optionxform = str  # the readers' setting: spelling is load-bearing
    parser.read(str(manifest_path), encoding="utf-8")

    read_back = {section: dict(parser[section]) for section in parser.sections()}
    assert read_back == data


class TestReadingItBack:
    """The read side: one typed reader, so no consumer spells a key itself.

    ``LaunchManifest.read`` is the only place in the family that subscripts
    this INI, which is what stops a fifth module inventing a fifth spelling of
    a key the writer never emitted.
    """

    def test_every_key_the_writer_emits_has_a_field_to_land_in(self, cfg_path, tmp_path):
        """The reader and the writer are two halves of one schema; a key added
        to the writer with nowhere to read it is a key no consumer can use."""
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
        covered["dashboard"] = {"enabled"}   # one flag, read as a bool
        covered["loopback"] = {"port"}       # one number, read as an int

        assert covered == _EXPECTED_KEYS

    def test_the_values_come_back_exactly_as_the_file_carries_them(self, cfg_path, tmp_path):
        """A child is handed these on its command line, so a value that came
        back re-rendered — a Path round trip, a stripped separator — would
        reach it as a different string than the writer wrote."""
        cfg = load_config(cfg_path)
        path = write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
        raw = build_windows_bridge_manifest(cfg)

        manifest = LaunchManifest.read(path)

        assert manifest.commands.nau_cmd_file == raw["commands"]["nau_cmd_file"]
        assert manifest.media.nau_library_sources == raw["media"]["nau_library_sources"]
        assert manifest.executables.python_exe == raw["executables"]["python_exe"]
        assert manifest.runtime.config_path == raw["runtime"]["config_path"]
        assert manifest.modules.satellite_module == raw["modules"]["satellite_module"]

    def test_the_four_that_were_never_strings_come_back_typed(self, cfg_path, tmp_path):
        """Two flags, a port and the layout numbers: each was converted at the
        four separate places that read it, in two different spellings for the
        dashboard's."""
        cfg = load_config(cfg_path)
        manifest = LaunchManifest.read(
            write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME))

        assert manifest.dashboard_enabled is True
        assert manifest.loopback_port == cfg.loopback_port
        assert manifest.layout == cfg.layout
        assert manifest.random_favs_browser.enabled is cfg.random_favs_browser.enabled

    def test_a_sides_file_can_be_asked_for_by_that_side(self, cfg_path, tmp_path):
        """The HUD publisher and the VR player both build their two sides in a
        loop, so they need the key by side name rather than spelled out."""
        cfg = load_config(cfg_path)
        commands = LaunchManifest.read(
            write_windows_bridge_manifest(
                cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)).commands

        assert commands.side_file("portrait", "hud") == commands.portrait_hud_file
        assert commands.side_file("landscape", "cmd") == commands.landscape_cmd_file
        assert commands.side_file("portrait", "playlist") == commands.portrait_playlist_file

    def test_a_missing_key_names_the_key_and_the_file(self, cfg_path, tmp_path):
        """The interesting question when this happens is always WHICH manifest
        — a session's own, a branch session's, or one a stale process holds."""
        cfg = load_config(cfg_path)
        path = write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
        path.write_text(
            path.read_text(encoding="utf-8").replace("nau_cmd_file = ", "nau_cmd_typo = "),
            encoding="utf-8")

        with pytest.raises(ManifestKeyMissing) as raised:
            LaunchManifest.read(path)

        assert "nau_cmd_file" in str(raised.value)
        assert str(path) in str(raised.value)

    def test_a_section_this_session_does_not_read_is_left_alone(self, cfg_path, tmp_path):
        """FunTimeVR amends the built dict with a [vr] section before writing
        it, so the reader has to pass over what it does not know."""
        cfg = load_config(cfg_path)
        data = build_windows_bridge_manifest(cfg)
        data["vr"] = {"tcode_udp_port": "8000"}
        path = write_manifest_data(data, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        assert LaunchManifest.read(path).commands.nau_cmd_file

    def test_the_keys_a_reader_has_always_defaulted_stay_defaulted(self, cfg_path, tmp_path):
        """These five were read with a fallback rather than demanded, so a
        manifest without them must still parse — this reader refuses nothing
        the readers it replaces accepted."""
        cfg = load_config(cfg_path)
        data = build_windows_bridge_manifest(cfg)
        for key in ("broker_state_dir", "broker_tray_launcher",
                    "origenerator_cmd_file", "origenerator_paused_file"):
            del data["commands"][key]
        del data["executables"]["origenerator_python_exe"]
        for key in ("genau_project_dirs", "origenerator_dir"):
            del data["runtime"][key]
        path = write_manifest_data(data, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        manifest = LaunchManifest.read(path)

        assert manifest.commands.broker_state_dir == ""
        assert manifest.executables.origenerator_python_exe == ""
        assert manifest.runtime.origenerator_dir == ""

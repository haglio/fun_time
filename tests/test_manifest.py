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
from pathlib import Path

from fun_time.config import load_config
from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    build_windows_bridge_manifest,
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

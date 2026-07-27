from __future__ import annotations

import configparser
import os
from pathlib import Path

from .hud_transport import HUD_FILENAME
from .nau_console import nau_console_path

WINDOWS_BRIDGE_MANIFEST_FILENAME = "windows_bridge_launch.ini"


def build_windows_bridge_manifest(config) -> dict[str, dict[str, str]]:
    layout = config.layout
    dashboard_enabled = os.environ.get("FUN_TIME_DISABLE_DASHBOARD") != "1"
    return {
        "runtime": {
            "config_path": str(config.config_path),
            "windows_bridge_log_file": str(config.log_file("windows_bridge")),
            "genau_config_path": str(config.paths.genau_config_path or config.config_path),
        },
        "executables": {
            # Two interpreters: ours runs everything this repo ships (the
            # dashboard, the audio companion, the satellite players), and
            # genau's runs the apps that live in ../genau (Genau and Nau).
            "python_exe": str(config.paths.python_exe),
            "genau_python_exe": str(config.paths.genau_python_exe or config.paths.python_exe),
        },
        "media": {
            "nau_library_sources": "|".join(str(path) for path in config.paths.nau_library_dirs),
            "portrait_dirs": "|".join(str(path) for path in config.paths.portrait_dirs),
            "landscape_dirs": "|".join(str(path) for path in config.paths.landscape_dirs),
            "weird_dir": str(config.paths.weird_dir),
            "favs_file": str(config.paths.favs_file),
            "genau_clips": str(config.paths.clips_dir),
            "genau_audio": str(config.paths.audio_dir),
        },
        "modules": {
            "genau_module": "genau",
            "nau_module": "nau",
            "satellite_module": "satellite",
            "audio_module": "fun_time.audio_companion_app",
            "dashboard_module": "fun_time.dashboard_app",
        },
        "commands": {
            "genau_mode_file": str(config.genau_mode_file),
            "genau_cmd_file": str(config.genau_cmd_file),
            "genau_paused_file": str(config.genau_paused_file),
            "nau_cmd_file": str(config.nau_cmd_file),
            "nau_paused_file": str(config.nau_paused_file),
            "nau_status_file": str(config.nau_status_file),
            "nau_console_file": str(nau_console_path(config.paths.state_dir)),
            "nau_playlist_file": str(config.nau_playlist_file),
            "portrait_cmd_file": str(config.paths.state_dir / "portrait_cmd.txt"),
            "portrait_paused_file": str(config.paths.state_dir / "portrait_paused.txt"),
            "portrait_status_file": str(config.paths.state_dir / "portrait_status.txt"),
            "portrait_playlist_file": str(config.paths.state_dir / "portrait_playlist.tsv"),
            "portrait_hud_file": str(config.paths.state_dir / HUD_FILENAME["portrait"]),
            "landscape_cmd_file": str(config.paths.state_dir / "landscape_cmd.txt"),
            "landscape_paused_file": str(config.paths.state_dir / "landscape_paused.txt"),
            "landscape_status_file": str(config.paths.state_dir / "landscape_status.txt"),
            "landscape_playlist_file": str(config.paths.state_dir / "landscape_playlist.tsv"),
            "landscape_hud_file": str(config.paths.state_dir / HUD_FILENAME["landscape"]),
            "broker_cmd_file": str(config.paths.state_dir / "broker_cmd.txt"),
            "broker_heartbeat_file": str(config.paths.state_dir / "broker_heartbeat.txt"),
            "broker_tray_launcher": str(config.paths.broker_tray_launcher or ""),
            "audio_paused_file": str(config.audio_paused_file),
            "audio_volume_file": str(config.audio_volume_file),
            "dashboard_state_file": str(config.paths.state_dir / "dashboard_state.ini"),
            "dashboard_cmd_file": str(config.paths.state_dir / "dashboard_cmd.txt"),
        },
        "dashboard": {
            "enabled": "1" if dashboard_enabled else "0",
        },
        "loopback": {
            "port": str(config.loopback_port),
        },
        "layout": {
            "main_monitor": str(layout.main_monitor),
            "secondary_monitor": str(layout.secondary_monitor),
            "primary_top_ratio": str(layout.primary_top_ratio),
            "landscape_width_ratio": str(layout.landscape_width_ratio),
        },
        "random_favs_browser": {
            "enabled": "1" if config.random_favs_browser.enabled else "0",
            "shortcut_path": str(config.random_favs_browser.shortcut_path),
            "manifest_file": str(config.random_favs_browser_manifest_file),
        },
        "regen": {
            "generate_video_url": config.regen.generate_video_url,
            "generate_image_url": config.regen.generate_image_url,
            "media_root": str(config.regen.media_root or ""),
            "metadata_root": str(config.regen.metadata_root or ""),
        },
    }


def write_manifest_data(data: dict[str, dict[str, str]], destination: Path) -> Path:
    """Write a manifest dict as the INI every child process reads back.

    Split from :func:`write_windows_bridge_manifest` so a variant session
    (FunTimeVR) can amend the built dict before it hits disk.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_dict(data)
    with destination.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    return destination


def write_windows_bridge_manifest(config, destination: Path | None = None) -> Path:
    manifest_path = destination or (config.paths.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME)
    return write_manifest_data(build_windows_bridge_manifest(config), manifest_path)

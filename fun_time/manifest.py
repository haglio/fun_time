from __future__ import annotations

import configparser
import os
from pathlib import Path

WINDOWS_BRIDGE_MANIFEST_FILENAME = "windows_bridge_launch.ini"


def build_windows_bridge_manifest(config, vlc_http_pass: str) -> dict[str, dict[str, str]]:
    layout = config.layout
    dashboard_enabled = os.environ.get("FUN_TIME_DISABLE_DASHBOARD") != "1"
    return {
        "runtime": {
            "project_dir": str(config.project_dir),
            "config_path": str(config.config_path),
            "windows_bridge_log_file": str(config.log_file("windows_bridge")),
        },
        "executables": {
            "vlc_exe": str(config.paths.vlc_exe),
            "mfp_exe": str(config.paths.mfp_exe),
            "python_exe": str(config.paths.python_exe),
        },
        "media": {
            "primary_vlc_sources": "|".join(str(path) for path in config.paths.primary_vlc_dirs),
            "portrait_dirs": "|".join(str(path) for path in config.paths.portrait_dirs),
            "landscape_dirs": "|".join(str(path) for path in config.paths.landscape_dirs),
            "weird_dir": str(config.paths.weird_dir),
            "favs_file": str(config.paths.favs_file),
            "robot_hand_clips": str(config.paths.clips_dir),
            "robot_hand_audio": str(config.paths.audio_dir),
        },
        "modules": {
            "robot_hand_module": "fun_time.robot_hand.app",
            "audio_module": "fun_time.audio_companion_app",
            "dashboard_module": "fun_time.dashboard_app",
        },
        "commands": {
            "robot_hand_mode_file": str(config.robot_hand_mode_file),
            "robot_hand_cmd_file": str(config.robot_hand_cmd_file),
            "robot_hand_enabled_file": str(config.robot_hand_enabled_file),
            "robot_hand_paused_file": str(config.robot_hand_paused_file),
            "broker_cmd_file": str(config.broker_cmd_file),
            "broker_heartbeat_file": str(config.broker_heartbeat_file),
            "audio_cmd_file": str(config.audio_cmd_file),
            "audio_paused_file": str(config.audio_paused_file),
            "dashboard_state_file": str(config.paths.state_dir / "dashboard_state.ini"),
            "dashboard_cmd_file": str(config.paths.state_dir / "dashboard_cmd.txt"),
        },
        "dashboard": {
            "enabled": "1" if dashboard_enabled else "0",
        },
        "vlc": {
            "primary_vlc_port": str(config.vlc.primary_vlc_http_port),
            "vlc2_port": str(config.vlc.vlc2_http_port),
            "vlc3_port": str(config.vlc.vlc3_http_port),
            "vlc_pass": vlc_http_pass,
        },
        "layout": {
            "main_monitor": str(layout.main_monitor),
            "secondary_monitor": str(layout.secondary_monitor),
            "primary_top_ratio": str(layout.primary_top_ratio),
            "landscape_width_ratio": str(layout.landscape_width_ratio),
            "mfp_width_ratio": str(layout.mfp_width_ratio),
            "mfp_height_ratio": str(layout.mfp_height_ratio),
            "left_partition_top_ratio": str(layout.left_partition_top_ratio),
            "left_partition_bottom_ratio": str(layout.left_partition_bottom_ratio),
        },
        "random_favs_browser": {
            "enabled": "1" if config.random_favs_browser.enabled else "0",
            "shortcut_path": str(config.random_favs_browser.shortcut_path),
            "manifest_file": str(config.random_favs_browser_manifest_file),
            "lazy_load": "1" if config.random_favs_browser.lazy_load else "0",
        },
    }


def write_windows_bridge_manifest(config, vlc_http_pass: str, destination: Path | None = None) -> Path:
    manifest_path = destination or (config.paths.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_dict(build_windows_bridge_manifest(config, vlc_http_pass))
    with manifest_path.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    return manifest_path

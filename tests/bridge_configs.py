from __future__ import annotations

from pathlib import Path

from fun_time.bridge_records import BridgeConfig
from fun_time.config import SatelliteChannel
from fun_time.fun_time_funestras import FunTimeFunestra


def made_up_bridge_config(state_dir: Path, **overrides) -> BridgeConfig:
    settings = {
        "portrait": SatelliteChannel.in_state_dir(state_dir, FunTimeFunestra.PORTRAIT),
        "landscape": SatelliteChannel.in_state_dir(state_dir, FunTimeFunestra.LANDSCAPE),
        "trash_dir": state_dir / "trash",
        "state_dir": state_dir,
        "main_sources": "",
        "broker_mode_file": state_dir / "broker_mode.txt",
        "genau_cmd_file": state_dir / "genau_cmd.txt",
        "genau_paused_file": state_dir / "genau_paused.txt",
        "genau_status_file": state_dir / "genau_status.txt",
        "audio_paused_file": state_dir / "audio_paused.txt",
        "audio_volume_file": state_dir / "audio_volume.txt",
        "main_funestra_cmd_file": state_dir / "main_player_cmd.txt",
        "main_funestra_paused_file": state_dir / "main_player_paused.txt",
        "main_funestra_status_file": state_dir / "main_player_status.txt",
        "dashboard_state_file": state_dir / "dashboard_state.ini",
        "origenerator_cmd_file": state_dir / "origenerator_cmd.txt",
        "origenerator_paused_file": state_dir / "origenerator_paused.txt",
        "origenerator_status_file": state_dir / "origenerator_status.txt",
    }
    settings.update(overrides)
    return BridgeConfig(**settings)

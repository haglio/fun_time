from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from funestra.channel_manifest import channel_argv, channels_from_args
from funestra.funestra import Channels

from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.osr2_section import osr2_readout_path

from .cli import MAIN_FUNESTRAS_CHANNELS, PLAY_POINTS, play_points_path


@dataclass(frozen=True)
class MainChannels(Channels):
    @classmethod
    def from_args(cls, args) -> MainChannels:
        return cls(
            **channels_from_args(args, MAIN_FUNESTRAS_CHANNELS),
            play_points=play_points_path(args),
            osr2_sender=FunTimeFunestra.MAIN.label,
            osr2_readout=(osr2_readout_path(args.state_dir, FunTimeFunestra.MAIN.label)
                          if args.state_dir else None),
        )

    @classmethod
    def from_manifest(cls, commands) -> MainChannels:
        state_dir = Path(commands.state_dir)
        return cls(
            playlist=Path(commands.main_funestra_playlist_file),
            command=Path(commands.main_funestra_cmd_file),
            paused=Path(commands.main_funestra_paused_file),
            status=Path(commands.main_funestra_status_file),
            notice=Path(commands.main_funestra_notice_file),
            play_points=state_dir / PLAY_POINTS,
            console=Path(commands.main_funestra_console_file),
            dashboard_cmd=Path(commands.dashboard_cmd_file),
            drive=Path(commands.genau_drive_file),
            osr2_readout=osr2_readout_path(state_dir, FunTimeFunestra.MAIN.label),
        )

    def to_argv(self) -> list[str]:
        return channel_argv(self, MAIN_FUNESTRAS_CHANNELS)

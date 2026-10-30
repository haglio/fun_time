"""The files the Main Funestra is driven through, off its command line or a headset's manifest."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from funestra.funestra import Channels

from fun_time.osr2_section import osr2_readout_path
from fun_time.room_funestras import RoomFunestra

from .cli import PLAY_POINTS, play_points_path


@dataclass(frozen=True)
class MainChannels(Channels):
    @classmethod
    def from_args(cls, args) -> MainChannels:
        return cls(
            playlist=args.playlist,
            command=args.command_file,
            paused=args.paused_file,
            status=args.status_file,
            notice=args.notice_file,
            play_points=play_points_path(args),
            console=args.console_file,
            dashboard_cmd=args.dashboard_cmd_file,
            drive=args.drive_file,
            osr2_sender=RoomFunestra.MAIN.label,
            osr2_port=args.osr2_port,
            osr2_readout=(osr2_readout_path(args.state_dir, RoomFunestra.MAIN.label)
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
            osr2_readout=osr2_readout_path(state_dir, RoomFunestra.MAIN.label),
        )

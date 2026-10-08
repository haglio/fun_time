"""The files the Main Funestra is driven through, as Fun Time launches it with them."""
from __future__ import annotations

from dataclasses import dataclass

from player_core.funestra import Channels

from .cli import play_points_path


@dataclass(frozen=True)
class MainChannels(Channels):
    @classmethod
    def from_args(cls, args) -> MainChannels:
        return cls(
            playlist=args.playlist,
            command=args.command_file,
            paused=args.paused_file,
            status=args.status_file,
            play_points=play_points_path(args),
            console=args.console_file,
            dashboard_cmd=args.dashboard_cmd_file,
            drive=args.drive_file,
            tcode_host=args.tcode_host,
            tcode_port=args.tcode_port,
        )

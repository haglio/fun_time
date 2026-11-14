from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from funestra.channel_manifest import channel_argv, channels_from_args
from funestra.funestra import Channels

from fun_time.config import SatelliteChannel
from fun_time.osr2_section import osr2_readout_path

SATELLITE_CHANNELS = ("playlist", "command", "paused", "status", "notice", "play_points", "hud",
                      "dashboard_cmd", "drive", "osr2_sender", "osr2_port", "osr2_readout")

FILES_EACH_SATELLITE_HAS = {"playlist": "playlist", "command": "cmd", "paused": "paused",
                             "status": "status", "notice": "notice", "hud": "hud"}

PLACEMENT = (
    ("x", "--x"),
    ("y", "--y"),
    ("width", "--width"),
    ("height", "--height"),
    ("title", "--title"),
    ("taskbar_identity", "--taskbar-identity"),
    ("icon", "--icon"),
    ("tiles", "--tile"),
)


def _argv(record, table) -> list[str]:
    words: list[str] = []
    for field, flag in table:
        value = getattr(record, field)
        if isinstance(value, bool):
            words += [flag] if value else []
        elif value is not None:
            words += [flag, str(value)]
    return words


@dataclass(frozen=True)
class SatelliteChannels(Channels):
    @classmethod
    def from_args(cls, args) -> SatelliteChannels:
        return cls(**channels_from_args(args, SATELLITE_CHANNELS))

    @classmethod
    def from_channel(cls, channel: SatelliteChannel, commands, funestra: str, *,
           play_points: Path | None = None) -> SatelliteChannels:
        return cls(
            play_points=play_points,
            dashboard_cmd=Path(commands.dashboard_cmd_file),
            drive=Path(commands.genau_drive_file),
            osr2_sender=funestra,
            osr2_port=int(commands.osr2_slot_port),
            osr2_readout=osr2_readout_path(Path(commands.state_dir), funestra),
            **{field: getattr(channel, f"{kind}_file")
               for field, kind in FILES_EACH_SATELLITE_HAS.items()},
        )

    def to_argv(self) -> list[str]:
        return channel_argv(self, SATELLITE_CHANNELS)


@dataclass(frozen=True)
class WindowPlacement:
    x: int | None = None
    y: int | None = None
    width: int = 1200
    height: int = 900
    title: str = "Satellite"
    taskbar_identity: str | None = None
    icon: Path | None = None
    tiles: bool = False

    @classmethod
    def from_args(cls, args) -> WindowPlacement:
        return cls(x=args.x, y=args.y, width=args.width, height=args.height,
                   title=args.title, taskbar_identity=args.taskbar_identity,
                   icon=args.icon, tiles=args.tile)

    def to_argv(self) -> list[str]:
        return _argv(self, PLACEMENT)

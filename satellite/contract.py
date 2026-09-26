"""The values a satellite is launched with, named once -- adding a file is one
field and one row.  The spellings are a launcher contract: the flags go into a
real command line and the manifest keys are read under ``optionxform = str``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: field, the flag it is given by, and the kind of file a side carries it as in
#: the launch manifest -- ``None`` where the manifest does not spell it per side.
CHANNELS = (
    ("playlist", "--playlist", "playlist"),
    ("command", "--command-file", "cmd"),
    ("paused", "--paused-file", "paused"),
    ("status", "--status-file", "status"),
    ("play_points", "--play-points-file", None),
    ("hud", "--hud-file", "hud"),
    ("dashboard_cmd", "--dashboard-cmd-file", None),
)

PLACEMENT = (
    ("x", "--x"),
    ("y", "--y"),
    ("width", "--width"),
    ("height", "--height"),
    ("title", "--title"),
    ("taskbar_identity", "--taskbar-identity"),
    ("tiles", "--tile"),
)


def _argv(record, table) -> list[str]:
    words: list[str] = []
    for field, flag, *_ in table:
        value = getattr(record, field)
        if isinstance(value, bool):
            words += [flag] if value else []
        elif value is not None:
            words += [flag, str(value)]
    return words


@dataclass(frozen=True)
class SatelliteChannels:
    """The files one satellite reads and writes; each is optional."""

    playlist: Path | None = None
    command: Path | None = None
    paused: Path | None = None
    status: Path | None = None
    play_points: Path | None = None
    hud: Path | None = None
    dashboard_cmd: Path | None = None

    @classmethod
    def from_args(cls, args) -> SatelliteChannels:
        # Spelled out rather than read off CHANNELS: the dead-code gate asks
        # that every declared option be read where it can see it.
        return cls(
            playlist=args.playlist,
            command=args.command_file,
            paused=args.paused_file,
            status=args.status_file,
            play_points=args.play_points_file,
            hud=args.hud_file,
            dashboard_cmd=args.dashboard_cmd_file,
        )

    @classmethod
    def from_manifest(cls, commands, player: str, *,
                      play_points: Path | None = None) -> SatelliteChannels:
        return cls(
            play_points=play_points,
            dashboard_cmd=Path(commands.dashboard_cmd_file),
            **{field: Path(commands.player_file(player, kind))
               for field, _flag, kind in CHANNELS if kind is not None},
        )

    def to_argv(self) -> list[str]:
        return _argv(self, CHANNELS)


@dataclass(frozen=True)
class WindowPlacement:
    """Where a satellite's window opens, what it is called, whose it is, and whether
    it tiles a portrait picture across it."""

    x: int | None = None
    y: int | None = None
    width: int = 1200
    height: int = 900
    title: str = "Satellite"
    taskbar_identity: str | None = None
    tiles: bool = False

    @classmethod
    def from_args(cls, args) -> WindowPlacement:
        return cls(x=args.x, y=args.y, width=args.width, height=args.height,
                   title=args.title, taskbar_identity=args.taskbar_identity,
                   tiles=args.tile)

    def to_argv(self) -> list[str]:
        return _argv(self, PLACEMENT)

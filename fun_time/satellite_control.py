"""fun_time's side of the native satellite protocol: write commands, read status.

The native satellite players (this repo's ``satellite`` package) are driven
through a file quartet; this module is fun_time's end of it, the counterpart to
the player's own runtime/status.  Commands are appended one verb per line to the
player's command file (it drains them with ``player_core.file_channel``), and
where the clip has got to is read back from the status file the player publishes.

Its sibling ``broker_control`` is the same idea for the OSR2 broker, with the
one difference spelled out there: that channel holds a single verb, not a queue.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def write_satellite_command(cmd_file: Path, verb: str) -> None:
    """Queue *verb* for a satellite by appending it, one per line.

    Appended rather than overwritten so a burst of commands issued before the
    player next drains its file all survive, matching how the player reads them.
    """
    cmd_file.parent.mkdir(parents=True, exist_ok=True)
    with cmd_file.open("a", encoding="utf-8") as fh:
        fh.write(verb.rstrip("\n") + "\n")


@dataclass(frozen=True)
class SatelliteStatus:
    video: str = ""
    position_ms: int = 0
    duration_ms: int = 0
    paused: bool = False
    locked: bool = False

    @property
    def fraction(self) -> float | None:
        """How far through the clip, 0..1 — None when the duration is not yet known."""
        if self.duration_ms <= 0:
            return None
        return self.position_ms / self.duration_ms


def read_satellite_status(status_file: Path) -> SatelliteStatus:
    """Parse a native satellite's status file; an absent or blank file reads empty."""
    try:
        text = Path(status_file).read_text(encoding="utf-8")
    except OSError:
        return SatelliteStatus()
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return SatelliteStatus(
        video=fields.get("video", ""),
        position_ms=_int(fields.get("position_ms")),
        duration_ms=_int(fields.get("duration_ms")),
        paused=fields.get("paused") == "1",
        locked=fields.get("locked") == "1",
    )


def _int(value: str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except ValueError:
        return 0

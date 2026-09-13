"""fun_time's side of the native satellite protocol: what a satellite reports.

The native satellite players (this repo's ``satellite`` package) are driven
through a file quartet; verbs go in through ``player_core.file_channel``'s
append (``satellite_groups.send_satellite``), and where the clip has got to is
read back here from the status file the player publishes.

Its sibling ``broker_control`` is the same idea for the OSR2 broker, with the
one difference spelled out there: that channel holds a single verb, not a queue.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from app_support.file_channel import read_key_values
from player_core.status import PlayerStatus, parse_status


@dataclass(frozen=True)
class SatelliteStatus(PlayerStatus):
    """The family's five, and how many clips the satellite's playlist holds."""

    playlist_length: int = 0

    @property
    def fraction(self) -> float | None:
        """How far through the clip, 0..1 — None when the duration is not yet known."""
        if self.duration_ms <= 0:
            return None
        return self.position_ms / self.duration_ms


def read_satellite_status(status_file: Path) -> SatelliteStatus:
    """Parse a native satellite's status file; an absent or blank file reads empty."""
    try:
        fields = read_key_values(Path(status_file))
    except OSError:
        return SatelliteStatus()
    return SatelliteStatus(
        **asdict(parse_status(fields)),
        playlist_length=_int(fields.get("playlist_length")),
    )


def _int(value: str | None) -> int:
    try:
        return int(value.strip()) if value is not None else 0
    except ValueError:
        return 0

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from app_support.file_channel import read_key_values
from funestra.status import parse_status

from .funestra_status import ScriptedStatus, scripted_fields


@dataclass(frozen=True)
class SatelliteStatus(ScriptedStatus):
    playlist_length: int = 0
    volume: int | None = None
    muted: bool | None = None

    @property
    def fraction(self) -> float | None:
        if self.duration_ms <= 0:
            return None
        return self.position_ms / self.duration_ms


def read_satellite_status(status_file: Path, *,
                          fallback: SatelliteStatus | None = None) -> SatelliteStatus:
    try:
        fields = read_key_values(Path(status_file))
    except OSError:
        return fallback or SatelliteStatus()
    return _satellite_status_from(fields)


def _satellite_status_from(fields: dict) -> SatelliteStatus:
    return SatelliteStatus(
        **asdict(parse_status(fields)),
        **scripted_fields(fields),
        playlist_length=_int(fields.get("playlist_length")),
        volume=int(fields["volume"]) if fields.get("volume", "").strip().isdigit() else None,
        muted={"1": True, "0": False}.get(fields.get("muted", "").strip()),
    )


def published_satellite_status(status_file: Path) -> SatelliteStatus | None:
    try:
        fields = read_key_values(Path(status_file))
    except OSError:
        return None
    return _satellite_status_from(fields) if "speed" in fields else None


def _int(value: str | None) -> int:
    try:
        return int(value.strip()) if value is not None else 0
    except ValueError:
        return 0

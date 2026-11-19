from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from funestra.funestra import Channels

from fun_time.config import SatelliteChannel
from fun_time.osr2_section import osr2_readout_path

SATELLITE_CHANNELS = ("playlist", "command", "paused", "status", "notice", "play_points", "hud",
                      "dashboard_cmd", "drive", "osr2_sender", "osr2_port", "osr2_readout")

FILES_EACH_SATELLITE_HAS = {"playlist": "playlist", "command": "cmd", "paused": "paused",
                             "status": "status", "notice": "notice", "hud": "hud"}


@dataclass(frozen=True)
class SatelliteChannels(Channels):
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


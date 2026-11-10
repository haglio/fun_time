from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from satellite.contract import SatelliteChannels

from .fun_time_funestras import FunTimeFunestra
from .window_layout import WindowRect


@dataclass(frozen=True)
class SatelliteLaunch:
    funestra: FunTimeFunestra
    channels: SatelliteChannels
    log_file: str | Path
    rect: WindowRect


def for_funestra(launch: SatelliteLaunch, funestra: FunTimeFunestra) -> SatelliteLaunch:
    if launch.funestra is not funestra:
        raise ValueError(f"a {launch.funestra.label} launch was handed to {funestra.label}")
    return launch

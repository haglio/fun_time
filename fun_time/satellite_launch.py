from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from funestra.play_points import play_points_filename

from satellite.contract import SatelliteChannels

from .fun_time_funestras import FunTimeFunestra
from .manifest import LaunchManifest
from .modes import PLAYLIST_LANDSCAPE, PLAYLIST_PORTRAIT, build_playlist_file_path
from .window_layout import WindowRect

_PLAYLISTS = {FunTimeFunestra.PORTRAIT: PLAYLIST_PORTRAIT, FunTimeFunestra.LANDSCAPE: PLAYLIST_LANDSCAPE}


@dataclass(frozen=True)
class SatelliteFeed:
    funestra: FunTimeFunestra
    channels: SatelliteChannels


def satellite_feed(m: LaunchManifest, funestra: FunTimeFunestra, state_dir: Path) -> SatelliteFeed:
    label = funestra.label
    channel = m.satellite(funestra)
    return SatelliteFeed(
        funestra=funestra,
        channels=replace(
            SatelliteChannels.from_channel(
                channel, m.commands, label, play_points=state_dir / play_points_filename(label)),
            playlist=build_playlist_file_path(state_dir, _PLAYLISTS[funestra])),
    )


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

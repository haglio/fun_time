from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from funestra.modes import SatellitesMode

from .fun_time_funestras import FunTimeFunestra

if TYPE_CHECKING:
    from .bridge_records import BridgeConfig
    from .shared_state import BridgeState


class RfbMode(StrEnum):
    RFB = "rfb"
    ORIGENERATOR = "origenerator"


KINO_MODE = SatellitesMode.KINO
ORIGENERATOR_MODE = SatellitesMode.ORIGENERATOR

OPEN_SLIDESHOW = "open_slideshow"
CLOSE_SLIDESHOW = "close_slideshow"
CLOSE_SLIDESHOWS = "CLOSE_SLIDESHOWS"


def origenerator_shows(mode: SatellitesMode) -> bool:
    return mode == ORIGENERATOR_MODE


def said_about(satellite: str, verb: str) -> str:
    return f"{satellite}_{verb}"


def mode_command(satellite: str, mode: SatellitesMode) -> str:
    return said_about(satellite, f"{mode}_activate")


SATELLITE_MODE_COMMANDS: dict[str, tuple[FunTimeFunestra, SatellitesMode]] = {
    mode_command(funestra.label, mode): (funestra, mode)
    for funestra in FunTimeFunestra.SATELLITES
    for mode in (KINO_MODE, ORIGENERATOR_MODE)
}

BOTH_SATELLITES_MODE_COMMANDS: dict[str, SatellitesMode | None] = {
    "satellites_kino_activate": KINO_MODE,
    "satellites_origenerator_activate": ORIGENERATOR_MODE,
    "satellites_toggle": None,
}


def hosts(funestra: FunTimeFunestra | None, state: BridgeState, config: BridgeConfig) -> bool:
    return (funestra in FunTimeFunestra.SATELLITES and config.hosts_origenerator
            and state.satellite(funestra).handed_over)


def hosted_satellites(state: BridgeState, config: BridgeConfig) -> frozenset[str]:
    return frozenset(funestra.label for funestra in FunTimeFunestra.SATELLITES
                     if hosts(funestra, state, config))


def hosts_a_satellite(state: BridgeState, config: BridgeConfig) -> bool:
    return config.hosts_origenerator and state.origenerator_holds_a_satellite()


from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from .fun_time_funestras import FunTimeFunestra

if TYPE_CHECKING:
    from .bridge_records import BridgeConfig
    from .shared_state import BridgeState


class RfbMode(StrEnum):
    RFB = "rfb"
    ORIGENERATOR = "origenerator"


WHAT_THE_DIM_BUTTONS_SAY = "Origenerator is still starting"

OPEN_SLIDESHOW = "open_slideshow"
CLOSE_SLIDESHOWS = "CLOSE_SLIDESHOWS"


def said_about(satellite: str, verb: str) -> str:
    return f"{satellite}_{verb}"


def hosts(funestra: FunTimeFunestra | None, state: BridgeState, config: BridgeConfig) -> bool:
    return (funestra in FunTimeFunestra.SATELLITES and config.hosts_origenerator
            and state.satellite(funestra).handed_over)


def hosted_satellites(state: BridgeState, config: BridgeConfig) -> frozenset[str]:
    return frozenset(funestra.label for funestra in FunTimeFunestra.SATELLITES
                     if hosts(funestra, state, config))


def hosts_a_satellite(state: BridgeState, config: BridgeConfig) -> bool:
    return bool(hosted_satellites(state, config))


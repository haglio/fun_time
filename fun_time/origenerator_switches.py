from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from .fun_time_funestras import FunTimeFunestra

if TYPE_CHECKING:
    from .bridge_records import BridgeConfig


class RfbMode(StrEnum):
    RFB = "rfb"
    ORIGENERATOR = "origenerator"


WHAT_THE_DIM_BUTTONS_SAY = "Origenerator is still starting"

OPEN_SLIDESHOW = "open_slideshow"
CLOSE_SLIDESHOWS = "CLOSE_SLIDESHOWS"


def said_about(satellite: str, verb: str) -> str:
    return f"{satellite}_{verb}"


def hosts(funestra: FunTimeFunestra | None, config: BridgeConfig) -> bool:
    return funestra in FunTimeFunestra.SATELLITES and config.hosts_origenerator


def hosted_satellites(config: BridgeConfig) -> frozenset[str]:
    return frozenset(funestra.label for funestra in FunTimeFunestra.SATELLITES
                     if hosts(funestra, config))


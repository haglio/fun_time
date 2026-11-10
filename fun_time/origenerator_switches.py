from __future__ import annotations

from enum import StrEnum


class RfbMode(StrEnum):
    RFB = "rfb"
    ORIGENERATOR = "origenerator"


WHAT_THE_DIM_BUTTONS_SAY = "Origenerator is still starting"

OPEN_SLIDESHOW = "open_slideshow"
CLOSE_SLIDESHOWS = "CLOSE_SLIDESHOWS"


def said_about(satellite: str, verb: str) -> str:
    return f"{satellite}_{verb}"

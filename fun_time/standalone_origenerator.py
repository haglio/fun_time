from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from app_support import origenerator_handoff as handoff

RELEASE = "RELEASE"
_THE_SHAPES_OF_A_SLIDESHOW = frozenset({"portrait", "landscape", "both"})


class OpenOrigenerator(NamedTuple):
    pid: int
    starting: bool
    place: Path
    showing_a_slideshow: bool

    @property
    def checkout(self) -> Path:
        return self.place.parent


def the_open_origenerator(origenerator_dir: str | Path) -> OpenOrigenerator | None:
    for place in handoff.meeting_places(Path(origenerator_dir)):
        offer = handoff.the_offer_in(place)
        if offer is not None:
            shape = handoff.the_shape_shown_in(place, by=offer.process)
            return OpenOrigenerator(offer.process.pid, starting=offer.starting, place=place,
                                    showing_a_slideshow=shape in _THE_SHAPES_OF_A_SLIDESHOW)
    return None


def claim_the_osr2(origenerator_dir: str | Path) -> None:
    handoff.claim_the_osr2(*handoff.meeting_places(Path(origenerator_dir)))

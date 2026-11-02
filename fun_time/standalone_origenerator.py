from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from app_support import origenerator_handoff as handoff

from .fun_time_funestras import FunTimeFunestra

RELEASE = "RELEASE"
_THE_SATELLITES_SHOWING = {
    "portrait": (FunTimeFunestra.PORTRAIT,),
    "landscape": (FunTimeFunestra.LANDSCAPE,),
    "both": FunTimeFunestra.SATELLITES,
}


class OpenOrigenerator(NamedTuple):
    pid: int
    starting: bool
    place: Path
    slideshow_on: tuple[FunTimeFunestra, ...]

    @property
    def checkout(self) -> Path:
        return self.place.parent


def the_open_origenerator(origenerator_dir: str | Path) -> OpenOrigenerator | None:
    for place in handoff.meeting_places(Path(origenerator_dir)):
        offer = handoff.the_offer_in(place)
        if offer is not None:
            shape = handoff.the_shape_shown_in(place, by=offer.process)
            return OpenOrigenerator(offer.process.pid, starting=offer.starting, place=place,
                                    slideshow_on=_THE_SATELLITES_SHOWING.get(shape, ()))
    return None


def claim_the_osr2(origenerator_dir: str | Path) -> None:
    handoff.claim_the_osr2(*handoff.meeting_places(Path(origenerator_dir)))

"""Where the hosted Origenerator shows: on a Satellite Funestra in that
Funestra's own Origenerator mode (:mod:`fun_time.funestra_handover`), and in the
Random Favs Browser's place while the strip under the dashboard says so.  Each
of the three switches on its own and tears nothing down; what a session opens
in is ``docs/resuming-a-session.md``.
"""
from __future__ import annotations

from enum import StrEnum

from funestra.modes import SatellitesMode

from .room_funestras import RoomFunestra


class RfbSlot(StrEnum):
    """What holds the Random Favs Browser's place: the browser, or the hosted
    Origenerator's own window over it."""

    RFB = "rfb"
    ORIGENERATOR = "origenerator"


KINO_MODE = SatellitesMode.KINO
ORIGENERATOR_MODE = SatellitesMode.ORIGENERATOR

# Said to the hosted app about one side: take its Funestra and put a slideshow on
# it, or let it go.  A crossing parking the app lets go of every slideshow at once.
OPEN_SLIDESHOW = "open_slideshow"
CLOSE_SLIDESHOW = "close_slideshow"
CLOSE_SLIDESHOWS = "CLOSE_SLIDESHOWS"


def origenerator_shows(mode: SatellitesMode) -> bool:
    return mode == ORIGENERATOR_MODE


def said_about(side: str, verb: str) -> str:
    return f"{side}_{verb}"


def mode_command(side: str, mode: SatellitesMode) -> str:
    return said_about(side, f"{mode}_activate")


SATELLITE_MODE_COMMANDS: dict[str, tuple[RoomFunestra, SatellitesMode]] = {
    mode_command(funestra.label, mode): (funestra, mode)
    for funestra in RoomFunestra.SATELLITES
    for mode in (KINO_MODE, ORIGENERATOR_MODE)
}

# Both satellites at once.  The toggle's target is None: like the F key, it
# puts both in Origenerator mode unless both already are.
BOTH_SATELLITES_MODE_COMMANDS: dict[str, SatellitesMode | None] = {
    "satellites_kino_activate": KINO_MODE,
    "satellites_origenerator_activate": ORIGENERATOR_MODE,
    "satellites_toggle": None,
}

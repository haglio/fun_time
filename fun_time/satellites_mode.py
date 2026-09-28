"""The satellite side's two modes: kino and origenerator.

``kino`` is the session as ever, the Random Favs Browser and the two satellite
players; ``origenerator`` puts the hosted app over the browser's rect and its
slideshows on the players (:mod:`fun_time.player_handover`).  Switching tears
nothing down, and what a session opens in is ``docs/resuming-a-session.md``.
"""
from __future__ import annotations

from player_core.modes import SatellitesMode

KINO_MODE = SatellitesMode.KINO
ORIGENERATOR_MODE = SatellitesMode.ORIGENERATOR

OPEN_SHOWS = "OPEN_SHOWS"
CLOSE_SHOWS = "CLOSE_SHOWS"

# The mode every session is BUILT in (mirroring mode_plan.STARTUP_MAIN_MODE):
# the satellites launch as players, and a room left in origenerator mode takes it
# up once the hosted app answers (docs/resuming-a-session.md).
STARTUP_SATELLITES_MODE = KINO_MODE


def origenerator_shows(satellites_mode: SatellitesMode) -> bool:
    """Whether the hosted Origenerator owns the satellite side in this mode."""
    return satellites_mode == ORIGENERATOR_MODE


def toggled_satellites_mode(satellites_mode: SatellitesMode) -> SatellitesMode:
    """The other mode — what the one toggle hotkey switches to."""
    return KINO_MODE if origenerator_shows(satellites_mode) else ORIGENERATOR_MODE


def mode_a_command_settles_on(command: str, satellites_mode: SatellitesMode) -> SatellitesMode:
    """The mode *command* puts the satellite side in, from the one it is in."""
    named = MODE_BY_COMMAND[command]
    return toggled_satellites_mode(satellites_mode) if named is None else named


# The three commands that settle this side's mode; the toggle's target is None
# because only the room it is pressed in knows which mode that is.
MODE_BY_COMMAND: dict[str, SatellitesMode | None] = {
    "origenerator_activate": ORIGENERATOR_MODE,
    "satellites_kino_activate": KINO_MODE,
    "satellites_toggle": None,
}

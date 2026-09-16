"""The satellite side's two modes: video and origenerator.

The main slot's modes (:mod:`fun_time.mode_plan`) decide which player owns the
secondary monitor's shared rect; this axis decides what the whole satellite
side shows.  In ``video`` mode it is the session as ever: the Random Favs
Browser and the two satellite players.  In ``origenerator`` mode the hosted
Origenerator sits over the RFB's rect and plays its slideshows on the two
players (:mod:`fun_time.player_handover`); which of them it holds is what its
status file says.

Like the main modes, switching is cheap and total: nothing is torn down.
"""
from __future__ import annotations

VIDEO_MODE = "video"
ORIGENERATOR_MODE = "origenerator"

OPEN_SHOWS = "OPEN_SHOWS"
CLOSE_SHOWS = "CLOSE_SHOWS"

# The mode every session is BUILT in (mirroring mode_plan.STARTUP_MAIN_MODE):
# the satellites launch as players, and a session resuming into origenerator
# mode is seeded as a switch out of here.
STARTUP_SATELLITES_MODE = VIDEO_MODE


def origenerator_shows(satellites_mode: str) -> bool:
    """Whether the hosted Origenerator owns the satellite side in this mode."""
    return satellites_mode == ORIGENERATOR_MODE


def toggled_satellites_mode(satellites_mode: str) -> str:
    """The other mode — what the one toggle hotkey switches to."""
    return VIDEO_MODE if origenerator_shows(satellites_mode) else ORIGENERATOR_MODE

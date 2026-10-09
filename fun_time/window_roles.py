"""Topmost band policy for the managed Fun Time windows.

Startup, omnipause and mode switches all read this ONE policy, so they can
never disagree about a window's topmost band, which is the drift that leaves
the main player stranded on top after entering omnipause.

The satellite, dashboard and Main Player windows each own a screen rect and
never overlap, so they are unconditionally topmost.  One pair SHARES a rect and
stacks against each other: the Random Favs Browser under the hosted
Origenerator's window (``restack_rfb_slot``).
"""
from __future__ import annotations

from .satellites_mode import KINO_MODE, origenerator_shows

# Windows with their own screen rect — always topmost; order among them is
# irrelevant because they never overlap.  The log stream is a child widget of the
# dashboard window, not a role of its own, so it rides the dashboard's band.
# Kino and Genau both run on the Main Player's one window.
FIXED_TOPMOST_ROLES: tuple[str, ...] = ("portrait", "landscape", "dashboard", "main_player")

# The hosted Origenerator's one window.  Its shows play on the satellite
# players, not in windows of its own (fun_time.player_handover).
ORIGENERATOR_ROLE = "origenerator"

# The browser and the hosted app's window share the RFB's rect.  In
# origenerator mode the hosted window covers it and the browser leaves the band,
# so no promotion, however late it lands, can stack the browser back over it.
RFB_SLOT_ROLES: tuple[str, ...] = ("rfb", ORIGENERATOR_ROLE)

# The caption that window wears, resolved together with the app's PID: by
# title alone a standalone Origenerator of his would match.
ORIGENERATOR_TITLE = "Origenerator"

# Every window role the bridge manages, in promotion order.
MANAGED_ROLES: tuple[str, ...] = FIXED_TOPMOST_ROLES + RFB_SLOT_ROLES


def role_topmost(role: str, satellites_mode: str = KINO_MODE) -> bool:
    """Whether *role*'s window belongs in the TOPMOST band in this mode.

    The Random Favs Browser shares its rect with the hosted app's main window,
    so each is topmost only where it shows something, and the covered one
    stays out of the band: the hosted app's window rides *satellites_mode*.
    Every other managed window owns its own rect and is unconditionally topmost.
    """
    if role == ORIGENERATOR_ROLE:
        return origenerator_shows(satellites_mode)
    if role == "rfb":
        # The RFB shares its rect with the hosted app's main window, so it is
        # mode-dependent the same way the pair is: in origenerator mode that
        # window covers it completely, and promoting it there only puts it
        # briefly ABOVE its cover — HWND_TOPMOST inserts at the top of the band,
        # so every re-band (leaving OmniPause, a mode switch, the startup pass)
        # flashed the browser over Origenerator on its way past.
        return not origenerator_shows(satellites_mode)
    return True


def visible_roles(satellites_mode: str = KINO_MODE) -> list[str]:
    """Every managed role whose window this mode keeps on screen: the browser
    counts while the hosted window covers it, since it is covered, not parked."""
    rfb_slot = RFB_SLOT_ROLES if origenerator_shows(satellites_mode) else ("rfb",)
    return [*FIXED_TOPMOST_ROLES, *rfb_slot]

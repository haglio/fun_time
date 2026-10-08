"""Topmost band policy for the managed Fun Time windows.

Startup, omnipause and mode switches all read this ONE policy, so they can
never disagree about a window's topmost band, which is the drift that leaves
the main player stranded on top after entering omnipause.

The satellite and dashboard windows each own a screen rect and never overlap,
so they are unconditionally topmost.  Two pairs SHARE a rect and stack against
each other: the main player under Genau's HUD
(``role_windows.WindowRoles.restack_main_slot``), and the Random Favs Browser
under the hosted Origenerator's window (``restack_rfb_slot``).
"""
from __future__ import annotations

from .mode_plan import main_player_displays
from .satellites_mode import KINO_MODE, origenerator_shows

# Windows with their own screen rect — always topmost; order among them is
# irrelevant because they never overlap.  The log stream is a child widget of the
# dashboard window, not a role of its own, so it rides the dashboard's band.
FIXED_TOPMOST_ROLES: tuple[str, ...] = ("portrait", "landscape", "dashboard")

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

# What Genau's window calls itself, plainly and while its HUD is over the main
# player's video.  Passed to it on the launch, like each satellite's, and
# matched EXACTLY: a substring match found both only by luck.
GENAU_TITLE = "Genau"
GENAU_KINO_TITLE = "Kino Main Player+Genau"
GENAU_TITLES = (GENAU_TITLE, GENAU_KINO_TITLE)

# The two players that share the main slot's rect and therefore need
# explicit stacking (the main player under Genau's HUD in kino mode).
MAIN_SLOT_ROLES: tuple[str, ...] = ("main_player", "genau")

# Every window role the bridge manages, in promotion order.
MANAGED_ROLES: tuple[str, ...] = (
    FIXED_TOPMOST_ROLES + RFB_SLOT_ROLES + MAIN_SLOT_ROLES
)


def role_topmost(role: str, main_mode: str, satellites_mode: str = KINO_MODE) -> bool:
    """Whether *role*'s window belongs in the TOPMOST band in these modes.

    The main player is mode-dependent, sharing a rect with Genau, and so is the Random Favs
    Browser, which shares its own with the hosted app's main window: each is
    topmost only where it shows something, and the hidden slot-mate stays out of
    the band.  Genau is in the band in both modes and promoted last, so it lands
    ABOVE the main player.  The hosted app's window shares the RFB's rect the same
    way, so it rides *satellites_mode* as the main player rides *main_mode*.  Every other managed window owns
    its own rect and is unconditionally topmost.
    """
    if role == "main_player":
        return main_player_displays(main_mode)
    if role == "genau":
        return True
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


def visible_roles(main_mode: str, satellites_mode: str = KINO_MODE) -> list[str]:
    """Every managed role whose window these modes keep on screen."""
    rfb_slot = RFB_SLOT_ROLES if origenerator_shows(satellites_mode) else ("rfb",)
    return [*FIXED_TOPMOST_ROLES, *rfb_slot, *visible_main_slot_roles(main_mode)]


def visible_main_slot_roles(main_mode: str) -> tuple[str, ...]:
    """Which of the two main-slot players *main_mode* has on the screen: Genau
    in genau, and both in kino mode, where Genau's HUD sits over the main player's video.

    Read by anything that acts on "the main player's window", because the pair
    shares one rect and the idle one is parked — minimizing a window the mode has
    already put away is what drags it back into view.  Derived from the band
    policy above rather than listed again: a main-slot player is in the topmost
    band exactly when it is showing something, so the two answers cannot drift.
    """
    return tuple(role for role in MAIN_SLOT_ROLES if role_topmost(role, main_mode))

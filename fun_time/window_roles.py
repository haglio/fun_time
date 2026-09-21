"""Topmost band policy for the managed Fun Time windows.

Startup, omnipause and mode switches all read this ONE policy, so they can
never disagree about a window's topmost band, which is the drift that leaves
the main player stranded on top after entering omnipause.

The satellite / dashboard / RFB windows each own a screen rect and never
overlap, so they are unconditionally topmost.  The main player and Genau are the exception:
they SHARE one rect, so they float above the desktop AND stack against each
other -- in video mode Genau's HUD sits just above the main player's video, an order
``role_windows.WindowRoles.restack_main_slot`` enforces rather than these flags;
in genau mode Genau owns the display and the main player is hidden.
"""
from __future__ import annotations

from .mode_plan import main_player_displays
from .satellites_mode import VIDEO_MODE, origenerator_shows

# Windows with their own screen rect — always topmost; order among them is
# irrelevant because they never overlap.  The log stream is a child widget of the
# dashboard window, not a role of its own, so it rides the dashboard's band.
FIXED_TOPMOST_ROLES: tuple[str, ...] = ("rfb", "portrait", "landscape", "dashboard")

# The hosted Origenerator's one window: it SHARES the RFB's rect, so like the
# main-slot pair it is mode-dependent — in the band only while the satellites
# are in origenerator mode.  Listed AFTER the fixed roles because HWND_TOPMOST
# inserts at the top of the band: promoted later means stacked above the window
# it covers.  Its shows play on the satellite players, not in windows of its
# own (fun_time.player_handover).
ORIGENERATOR_ROLE = "origenerator"

# The caption that window wears, resolved together with the app's PID: by
# title alone a standalone Origenerator of his would match.
ORIGENERATOR_TITLE = "Origenerator"

# What Genau's window calls itself, plainly and while its HUD is over the main
# player's video.  Passed to it on the launch, like each satellite's, and
# matched EXACTLY: a substring match found both only by luck.
GENAU_TITLE = "Genau"
GENAU_VIDEO_TITLE = "Video Main Player+Genau"
GENAU_TITLES = (GENAU_TITLE, GENAU_VIDEO_TITLE)

# The two players that share the main slot's rect and therefore need
# explicit stacking (the main player under Genau's HUD in video mode).
MAIN_SLOT_ROLES: tuple[str, ...] = ("main_player", "genau")

# Every window role the bridge manages, in promotion order.
MANAGED_ROLES: tuple[str, ...] = (
    FIXED_TOPMOST_ROLES + (ORIGENERATOR_ROLE,) + MAIN_SLOT_ROLES
)


def role_topmost(role: str, main_mode: str, satellites_mode: str = VIDEO_MODE) -> bool:
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


def visible_roles(main_mode: str, satellites_mode: str = VIDEO_MODE) -> list[str]:
    """Every managed role whose window these modes keep on screen."""
    origenerator = (ORIGENERATOR_ROLE,) if origenerator_shows(satellites_mode) else ()
    return [*FIXED_TOPMOST_ROLES, *origenerator, *visible_main_slot_roles(main_mode)]


def visible_main_slot_roles(main_mode: str) -> tuple[str, ...]:
    """Which of the two main-slot players *main_mode* has on the screen: Genau
    in genau, and both in video mode, where Genau's HUD sits over the main player's video.

    Read by anything that acts on "the main player's window", because the pair
    shares one rect and the idle one is parked — minimizing a window the mode has
    already put away is what drags it back into view.  Derived from the band
    policy above rather than listed again: a main-slot player is in the topmost
    band exactly when it is showing something, so the two answers cannot drift.
    """
    return tuple(role for role in MAIN_SLOT_ROLES if role_topmost(role, main_mode))

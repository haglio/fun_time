"""Topmost band policy for the managed Fun Time windows.

Startup, omnipause and mode switches all read this ONE policy, so they can
never disagree about a window's topmost band — the drift that once left Nau
stranded on top after entering omnipause.

The satellite / dashboard / RFB windows each own a screen rect and
never overlap, so they are unconditionally topmost.  The main-slot players Nau and
Genau are the exception: they SHARE one rect (in hybrid Genau's transparent HUD
overlays Nau's video), so they both need to float above the desktop AND be
stacked relative to each other:

  * nau mode    — Nau owns the display and is topmost (Genau hidden).
  * hybrid mode — Nau is topmost so the video floats up, and Genau is stacked
                  just ABOVE it so the HUD overlays the video.  That ordering is
                  enforced by promoting Nau before Genau (see the dispatch
                  loop's ``_restack_main_slot``), not by these flags.
  * genau mode  — Genau owns the display and is topmost (Nau hidden).
"""
from __future__ import annotations

from .mode_plan import genau_active, nau_displays
from .satellites_mode import origenerator_shows

# Windows with their own screen rect — always topmost; order among them is
# irrelevant because they never overlap.  The log stream is a child widget of the
# dashboard window, not a role of its own, so it rides the dashboard's band.
FIXED_TOPMOST_ROLES: tuple[str, ...] = ("rfb", "portrait", "landscape", "dashboard")

# The hosted Origenerator's windows: its main window SHARES the RFB's rect and
# its region shows share the players', so like the main-slot pair they are
# mode-dependent — in the band only while the satellites are in origenerator
# mode.  Listed AFTER the fixed roles because HWND_TOPMOST inserts at the top
# of the band: promoted later means stacked above the windows they cover.
ORIGENERATOR_ROLES: tuple[str, ...] = (
    "origenerator", "origenerator_portrait", "origenerator_landscape",
)

# The window captions the hosted app gives its three windows (its
# ``fun_time_mode`` names the show titles), resolved together with its PID —
# by pid alone the three are indistinguishable, and by title alone a
# standalone Origenerator's windows would match.
ORIGENERATOR_ROLE_TITLES: dict[str, str] = {
    "origenerator": "Origenerator",
    "origenerator_portrait": "Origenerator Portrait",
    "origenerator_landscape": "Origenerator Landscape",
}

# The two players that share the main player's rect and therefore need
# explicit stacking (Nau under Genau's HUD in hybrid).
PRIMARY_SLOT_ROLES: tuple[str, ...] = ("nau", "genau")

# Every window role the bridge manages, in promotion order.
MANAGED_ROLES: tuple[str, ...] = (
    FIXED_TOPMOST_ROLES + ORIGENERATOR_ROLES + PRIMARY_SLOT_ROLES
)


def role_topmost(role: str, main_mode: str, satellites_mode: str = "player") -> bool:
    """Whether *role*'s window belongs in the TOPMOST band in these modes.

    Both main-slot players are mode-dependent, because they share a rect —
    and so is the Random Favs Browser, which shares its own with the hosted
    app's main window:
    each is topmost only in the modes where it shows something, and the hidden
    slot-mate stays out of the band entirely.  Genau is promoted last, so being
    in the band at all puts it ABOVE Nau — which is what hybrid wants and what
    nau mode must not have.  The origenerator trio shares rects the same way —
    with the RFB and the two players — so it rides *satellites_mode* exactly as
    the pair rides *main_mode*.  Every other managed window owns its own rect,
    overlaps nothing, and is unconditionally topmost.
    """
    if role == "nau":
        return nau_displays(main_mode)
    if role == "genau":
        return genau_active(main_mode)
    if role in ORIGENERATOR_ROLES:
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


def visible_main_slot_roles(main_mode: str) -> tuple[str, ...]:
    """Which of the two main-slot players *main_mode* has on the screen: Nau in
    nau, Genau in genau, and both in hybrid, where Genau's HUD sits over Nau's
    video.

    Read by anything that acts on "the main player's window", because the pair
    shares one rect and the idle one is parked — minimizing a window the mode has
    already put away is what drags it back into view.  Derived from the band
    policy above rather than listed again: a main-slot player is in the topmost
    band exactly when it is showing something, so the two answers cannot drift.
    """
    return tuple(role for role in PRIMARY_SLOT_ROLES if role_topmost(role, main_mode))

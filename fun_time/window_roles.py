"""Topmost band policy for the managed Fun Time windows.

Startup, omnipause and mode switches all read this ONE policy, so they can
never disagree about a window's topmost band — the drift that once left Nau
stranded on top after entering omnipause.

The satellite / dashboard / RFB windows each own a screen rect and
never overlap, so they are unconditionally topmost.  The primary-slot players Nau and
Genau are the exception: they SHARE one rect (in hybrid Genau's transparent HUD
overlays Nau's video), so they both need to float above the desktop AND be
stacked relative to each other:

  * nau mode    — Nau owns the display and is topmost (Genau hidden).
  * hybrid mode — Nau is topmost so the video floats up, and Genau is stacked
                  just ABOVE it so the HUD overlays the video.  That ordering is
                  enforced by promoting Nau before Genau (see the dispatch
                  loop's ``_restack_primary_slot``), not by these flags.
  * genau mode  — Genau owns the display and is topmost (Nau hidden).
"""
from __future__ import annotations

from .mode_plan import genau_active, nau_displays

# Windows with their own screen rect — always topmost; order among them is
# irrelevant because they never overlap.  The log stream is a child widget of the
# dashboard window, not a role of its own, so it rides the dashboard's band.
FIXED_TOPMOST_ROLES: tuple[str, ...] = ("rfb", "portrait", "landscape", "dashboard")

# The two players that share the primary-display rect and therefore need
# explicit stacking (Nau under Genau's HUD in hybrid).
PRIMARY_SLOT_ROLES: tuple[str, ...] = ("nau", "genau")

# Every window role the bridge manages.
MANAGED_ROLES: tuple[str, ...] = FIXED_TOPMOST_ROLES + PRIMARY_SLOT_ROLES


def role_topmost(role: str, primary_mode: str) -> bool:
    """Whether *role*'s window belongs in the TOPMOST band in *primary_mode*.

    Both primary-slot players are mode-dependent, because they share a rect:
    each is topmost only in the modes where it shows something, and the hidden
    slot-mate stays out of the band entirely.  Genau is promoted last, so being
    in the band at all puts it ABOVE Nau — which is what hybrid wants and what
    nau mode must not have.  Every other managed window owns its own rect,
    overlaps nothing, and is unconditionally topmost.
    """
    if role == "nau":
        return nau_displays(primary_mode)
    if role == "genau":
        return genau_active(primary_mode)
    return True

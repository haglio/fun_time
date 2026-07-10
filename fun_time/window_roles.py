"""Topmost band policy for the managed Fun Time windows.

Startup, omnipause and mode switches all read this ONE policy, so they can
never disagree about a window's topmost band — the drift that once left Nau
stranded on top after entering omnipause.

The satellite / dashboard / log-panel / RFB windows each own a screen rect and
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

# Windows with their own screen rect — always topmost; order among them is
# irrelevant because they never overlap.
FIXED_TOPMOST_ROLES: tuple[str, ...] = ("rfb", "portrait", "landscape", "dashboard", "logs")

# The two players that share the primary-display rect and therefore need
# explicit stacking (Nau under Genau's HUD in hybrid).
PRIMARY_SLOT_ROLES: tuple[str, ...] = ("nau", "genau")

# Every window role the bridge manages.
MANAGED_ROLES: tuple[str, ...] = FIXED_TOPMOST_ROLES + PRIMARY_SLOT_ROLES

# The log panel is a Qt window owned by the dashboard process, so a pid lookup
# cannot tell the two apart; its exact title is what resolves it.  It lives here
# rather than in fun_time.log_panel so the dispatch loop can look the window up
# without importing PyQt6 into the orchestrator process.
LOG_PANEL_WINDOW_TITLE = "Fun Time Logs"


def role_topmost(role: str, primary_mode: str) -> bool:
    """Whether *role*'s window belongs in the TOPMOST band in *primary_mode*.

    Nau is the only mode-dependent role: topmost whenever it owns the display
    (nau and hybrid) so its video floats above the desktop, and non-topmost in
    genau mode where it is hidden.  In hybrid Genau's HUD must sit ABOVE Nau —
    that stacking is handled by promotion order, not this flag.  Every other
    managed window is unconditionally topmost.
    """
    if role == "nau":
        return primary_mode in ("nau", "hybrid")
    return True

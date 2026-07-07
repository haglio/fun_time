"""Topmost band policy for the managed Fun Time windows.

Startup, omnipause and mode switches all read this ONE policy, so they can
never disagree about a window's topmost band — the drift that once left Nau
stranded on top after entering omnipause.

Every managed window is always-on-top EXCEPT the primary player Nau, whose band
is mode-dependent:

  * nau mode    — Nau owns the whole display and floats topmost, above the
                  desktop, exactly like the primary player always has.
  * hybrid mode — Nau still shows the video but must ride UNDER Genau's
                  transparent HUD, so it stays non-topmost.
  * genau mode  — Nau is hidden; its band is irrelevant, kept non-topmost.

Windows never overlap within the topmost band (each satellite / dashboard / RFB
has its own screen rect), so there is no intra-band z-order to manage: the flag
alone decides whether a window floats above the desktop.
"""
from __future__ import annotations

# Every window role the bridge manages, in a stable order.
MANAGED_ROLES: tuple[str, ...] = (
    "rfb", "portrait", "landscape", "genau", "nau", "dashboard",
)


def role_topmost(role: str, primary_mode: str) -> bool:
    """Whether *role*'s window belongs in the TOPMOST band in *primary_mode*.

    Nau is the only mode-dependent role: topmost only in pure nau mode, where it
    owns the display and nothing paints over it.  Every other managed window is
    unconditionally topmost.
    """
    if role == "nau":
        return primary_mode == "nau"
    return True

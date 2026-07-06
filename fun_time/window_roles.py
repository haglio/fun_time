"""Static topmost policy for the managed Fun Time windows.

Both the startup sequencer (which applies these flags once, as each window
appears) and the dispatch loop (which clears and re-applies them around
omnipause) read this single mapping. Keeping ONE copy is what stops startup
and omnipause from disagreeing about a window's topmost band — the drift that
once left Nau stranded on top after entering omnipause.

Nau is deliberately NOT topmost: in hybrid mode it sits beneath Genau's
transparent HUD and must never rise above it. Windows never overlap anymore
(each has its own screen rect), so there is no z-order to manage — a plain
non-topmost flag is all that keeps Nau in place.
"""
from __future__ import annotations

ROLE_TOPMOST: dict[str, bool] = {
    "rfb": True,
    "portrait": True,
    "landscape": True,
    "genau": True,
    "nau": False,
    "dashboard": True,
}

"""Topmost band policy for the managed Fun Time windows.

Startup, omnipause and mode switches all read this ONE policy, so they can
never disagree about a window's topmost band, which is the drift that leaves
the Main Funestra stranded on top after entering omnipause.

The satellite, dashboard and Main Funestra windows each own a screen rect and
never overlap, so they are unconditionally topmost.  One pair SHARES a rect and
stacks against each other: the Random Favs Browser under the hosted
Origenerator's window (``restack_rfb_slot``).
"""
from __future__ import annotations

from shared_ui.preview import Preview, window_title

from .origenerator_switches import RfbSlot

# Windows with their own screen rect — always topmost; order among them is
# irrelevant because they never overlap.  The log stream is a child widget of the
# dashboard window, not a role of its own, so it rides the dashboard's band.
# Kino and Genau both run on the Main Funestra's one window.
FIXED_TOPMOST_ROLES: tuple[str, ...] = ("portrait", "landscape", "dashboard", "main_player")

# The hosted Origenerator's one window.  Its slideshows play on the Satellite
# Funestras, not in windows of its own (fun_time.funestra_handover).
ORIGENERATOR_ROLE = "origenerator"

# The browser and the hosted app's window share the RFB's rect.  While the
# hosted window holds it the browser leaves the band, so no promotion, however
# late it lands, can stack the browser back over it.
RFB_SLOT_ROLES: tuple[str, ...] = ("rfb", ORIGENERATOR_ROLE)

# The caption that window wears, resolved together with the app's PID: by
# title alone a standalone Origenerator of his would match.
ORIGENERATOR_TITLE = "Origenerator"
_A_PREVIEWS_CAPTION = window_title(ORIGENERATOR_TITLE, Preview(feature=None))


def is_origenerators_caption(caption: str) -> bool:
    return caption == ORIGENERATOR_TITLE or caption.startswith(_A_PREVIEWS_CAPTION)

# Every window role the bridge manages, in promotion order.
MANAGED_ROLES: tuple[str, ...] = FIXED_TOPMOST_ROLES + RFB_SLOT_ROLES


def role_topmost(role: str, rfb_slot: RfbSlot = RfbSlot.RFB) -> bool:
    """Whether *role*'s window belongs in the TOPMOST band: every window with a
    rect of its own, and of the pair sharing the browser's rect only the one
    holding it -- promoting the covered one only puts it briefly ABOVE its
    cover, since HWND_TOPMOST inserts at the top of the band."""
    if role not in RFB_SLOT_ROLES:
        return True
    return (role == ORIGENERATOR_ROLE) == (rfb_slot is RfbSlot.ORIGENERATOR)


def visible_roles(rfb_slot: RfbSlot = RfbSlot.RFB) -> list[str]:
    """Every managed role whose window is kept on screen: the browser counts
    while the hosted window covers it, since it is covered, not parked."""
    held = RFB_SLOT_ROLES if rfb_slot is RfbSlot.ORIGENERATOR else ("rfb",)
    return [*FIXED_TOPMOST_ROLES, *held]

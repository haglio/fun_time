"""Whether the main player's window paints, and the black it shows when it does not.

Fun Time's main slot is one rect that the main player and Genau share, and the player
that does not own it is *minimized*, not closed — it keeps its taskbar button
for the whole session.  So an alt-tab or a click on that button restores a
paused player still holding the frame it stopped on.  Genau has been told
DISPLAY_ON/DISPLAY_OFF as it enters and leaves the modes that show it for
exactly this reason; this is the main player's half, and it arrives on the same verbs.

DISPLAY_OFF is not PAUSE.  A paused main player still has a video up (video mode pauses
nothing and OmniPause freezes it while it is very much on screen), so blanking
can only key off being told it is off screen.

mpv owns this window's pixels — the video renders straight into it, over the
pygame surface under it — so the black is an opaque overlay composited on top of
the video, the same channel the HUD already rides.  The HUD comes down with the
video: a blanked player shows nothing at all, which is both what "off" means
and why nothing here depends on the order mpv composites overlays in.
"""
from __future__ import annotations

import numpy as np

# Above every id main_player.painter draws with, and the only overlay up while it is.
_OVERLAY_ID = 8


def black_bgra(width: int, height: int) -> np.ndarray:
    """An opaque black (H, W, 4) BGRA block, mpv's overlay format.

    Opaque black is the one fill premultiplied and straight alpha agree on
    (every color channel is zero either way), so it covers the video whichever
    mpv takes the buffer as.
    """
    frame = np.zeros((max(1, height), max(1, width), 4), dtype=np.uint8)
    frame[:, :, 3] = 255
    return frame


class Display:
    """The main player's screen, as Fun Time switches it on and off.

    Holds the DISPLAY_ON/DISPLAY_OFF state and, on each frame, makes the window
    match it.  Defaults on, so the window paints its video until Fun Time
    says otherwise.
    """

    def __init__(self, player, hud_ids) -> None:
        self._player = player
        self._hud_ids = tuple(hud_ids)
        self._active = True
        # The size the black is currently up at, or None while it is not up.
        # An mpv overlay stays until it is removed or replaced, so a frame that
        # changes nothing does no work at all.
        self._blanked_at: tuple[int, int] | None = None

    @property
    def active(self) -> bool:
        """Whether the main player owns the main slot's rect right now, and so paints."""
        return self._active

    def set_active(self, active: bool) -> None:
        """Take DISPLAY_ON/DISPLAY_OFF; the next :meth:`sync` acts on it."""
        self._active = active

    def sync(self, width: int, height: int) -> None:
        """Make the window match the display state, at the current window size.

        Called every frame: the size is re-checked because the black has to
        cover the whole window, and Fun Time sizes that rect.
        """
        if self._active:
            self._clear()
        else:
            self._blank(width, height)

    def _blank(self, width: int, height: int) -> None:
        if self._blanked_at == (width, height):
            return
        for ident in self._hud_ids:
            self._player.remove_overlay(ident)
        self._player.overlay(_OVERLAY_ID, 0, 0, black_bgra(width, height))
        self._blanked_at = (width, height)

    def _clear(self) -> None:
        if self._blanked_at is None:
            return
        self._player.remove_overlay(_OVERLAY_ID)
        self._blanked_at = None

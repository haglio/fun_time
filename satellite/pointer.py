"""What the mouse does to a satellite's window: chip, scrubber row, HUD, picture.

Topmost first, the order mpv composites the first three in (12, 11, 10).
"""
from __future__ import annotations

import logging
from pathlib import Path

from player_core.file_channel import append_command
from player_core.timeline import TIMELINE_HEIGHT, bar_track_x

logger = logging.getLogger(__name__)

OMNIPAUSE_TOGGLE = "omnipause_toggle"  # spelled here: this package knows no fun_time


def time_at(mx: int, *, win_w: int, duration_ms: float) -> float:
    """The media time the scrubber puts under *mx*, saturating past either end."""
    x0, x1 = bar_track_x(win_w)
    fraction = min(1.0, max(0.0, (mx - x0) / max(1, x1 - x0)))
    return fraction * duration_ms


def ask_for_omnipause(dashboard_cmd_file: Path | None) -> None:
    """Ask fun_time to freeze the room, or to let it go again."""
    if dashboard_cmd_file is None:
        return  # no session to ask: a player launched by hand, or by a test
    if not append_command(Path(dashboard_cmd_file), OMNIPAUSE_TOGGLE):
        logger.warning("Dropped the omnipause ask (command file locked)")


class Pointer:
    """The control under the pointer, and what a press or a drag on it does."""

    def __init__(self, *, session, volume, hud=None,
                 dashboard_cmd_file: Path | None = None) -> None:
        self._session = session
        self._volume = volume
        self._hud = hud
        self._dashboard_cmd_file = dashboard_cmd_file

    def press(self, mx: int, my: int, *, win_w: int, win_h: int) -> None:
        """Take a press at window ``(mx, my)``; on the picture itself it asks
        for the room to pause or resume as a whole."""
        if not self._suppressed:
            if self._volume.press_at(mx, my, win_w=win_w, win_h=win_h,
                                     timeline_h=TIMELINE_HEIGHT):
                return
            if my >= win_h - TIMELINE_HEIGHT:
                self._session.seek_to(
                    time_at(mx, win_w=win_w, duration_ms=self._session.duration_ms))
                return
        if self._hud is not None and self._hud.press(mx, my):
            return
        ask_for_omnipause(self._dashboard_cmd_file)

    def motion(self, mx: int, my: int, *, held: bool,
               win_w: int, win_h: int) -> None:
        """Follow the pointer; the HUD is told wherever it goes, hover being its
        own question, and a held one also drags the volume slider."""
        if self._hud is not None:
            self._hud.motion(mx, my)
        if held and not self._suppressed:
            self._volume.drag_at(mx, my, win_w=win_w, win_h=win_h,
                                 timeline_h=TIMELINE_HEIGHT)

    @property
    def _suppressed(self) -> bool:
        return self._hud is not None and self._hud.display_suppressed

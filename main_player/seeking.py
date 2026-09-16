"""A seek mpv can refuse, and one a player owes the file it is opening."""
from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Ticks a refused seek is asked for again before it is let go: mpv takes one
# within a few frames of the file starting to play, so this is a ceiling.
GIVE_UP_AFTER = 120


def seek_if_taken(player, position_ms: float) -> bool:
    """Seek *player* to *position_ms*, or False where mpv refuses the seek: it
    refuses one until the file it is opening plays, which a known duration does
    not prove, and python-mpv raises that refusal as a SystemError."""
    try:
        player.seek_ms(position_ms)
    except SystemError:
        return False
    return True


class OwedSeek:
    """A seek owed to the file on screen, asked for each tick until mpv takes it."""

    def __init__(self) -> None:
        self._position_ms: float | None = None
        self._asked = 0

    def owe(self, position_ms: float | None) -> None:
        self._position_ms = position_ms
        self._asked = 0

    def pay(self, player, seek: Callable[[float], bool]) -> None:
        if self._position_ms is None or player.duration_ms <= 0:
            return
        self._asked += 1
        if seek(self._position_ms):
            self._position_ms = None
        elif self._asked >= GIVE_UP_AFTER:
            logger.warning("mpv never took the seek to %.0f ms; playing on from where it is",
                           self._position_ms)
            self._position_ms = None

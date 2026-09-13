"""One-shot notices the main player raises for the Fun Time overlay.

The main player owns the library index, so only the main player can tell that "full video" or "money
shot" had nowhere to go. It has no text layer of its own, and Fun Time already
flashes notices over the main slot — so the result travels as a tiny
sequenced file: the main player bumps the sequence, Fun Time notices the change and flashes
the message once. A missed read just means a missed flash, never a stuck one.

Published whole rather than truncated and rewritten: Fun Time polls this file
several times a second while a run of notices republishes it, and a poller that
catches the empty window cannot tell it from "there is nothing here" -- which is
a notice dropped, or a blank flashed over the video.
"""
from __future__ import annotations

import time
from pathlib import Path

from player_core.file_channel import publish_whole
from player_core.modes import NoticeLevel


class NoticeWriter:
    """Publishes the main player's latest one-shot notice to *path* (key=value lines)."""

    def __init__(self, path: Path | None, *, clock=time.time) -> None:
        self._path = path
        self._clock = clock

    def say(self, message: str, *, level: NoticeLevel) -> None:
        """Raise *message*; ``level`` picks the color Fun Time flashes it in.

        A warning is yellow, a notice white, and a highlight green — green being
        what Fun Time reserves for the favorites and the funscripts, so a
        funscript jump says so in the color and an ordinary jump does not.

        The sequence is a wall-clock stamp rather than a counter, so it
        survives a restart: a counter would begin again at 1 while the reader
        still held the high number from the session before, and every notice of
        the new one would read as older than what had already been shown.
        """
        if self._path is None:
            return
        publish_whole(
            self._path,
            f"seq={self._clock():.3f}\nlevel={level}\nmessage={message}\n")


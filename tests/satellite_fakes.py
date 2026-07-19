"""Shared in-memory stand-in for the mpv-backed player a satellite drives.

Models mpv's tiny lookahead playlist: a cold ``load`` resets it to just the
current entry, ``stage_next`` appends the one prefetch entry, and
``simulate_eof_advance`` mimics mpv reaching end-of-file and auto-advancing onto
that staged entry (which is what ``advanced_to_next`` then reports).  The three
satellite test modules all drive :class:`satellite.session.SatelliteSession`
against this same fake, so its interface is the player contract the session
relies on.
"""
from __future__ import annotations

from pathlib import Path


class FakeSatellitePlayer:
    def __init__(self, duration_ms: float = 5_000.0) -> None:
        self.opened: list[Path] = []        # cold plays (load) only
        self.playlist: list[Path] = []      # mpv's window: [current, next?]
        self.playlist_pos = 0
        self.duration_ms = duration_ms
        self.position_ms = 0.0
        self.paused = False
        self.loop_file = False
        self.closed = False
        self.overlays: dict[int, tuple[int, int, object]] = {}

    # --- the interface SatelliteSession drives -------------------------------
    def load(self, path: Path) -> None:
        self.opened.append(path)
        self.playlist = [path]
        self.playlist_pos = 0
        self.position_ms = 0.0

    def stage_next(self, path: Path) -> None:
        del self.playlist[self.playlist_pos + 1:]
        self.playlist.append(path)

    def clear_next(self) -> None:
        del self.playlist[self.playlist_pos + 1:]

    @property
    def advanced_to_next(self) -> bool:
        return self.playlist_pos >= 1

    def drop_consumed(self) -> None:
        while self.playlist_pos > 0:
            self.playlist.pop(0)
            self.playlist_pos -= 1

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def set_loop_file(self, loop: bool) -> None:
        self.loop_file = loop

    def close(self) -> None:
        self.closed = True

    # --- the overlay interface the lock HUD composites through ---------------
    def overlay(self, ident: int, x: int, y: int, bgra) -> None:
        self.overlays[ident] = (x, y, bgra)

    def remove_overlay(self, ident: int) -> None:
        self.overlays.pop(ident, None)

    # --- test conveniences ---------------------------------------------------
    def simulate_eof_advance(self) -> None:
        """Pretend the current clip ended and mpv rolled onto the staged next."""
        if len(self.playlist) > self.playlist_pos + 1:
            self.playlist_pos += 1

    @property
    def staged_next(self) -> Path | None:
        """The clip mpv would cut to at EOF (the prefetched entry), if any."""
        tail = self.playlist[self.playlist_pos + 1:]
        return tail[0] if tail else None

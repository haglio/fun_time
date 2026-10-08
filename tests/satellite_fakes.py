"""Shared in-memory stand-in for the mpv-backed player a headset satellite drives.

Models mpv's tiny lookahead playlist: a cold ``load`` resets it to just the
current entry, ``stage_next`` appends the one prefetch entry, and
``simulate_eof_advance`` mimics mpv reaching end-of-file and auto-advancing onto
that staged entry (which is what ``advanced_to_next`` then reports).
"""
from __future__ import annotations

from pathlib import Path

from tests.mpv_refusals import RefusesSeeks


class FakeSatellitePlayer(RefusesSeeks):
    def __init__(self, duration_ms: float = 5_000.0) -> None:
        self.opened: list[Path] = []        # cold plays (load) only
        self.playlist: list[Path] = []      # mpv's window: [current, next?]
        self.playlist_pos = 0
        self.duration_ms = duration_ms
        self.position_ms = 0.0
        self.frame_rate = 25.0
        self.paused = False
        self.loop_file = False
        self.closed = False
        self.overlays: dict[int, tuple[int, int, object]] = {}
        # mpv's two independent audio properties: a satellite opens muted, and
        # the level under that mute is what unmuting comes back to.
        self.volume = 100
        self.muted = True
        self.seeks: list[float] = []
        self.speed = 1.0
        self.pace_s: float | None = None
        self.showing_picture = False
        # How many times the loop has asked for a still's move to be
        # carried on — the player's own account of where it has got to lives
        # in player_core, and this only counts the asking.
        self.pushes = 0
        self.tiled_to: list[tuple[int, int]] = []
        self.swapped: list[Path] = []

    def tile_to_fill(self, window_width: int, window_height: int) -> None:
        self.tiled_to.append((window_width, window_height))

    # --- the interface the playback drives --------------------------------
    def load(self, path: Path) -> None:
        self.opened.append(path)
        self.playlist = [path]
        self.playlist_pos = 0
        self.position_ms = 0.0

    def swap_still(self, path: Path) -> None:
        self.swapped.append(path)
        self.playlist[self.playlist_pos] = path

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

    def set_pace(self, seconds: float) -> None:
        self.pace_s = seconds

    def push_still(self) -> None:
        self.pushes += 1

    def seek_ms(self, ms: float) -> None:
        self.refuse_if_asked()
        self.seeks.append(ms)
        self.position_ms = max(0.0, min(self.duration_ms, ms))

    def set_volume(self, volume: int) -> None:
        self.volume = volume

    def set_muted(self, muted: bool) -> None:
        self.muted = muted

    def set_speed(self, speed: float) -> None:
        self.speed = speed

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

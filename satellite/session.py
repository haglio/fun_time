"""Playlist/navigation orchestration for a satellite player, decoupled from the
window.

A satellite is the simple half of Nau: an unscripted, silent looper of short
clips.  It owns its playlist position and drives an mpv-backed *player*
(:class:`player_core.mpv_player.MpvPlayer`) to load/pause/lock — but there is no
funscript, no OSR2/T-Code, no loop recording and no audio, so this session is a
fraction of Nau's own PlayerSession.  Navigation is fully in-process
(a Python list + index), which is the whole point of leaving VLC behind: no HTTP
playlist to resolve ids against, and pausing is a flag the player obeys.

Auto-advance is the one thing mpv drives itself: the session hands mpv the *next*
clip as a staged playlist entry (``stage_next``), and with prefetch on mpv opens
and decodes it before the current clip ends, then rolls onto it at end-of-file
seamlessly.  Each tick :meth:`advance` notices that roll, re-syncs the index, and
stages the clip after it — so a let-it-play satellite never cold-loads a clip on
screen.  Explicit navigation (next/prev/discard/jump) still cold-loads, which is
fine: those are deliberate gestures, not the every-few-seconds cadence.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SatelliteSession:
    def __init__(
        self,
        playlist: list[Path],
        *,
        player,
        start_paused: bool = False,
    ) -> None:
        if not playlist:
            raise ValueError("playlist must not be empty")
        self._playlist = list(playlist)
        self._player = player
        self._paused = start_paused
        self._locked = False
        self._index = 0
        self.load(0)

    @property
    def index(self) -> int:
        return self._index

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_locked(self) -> bool:
        return self._locked

    @property
    def current_video(self) -> Path:
        return self._playlist[self._index]

    @property
    def playlist(self) -> list[Path]:
        """A copy of the current playlist, so callers cannot mutate it in place."""
        return list(self._playlist)

    @property
    def position_ms(self) -> float:
        return self._player.position_ms

    @property
    def duration_ms(self) -> float:
        return self._player.duration_ms

    def step(self, delta: int) -> None:
        """Navigate *delta* items (next = +1, prev = -1), wrapping the playlist."""
        self.load(self._index + delta)

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        self._player.set_paused(paused)

    def toggle_pause(self) -> None:
        self.set_paused(not self._paused)

    def set_locked(self, locked: bool) -> None:
        """Lock the satellite onto its current clip (repeat-one) or release it.

        Locking hands the repeat to mpv's own ``loop_file`` so a short clip loops
        seamlessly in place, and drops the staged next so :meth:`advance` can
        never walk off it.  Unlocking restores playlist auto-advance and
        re-stages the upcoming clip for prefetch.
        """
        self._locked = locked
        self._player.set_loop_file(locked)
        if locked:
            self._player.clear_next()
        else:
            self._stage_next()

    def advance(self) -> None:
        """Per-tick update: keep the prefetch window rolling as mpv auto-advances.

        mpv opens the staged next clip ahead of time and cuts to it itself at
        end-of-file, so there is nothing to load here — the session just notices
        the roll, moves its index onto the clip now playing, discards the spent
        head, and stages the following clip.  A paused satellite never advances,
        which is what makes OmniPause a settled state: freeze the flag and the
        playlist cannot walk on its own.  A locked satellite holds its clip too
        (repeat-one), with no staged next to roll onto.
        """
        if self._paused or self._locked:
            return
        if self._player.advanced_to_next:
            self._index = (self._index + 1) % len(self._playlist)
            self._player.drop_consumed()
            self._stage_next()

    def discard(self) -> None:
        """Drop the current clip from the playlist and play the next one — the
        satellite's "trash" gesture.

        The next clip shifts into the current index, so re-loading that index
        lands on it; discarding the last entry wraps to the first.  A satellite
        must always have something to play, so the final remaining clip cannot be
        discarded — that is a no-op, never an empty playlist.
        """
        if len(self._playlist) <= 1:
            return
        del self._playlist[self._index]
        self.load(self._index)

    def play_file(self, video: Path) -> None:
        """Jump to *video* if it is already in the playlist, else splice it in
        after the current clip and play it.

        Powers "play this exact clip": a lock's back-dating (bring back the clip
        the speaker actually saw) and a HUD switch both target a member, so those
        just jump; a newcomer from outside the list is inserted next and played.
        """
        for i, path in enumerate(self._playlist):
            if path == video:
                self.load(i)
                return
        self._playlist.insert(self._index + 1, video)
        self.load(self._index + 1)

    def load_playlist(self, playlist: list[Path]) -> None:
        """Swap in a whole new playlist and restart at the top.

        A fresh browse — a filter or a group loop — that should begin from the
        start of the new set.
        """
        if not playlist:
            raise ValueError("playlist must not be empty")
        self._playlist = list(playlist)
        self.load(0)

    def replace_playlist(self, playlist: list[Path]) -> None:
        """Swap in a rebuilt playlist but keep playing the current clip if it
        survives, else restart at the top.

        A reload where continuity matters — an F-mode toggle rebuilds the list,
        and the clip on screen should keep playing uninterrupted when it is still
        present rather than flicker back to a reload.  The prefetched next is
        re-staged from the new list without disturbing the playing clip.
        """
        if not playlist:
            raise ValueError("playlist must not be empty")
        current = self.current_video
        self._playlist = list(playlist)
        for i, path in enumerate(self._playlist):
            if path == current:
                self._index = i
                self._stage_next()
                return
        self.load(0)

    def load(self, index: int) -> None:
        self._index = index % len(self._playlist)
        video = self._playlist[self._index]
        logger.info("Loading: %s", video.name)
        self._player.load(video)
        self._player.set_paused(self._paused)
        self._stage_next()

    def _stage_next(self) -> None:
        """Hand mpv the upcoming clip so prefetch can open it before it is needed.

        Skipped while locked: a locked satellite repeats its clip in place and
        must never roll onto a neighbour.
        """
        if self._locked:
            return
        nxt = self._playlist[(self._index + 1) % len(self._playlist)]
        self._player.stage_next(nxt)

    def close(self) -> None:
        """Tear down the underlying player (mpv terminate) on shutdown."""
        self._player.close()

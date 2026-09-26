"""Playlist/navigation orchestration for a satellite player, decoupled from the
window.

A satellite is the simple half of the main player: an unscripted looper of short clips, muted
until its own chip is asked.  It owns its playlist position and drives an
mpv-backed *player* (:class:`player_core.mpv_player.MpvPlayer`) to
load/pause/lock/seek — but with no funscript, no OSR2/T-Code and no loop
recording, it is a fraction of the main player's own PlayerSession.  Navigation is fully
in-process (a Python list + index), which is the whole point of dropping VLC:
no HTTP playlist to resolve ids against, and pausing is a flag.

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

from player_core.playback_rate import clamp_rate

from main_player.play_points import PlayPoints
from main_player.seeking import OwedSeek, seek_if_taken

logger = logging.getLogger(__name__)


class SatelliteSession:
    def __init__(
        self,
        playlist: list[Path],
        *,
        player,
        start_paused: bool = False,
        play_points: PlayPoints | None = None,
    ) -> None:
        if not playlist:
            raise ValueError("playlist must not be empty")
        self._playlist = list(playlist)
        self._player = player
        self._paused = start_paused
        self._locked = False
        self._speed = 1.0
        self._index = 0
        self._play_points = play_points or PlayPoints(None)
        self._resume = OwedSeek()
        self._versions: dict[Path, Path] = {}
        self._switching_versions = False
        self.frame: Path | None = None
        self.load(0)

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
    def showing(self) -> Path:
        """The file on screen: the clip's own, unless a version was stepped to."""
        return self._versions.get(self.current_video, self.current_video)

    def step_version(self, versions: list[Path], delta: int) -> None:
        showing = self.showing
        if len(versions) < 2 or showing not in versions:
            return
        target = versions[(versions.index(showing) + delta) % len(versions)]
        clip = self.current_video
        if target == clip:
            self._versions.pop(clip, None)
        else:
            self._versions[clip] = target
        self.load(self._index)
        self._switching_versions = True

    @property
    def name_on_screen(self) -> str:
        name = self.current_video.stem
        if not self._switching_versions and self.showing == self.current_video:
            return name
        return f"{name} ({self.showing.name})"

    @property
    def playlist(self) -> list[Path]:
        """A copy of the list — a test seam, and nothing in the app reads it."""
        return list(self._playlist)

    @property
    def playlist_length(self) -> int:
        return len(self._playlist)

    @property
    def position_ms(self) -> float:
        return self._player.position_ms

    @property
    def duration_ms(self) -> float:
        return self._player.duration_ms

    @property
    def showing_picture(self) -> bool:
        return self._player.showing_picture

    def step(self, delta: int) -> None:
        """Navigate *delta* items (next = +1, prev = -1), wrapping the playlist."""
        self.load(self._index + delta)

    def seek_to(self, ms: float) -> bool:
        """Jump to *ms* in the clip on screen, or False where mpv refuses it."""
        return seek_if_taken(self._player, ms)

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        self._player.set_paused(paused)

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> None:
        self._speed = clamp_rate(speed)
        self._player.set_speed(self._speed)

    def set_pace(self, seconds: float) -> None:
        self._player.set_pace(seconds)

    def show_frame(self, frame: Path) -> None:
        self.frame = frame

    def clear_frame(self) -> None:
        self.frame = None

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
        which is what makes OmniPause a settled state; a locked one holds its
        clip too (repeat-one), with no staged next to roll onto.
        """
        self._resume.pay(self._player, self.seek_to)
        self._play_points.observe(
            self.current_video, self._player.position_ms, self._player.duration_ms)
        if self._paused or self._locked:
            return
        if self._player.advanced_to_next:
            self._play_points.ended()
            self.frame = None
            self._index = (self._index + 1) % len(self._playlist)
            self._player.drop_consumed()
            self._stage_next()
            self._resume.owe(self._play_points.point_for(self.current_video) or None)

    def discard(self) -> None:
        """Drop the clip on screen from the list and play the next — "trash"."""
        if len(self._playlist) <= 1:
            return
        self._versions.pop(self._playlist.pop(self._index), None)
        self.load(self._index)

    def play_file(self, video: Path) -> None:
        """Jump to *video* if it is already in the playlist, else splice it in
        after the current clip and play it.

        Powers "play this exact clip": a lock's back-dating (bring back the clip
        the speaker actually saw) and a HUD switch both target an item, so those
        just jump; a newcomer from outside the list is inserted next and played.
        """
        for i, path in enumerate(self._playlist):
            if path == video:
                self.load(i)
                return
        self._playlist.insert(self._index + 1, video)
        self.load(self._index + 1)

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
        chosen = self._versions.get(current)
        self._playlist = list(playlist)
        self._versions = {} if chosen is None else {current: chosen}
        for i, path in enumerate(self._playlist):
            if path == current:
                self._index = i
                self._stage_next()
                return
        self._versions = {}
        self.load(0)

    def load(self, index: int) -> None:
        self._play_points.leave()
        self._switching_versions = False
        self.frame = None
        self._index = index % len(self._playlist)
        clip = self._playlist[self._index]
        video = self._versions.get(clip, clip)
        logger.info("Loading: %s", video.name)
        self._player.load(video)
        self._player.set_paused(self._paused)
        self._stage_next()
        self._resume.owe(self._play_points.point_for(clip) or None)

    def _stage_next(self) -> None:
        """Hand mpv the upcoming clip so prefetch can open it before it is needed.

        Skipped while locked: a locked satellite repeats its clip in place and
        must never roll onto a neighbour.
        """
        if self._locked:
            return
        nxt = self._playlist[(self._index + 1) % len(self._playlist)]
        self._player.stage_next(self._versions.get(nxt, nxt))

    def close(self) -> None:
        """Tear down the underlying player, whatever thread is still driving it."""
        self._play_points.leave()
        self._player.close()

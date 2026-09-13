"""Playback/loop orchestration for the main player, decoupled from the window.

Owns the playlist position, loop recording, seek/step actions, and OSR2
output gating — everything the UI shell and the Fun Time command channel
both drive.  The actual video/audio/timeline is an mpv-backed *player*
(:class:`player_core.mpv_player.MpvPlayer`): mpv hardware-decodes, keeps A/V in sync,
seeks precisely, and loops an A/B range natively, so the session just tells it
what to do and reads its clock back.  Videos without a funscript play
normally: the OSR2 rests at its parked position with no script to follow, and
loop recording falls back to raw clip ranges without funscript snapping.
"""
from __future__ import annotations

import logging
from pathlib import Path

from player_core.funscript import load as load_funscript
from player_core.modes import LoopState

from .session_loops import SessionLoops

logger = logging.getLogger(__name__)

# A backward jump larger than this (ms) means the playback clock rewound rather
# than merely ticking forward.  A rewind that also lands within
# _EOF_WRAP_START_MS of zero is the file wrapping at EOF (mpv loop-file=inf
# restarts at 0), as opposed to a user seeking backward to some interior point.
_REWIND_MS = 50
_EOF_WRAP_START_MS = 250

# While marking a loop, close it once the playhead comes within this of the
# file end — proactively, so mpv's A/B loop takes over before loop-file wraps
# the whole video to the start and flashes the opening frames.  Wide enough that
# a tick reliably lands inside it at 60 fps, small enough to still feel instant.
_EOF_MARGIN_MS = 100

# Playback-rate bounds for the speed control (mpv's ``speed`` multiplier, where
# 1.0 is normal). The funscript follows a speed change automatically because it
# is driven off mpv's clock, which advances at the playback rate.
MIN_SPEED_RATE = 0.25
MAX_SPEED_RATE = 2.0

# Volume bounds for the audio control, on mpv's ``volume`` scale: a percentage
# of the source's own level, where 100 is untouched and 0 is silent.
MIN_VOLUME = 0
MAX_VOLUME = 100


class PlayerSession:
    def __init__(
        self,
        playlist: list[tuple[Path, Path | None]],
        *,
        player,
        tcode,
        start_paused: bool = False,
        version_index: dict[Path, list[tuple[Path, Path | None]]] | None = None,
    ) -> None:
        if not playlist:
            raise ValueError("playlist must not be empty")
        self._playlist = list(playlist)
        self._player = player
        self._tcode = tcode
        self._version_index = version_index or {}
        self._paused = start_paused
        # Locked is how the main player has always played: mpv repeats the one file
        # (``loop_file=inf``, the option the player is constructed with) and `[`/`]`
        # are the only things that move it.  Unlocking hands the end of the file
        # back to the playlist — see :meth:`set_locked`.
        self._locked = True
        self._tcode_enabled = True
        self._speed = 1.0
        self._volume = MAX_VOLUME
        self._index = 0
        self._funscript = None
        self._loops = SessionLoops(
            player,
            seek_to=self.seek_to,
            take_the_device_over=self._take_the_device_over,
        )
        self._last_pos_ms = 0.0
        self._pending_seek_ms: float | None = None
        self._stepped_at_eof = False
        self.load(0)

    @property
    def index(self) -> int:
        return self._index

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def locked(self) -> bool:
        """Whether the video on screen repeats rather than ending.

        On is the main player's original behavior and so the default: one video plays
        until you ask for another.  Published in the status file, because the
        console that draws the lock is drawn by whoever holds the main slot —
        which in genau mode is not this player.
        """
        return self._locked

    def set_locked(self, locked: bool) -> None:
        """Hold the current video (repeat-one) or hand its end back to the playlist.

        Locked is mpv's own ``loop_file``, so a video repeats seamlessly in place
        the way a locked satellite's clip does.  Unlocked, the file reaches its end
        and :meth:`advance` steps to the next entry, wrapping at the end — the
        playlist plays around rather than stopping.
        """
        self._locked = locked
        self._player.set_loop_file(locked)

    def toggle_lock(self) -> None:
        self.set_locked(not self._locked)

    @property
    def has_funscript(self) -> bool:
        return self._funscript is not None

    @property
    def current_funscript(self):
        """The loaded Funscript, or None for unscripted videos."""
        return self._funscript

    @property
    def funscript_resting(self) -> bool:
        """Whether the current spot sits in the funscript's quiet lead-in or an
        interior gap (a buffer past the nearest dense action), where the script
        has nothing to say.  Video mode hands these stretches to the Robot Hand.  False when
        there is no funscript — there is then nothing to rest between.
        """
        if self._funscript is None:
            return False
        return self._funscript.is_resting_at(int(self.position_ms))

    @property
    def current_video(self) -> Path:
        return self._playlist[self._index][0]

    @property
    def position_ms(self) -> float:
        return self._player.position_ms

    @property
    def duration_ms(self) -> float:
        return self._player.duration_ms

    @property
    def loop_state(self) -> LoopState:
        return self._loops.state

    @property
    def loop_bounds(self) -> tuple[int, int] | None:
        """Active loop (in_ms, out_ms) — None unless a loop is running."""
        return self._loops.bounds

    @property
    def record_in_ms(self) -> int | None:
        """In point of the loop being marked — None unless recording."""
        return self._loops.marked_in_ms

    def record_down(self) -> None:
        self._loops.record_down(int(self._player.position_ms))

    def record_up(self) -> None:
        self._loops.record_up(int(self._player.position_ms))

    def restore_loop(self, in_ms: int, out_ms: int) -> None:
        self._loops.restore(in_ms, out_ms)

    def loop_cancel(self) -> None:
        self._loops.cancel()

    def _take_the_device_over(self) -> None:
        """The playback clock jumped, or the device has changed hands: the next
        waypoint must glide from wherever the device really is.

        Every path that moves the playhead without playing to it says this --
        a seek, a loop wrap, a video opening, a resumed pause, a rate change,
        output being re-enabled after Genau had it.  Reset, the driver re-times
        its in-flight move against the new clock and sends the next waypoint at
        once, with the handoff glide.  Not reset, it aims from where the script
        says the device WAS: a clip scripted to its edges slams across the full
        range every pass, and a video resumed under OmniPause -- where the
        broker may have parked or retracted the device outright -- aims from a
        height nothing is at.
        """
        self._tcode.reset()

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        self._player.set_paused(paused)
        if not paused:
            # While the video sat paused the last in-flight waypoint completed:
            # the device walked on to wherever it was aimed and froze there.
            self._take_the_device_over()

    def toggle_pause(self) -> None:
        self.set_paused(not self._paused)

    @property
    def speed(self) -> float:
        """Playback rate multiplier (1.0 = normal)."""
        return self._speed

    def set_speed(self, speed: float) -> None:
        """Change the playback rate, clamped to the supported range.

        mpv retimes the video and its clock, so the funscript stays in sync on
        its own.  The in-flight T-Code move is the one thing that does not, and
        waiting out a now-mistimed one is what taking the device over avoids.
        """
        speed = max(MIN_SPEED_RATE, min(MAX_SPEED_RATE, speed))
        if speed == self._speed:
            return
        self._speed = speed
        self._player.set_speed(speed)
        self._take_the_device_over()

    def adjust_speed(self, delta: float) -> None:
        self.set_speed(self._speed + delta)

    @property
    def volume(self) -> int:
        """Playback volume: a percentage of the source's own level."""
        return self._volume

    def set_volume(self, volume: int) -> None:
        self._volume = max(MIN_VOLUME, min(MAX_VOLUME, volume))
        self._player.set_volume(self._volume)

    def set_tcode_enabled(self, enabled: bool) -> None:
        """Gate funscript T-Code output (the SET_TCODE_ENABLED command).

        In video mode the Robot Hand drives the OSR2 through the gaps, so the main player
        must stop emitting its own funscript-derived T-Code or the two fight over
        the broker's UDP inlet.  Muting just skips the per-tick update;
        re-enabling is a takeover, since the device is wherever the hand left it.
        """
        if enabled and not self._tcode_enabled:
            self._take_the_device_over()
        self._tcode_enabled = enabled

    @property
    def playlist(self) -> list[tuple[Path, Path | None]]:
        return list(self._playlist)

    def step(self, delta: int) -> None:
        self.load(self._index + delta)

    def play_file(self, video_path: Path, funscript_path: Path | None) -> None:
        """Jump to *video_path*, inserting it after the current entry if new."""
        for i, (vid, _fs) in enumerate(self._playlist):
            if vid == video_path:
                self.load(i)
                return
        self._playlist.insert(self._index + 1, (video_path, funscript_path))
        self.load(self._index + 1)

    @property
    def has_other_versions(self) -> bool:
        """Whether :meth:`cycle_version` would have anything to swap in.

        Asked the same question the swap asks, so the console's button is dim
        exactly when pressing it would do nothing.
        """
        return self._other_versions() is not None

    def _other_versions(self) -> list | None:
        """The current video's alternates, or None when there is nothing to
        cycle: no index, a singleton family, or a family the current video is
        mapped to without being in (Fun Time writes the playlist from its own
        selection, so that happens)."""
        versions = self._version_index.get(self.current_video)
        if versions is None or len(versions) <= 1:
            return None
        if self.current_video not in [video for video, _fs in versions]:
            return None
        return versions

    def cycle_version(self) -> None:
        """Swap the current entry for its next same-content version, cyclically.

        Uses the version index (entries ordered largest-first) to find the
        current video's alternates; a no-op for singletons or when no index was
        supplied.  The swap happens *in place*, so the playlist keeps one entry
        per distinct video — prev/next still navigate the deduped set rather than
        the version we cycled away from.  The new file starts from the
        beginning; nothing of the old one is preserved.
        """
        versions = self._other_versions()
        if versions is None:
            return
        videos = [vid for vid, _fs in versions]
        self._playlist[self._index] = versions[
            (videos.index(self.current_video) + 1) % len(versions)]
        self.load(self._index)

    def load_playlist(self, playlist: list[tuple[Path, Path | None]]) -> None:
        """Swap in a new playlist AND jump to its first video.

        Used by the length-mode toggle, where the point is to visibly land on
        the new mode's content (shorts vs full-length) rather than keep the
        current video playing invisibly.
        """
        if not playlist:
            return
        self._playlist = list(playlist)
        self.load(0)

    def replace_playlist(self, playlist: list[tuple[Path, Path | None]]) -> None:
        """Swap in a new playlist, keeping the current video only if it survives.

        If the current video is still in the new list, playback continues on it
        uninterrupted (the index just follows it).  Otherwise it was filtered
        out — e.g. an unscripted video when F-mode reloads the funscript-only
        list — so jump straight to the new list's first entry rather than
        stranding it on screen, mirroring how the satellites restart at item 0.
        """
        if not playlist:
            return
        current_entry = self._playlist[self._index]
        self._playlist = list(playlist)
        for i, (vid, _fs) in enumerate(self._playlist):
            if vid == current_entry[0]:
                self._index = i
                return
        # Current video was filtered out — jump to the new list's first entry.
        self.load(0)

    def seek_by(self, delta_ms: float) -> None:
        self.seek_to(self._player.position_ms + delta_ms)

    def seek_to(self, position_ms: float) -> None:
        """Seek to an absolute position (click-to-seek / nudge).

        While marking a loop, the record-down point is a floor: a backward seek
        can't rewind before where the loop started — it lands on the start.

        A seek issued in the same breath as a ``load`` is held rather than
        clamped: mpv opens a file asynchronously and reports no duration for a
        tick or two, and the ceiling below would read that as "this video is
        zero long" and put the playhead back at the top.  :meth:`advance`
        applies the held seek on the first tick the duration is known.
        """
        if self._player.duration_ms <= 0:
            self._pending_seek_ms = position_ms
            return
        floor = 0.0 if self.record_in_ms is None else float(self.record_in_ms)
        target = max(floor, min(self._player.duration_ms, position_ms))
        self._player.seek_ms(target)
        self._take_the_device_over()

    def _flush_pending_seek(self) -> None:
        """Take a seek held over a file open, once the file is open."""
        if self._pending_seek_ms is None or self._player.duration_ms <= 0:
            return
        target, self._pending_seek_ms = self._pending_seek_ms, None
        self.seek_to(target)

    def advance(self) -> None:
        """Per-tick update: what the loop makes of the clock, then the device,
        then the end of the file.

        mpv renders the video itself, so nothing is returned — the caller reads
        the session's position/state for the overlays.
        """
        # Ahead of the pause check: a seek waiting on a file to open is owed
        # whether or not the room is running, and a paused main player that never landed
        # it would show the wrong frame for as long as the pause lasts.
        self._flush_pending_seek()
        if self._paused:
            return

        pos_ms = self._player.position_ms
        rewound = pos_ms + _REWIND_MS < self._last_pos_ms
        prev_pos_ms, self._last_pos_ms = self._last_pos_ms, pos_ms

        if self._advance_loop_state(pos_ms, prev_pos_ms, rewound):
            return
        self._drive_device(pos_ms)
        self._advance_at_eof()

    def _advance_loop_state(
        self, pos_ms: float, prev_pos_ms: float, rewound: bool,
    ) -> bool:
        """What the loop machine makes of this tick; True when the tick is over.

        A recording that reached the end of the file closes there and starts,
        which moves the playhead — so nothing else in the tick is owed the old
        position and the caller stops.  The other two are wraps, and a wrap is a
        clock jump like any other.
        """
        if self._loops.marking:
            duration_ms = self._player.duration_ms
            near_end = (
                duration_ms > 0 and pos_ms >= duration_ms - _EOF_MARGIN_MS
            )
            wrapped = rewound and pos_ms < _EOF_WRAP_START_MS
            if near_end or wrapped:
                # Recording ran to the end of the file: close the loop at the
                # end and start it now.  near_end fires just before loop-file
                # (inf) wraps the whole video to the start, so the A/B loop
                # takes over without the opening frames flashing; wrapped is
                # the fallback if a tick only lands after the wrap.  Either
                # way the out point stays just short of the file end, which
                # mpv loops cleanly.
                self._loops.finish_at(int(pos_ms if near_end else prev_pos_ms))
                return True
        elif self._loops.running and rewound:
            # mpv's A/B loop wraps B->A by rewinding the clock.
            self._take_the_device_over()
        elif rewound:
            # The plain locked wrap (loop-file): a seek to the start in all but
            # name.
            self._take_the_device_over()
        return False

    def _drive_device(self, pos_ms: float) -> None:
        """Where the script says the device should be by now, or its rest."""
        if not self._tcode_enabled:
            return
        if self._funscript is not None:
            self._tcode.update(int(pos_ms), self._funscript, speed=self._speed)
        else:
            # No funscript to drive from: rest the OSR2 at its closest
            # position rather than leave it wherever the last video left it.
            self._tcode.park()

    def _advance_at_eof(self) -> None:
        """The end of the file, with nothing holding it: step to the next entry,
        wrapping at the end so the playlist plays around.

        Only ever reached unlocked — a lock is mpv's own loop-file, which
        restarts the file rather than ending it — and never mid-loop, where the
        A/B range owns the end.

        The latch is because loadfile is asynchronous: mpv goes on reporting
        end-of-file for a tick or two after the step is issued, and reading that
        again would step past a whole video before the new one had opened.  It
        clears on the first tick the player is playing again, so a short video
        ending immediately still steps off.
        """
        if not self._player.eof:
            self._stepped_at_eof = False
        elif not self._stepped_at_eof and self._loops.idle:
            self._stepped_at_eof = True
            self.load(self._index + 1)

    def close(self) -> None:
        self._tcode.close()
        self._player.close()

    def load(self, index: int) -> None:
        self._index = index % len(self._playlist)
        vid_path, fs_path = self._playlist[self._index]
        logger.info("Loading: %s", vid_path.name)
        # A seek still waiting on the outgoing file belonged to that file; the
        # incoming one starts at the top unless the caller asks otherwise.
        self._pending_seek_ms = None
        self._funscript = load_funscript(fs_path) if fs_path is not None else None
        self._loops.open(self._funscript)
        self._player.load(vid_path)
        self._player.set_paused(self._paused)
        self._take_the_device_over()
        self._last_pos_ms = 0.0

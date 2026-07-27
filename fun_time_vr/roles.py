"""The primary player's role in the VR process: Nau's contract, in-process.

fun_time drives its primary player through the nau file quartet — a playlist
with a funscript column, an overwrite command file, a paused flag, a status
file the dispatch loop parses back.  In VR that player is not a separate
window-owning process but one surface of the single OpenXR scene, so this role
speaks the same contract from inside the VR player: the verb subset the
orchestrator actually sends, funscript→T-Code exactly as Nau drives it (via
the shared ``player_core`` driver), nau-shaped status fields, and the one
verb that exists only here, ``CYCLE_PROJECTION``.

The satellites need no such twin: their session/verb/status logic is already
display-agnostic in this repo's ``satellite`` package, and the VR player
composes those pieces directly (see fun_time_vr.player).

Nau verbs outside this subset (loop recording, version cycling, clip jumps,
length modes, compilations) are not in the VR prototype yet; they report
unhandled and the player logs them once rather than crashing.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Sequence

from player_core.funscript import Funscript
from player_core.funscript import load as load_funscript
from player_core.playlist import read_playlist

from .projection import next_projection, resolve_projection, save_projection

logger = logging.getLogger(__name__)

# Nau's own steps and clamps (nau.runtime / nau.session), so the primary feels
# identical in and out of the headset.
SEEK_STEP_MS = 10_000
SPEED_STEP = 0.25
MIN_SPEED = 0.25
MAX_SPEED = 2.0


class PrimaryRole:
    def __init__(
        self,
        *,
        player,
        driver,
        playlist_file: Path,
        metadata_root: Path | None,
        vr_dirs: Sequence[Path],
        start_paused: bool = False,
    ) -> None:
        self._player = player
        self._driver = driver
        self._playlist_file = Path(playlist_file)
        self._metadata_root = metadata_root
        self._vr_dirs = tuple(vr_dirs)
        self._entries = read_playlist(self._playlist_file)
        if not self._entries:
            raise ValueError(f"primary playlist is empty: {playlist_file}")
        self._index = 0
        self._paused = start_paused
        self._speed = 1.0
        self._tcode_enabled = True
        self._funscript: Funscript | None = None
        self._projection = ""
        self._volume = 100
        self._muted = False
        # Until the host says the sound is live, a SET_VOLUME records the level
        # without unmuting the player: in VR the primary starts silent and the
        # host hands it its sound once the headset is presenting (see
        # fun_time_vr.player.route_audio), so an early command must not
        # un-silence it before then.  Reported muted meanwhile, which is what
        # the console draws and what unmuting returns to.
        self.audio_live = False
        # Level-set by RECENTER and drained by the host each frame: the role
        # only carries the request, because re-zeroing the scene onto the
        # current head pose is the host's to do — no player state moves.
        self._recenter_requested = False
        self._load(0)

    # ------------------------------------------------------------------ state

    @property
    def current_video(self) -> Path:
        return self._entries[self._index][0]

    @property
    def projection(self) -> str:
        return self._projection

    @property
    def has_funscript(self) -> bool:
        return self._funscript is not None

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def position_ms(self) -> float:
        return self._player.position_ms

    @property
    def duration_ms(self) -> float:
        return self._player.duration_ms

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    def _funscript_resting(self) -> bool:
        if self._funscript is None:
            return False
        return self._funscript.is_resting_at(int(self._player.position_ms))

    # ------------------------------------------------------------------ verbs

    def apply_command(self, command: str, *, on_quit: Callable[[], None] | None = None) -> bool:
        """Dispatch one command-file line; return whether it was handled."""
        parts = command.strip().split(None, 1)
        if not parts:
            return False
        keyword = parts[0].upper()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if keyword == "NEXT":
            self._load(self._index + 1)
        elif keyword == "PREV":
            self._load(self._index - 1)
        elif keyword == "SEEK_FWD":
            self._player.seek_ms(self._player.position_ms + SEEK_STEP_MS)
        elif keyword == "SEEK_BACK":
            self._player.seek_ms(self._player.position_ms - SEEK_STEP_MS)
        elif keyword == "SPEED_UP":
            self._set_speed(self._speed + SPEED_STEP)
        elif keyword == "SPEED_DOWN":
            self._set_speed(self._speed - SPEED_STEP)
        elif keyword == "SET_SPEED" and arg:
            self._apply_set_speed(arg)
        elif keyword == "SET_VOLUME" and arg:
            self._apply_set_volume(arg)
        elif keyword == "PLAY_FILE" and arg:
            self._apply_play_file(arg)
        elif keyword == "RELOAD_PLAYLIST":
            self._reload_playlist()
        elif keyword == "CYCLE_PROJECTION":
            self._cycle_projection()
        elif keyword == "RECENTER":
            self._recenter_requested = True
        elif keyword == "SET_TCODE_ENABLED" and arg:
            self._tcode_enabled = arg.strip() != "0"
        elif keyword in ("SET_HYBRID", "DISPLAY_ON", "DISPLAY_OFF"):
            # Accepted so a mode switch is not "unhandled" — both verbs ride
            # every one.  The VR scene has no Genau panel to make room for or to
            # hand the display to yet (that arrives with genau mode), so there is
            # nothing to step aside from and nothing to go dark for: blanking
            # here would leave the headset showing nothing at all.
            pass
        elif keyword == "QUIT":
            if on_quit is None:
                return False
            on_quit()
        else:
            return False
        return True

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        self._player.set_paused(paused)

    def tick(self, now: float) -> None:
        """Drive the OSR2 for this instant: waypoints while scripted, parked
        while unscripted, silent while paused or handed off (SET_TCODE_ENABLED
        0 — in hybrid the arbiter gives those stretches to Genau, and two
        drivers must never feed the broker's one inlet)."""
        if self._paused or not self._tcode_enabled:
            return
        if self._funscript is not None:
            self._driver.update(
                int(self._player.position_ms), self._funscript, now=now, speed=self._speed
            )
        else:
            self._driver.park(now=now)

    def take_recenter(self) -> bool:
        """Whether a RECENTER arrived since last asked (consumes the request)."""
        taken = self._recenter_requested
        self._recenter_requested = False
        return taken

    def status_fields(self) -> dict[str, str]:
        """Nau's own status contract, so the dispatch loop's parser, the hybrid
        arbiter and watch tracking read this player exactly as they read Nau."""
        return {
            "video": str(self.current_video),
            "position_ms": str(int(self._player.position_ms)),
            "duration_ms": str(int(self._player.duration_ms)),
            "has_funscript": "1" if self.has_funscript else "0",
            "funscript_resting": "1" if self._funscript_resting() else "0",
            "state": "normal",
            "paused": "1" if self._paused else "0",
        }

    def close(self) -> None:
        self._driver.close()
        self._player.close()

    # ---------------------------------------------------------------- helpers

    def _load(self, index: int) -> None:
        self._index = index % len(self._entries)
        video, funscript = self._entries[self._index]
        logger.info("Primary loading: %s", Path(video).name)
        self._player.load(video)
        self._player.set_paused(self._paused)
        self._player.set_speed(self._speed)
        self._funscript = self._load_funscript(funscript)
        self._driver.reset()
        self._projection = resolve_projection(str(video), self._metadata_root, self._vr_dirs)

    @staticmethod
    def _load_funscript(path: Path | None) -> Funscript | None:
        if path is None or not Path(path).is_file():
            return None
        try:
            return load_funscript(Path(path))
        except (OSError, ValueError, KeyError):
            logger.warning("Unreadable funscript %s", path, exc_info=True)
            return None

    def _set_speed(self, speed: float) -> None:
        self._speed = max(MIN_SPEED, min(MAX_SPEED, speed))
        self._player.set_speed(self._speed)

    @property
    def speed(self) -> float:
        return self._speed

    def _apply_set_speed(self, arg: str) -> None:
        if arg == "min":
            self._set_speed(MIN_SPEED)
            return
        if arg == "max":
            self._set_speed(MAX_SPEED)
            return
        try:
            self._set_speed(float(arg))
        except ValueError:
            logger.warning("SET_SPEED with unreadable argument: %r", arg)

    def _apply_set_volume(self, arg: str) -> None:
        parts = arg.split()
        try:
            level = max(0, min(100, int(parts[0])))
        except ValueError:
            logger.warning("SET_VOLUME with unreadable argument: %r", arg)
            return
        self._volume = level
        self._muted = len(parts) > 1 and parts[1].strip() == "1"
        self._player.set_volume(level)
        if self.audio_live:
            self._player.set_muted(self._muted)

    def _apply_play_file(self, arg: str) -> None:
        """Jump to the named video if queued, else splice it in after the
        current one — the same jump-or-splice every player answers PLAY_FILE
        with.  A TAB carries the funscript column, exactly as the playlist does."""
        video_raw, _, funscript_raw = arg.partition("\t")
        video = Path(video_raw.strip())
        funscript = Path(funscript_raw.strip()) if funscript_raw.strip() else None
        for position, (queued, _fs) in enumerate(self._entries):
            if queued == video:
                self._load(position)
                return
        self._entries.insert(self._index + 1, (video, funscript))
        self._load(self._index + 1)

    def _reload_playlist(self) -> None:
        """Swap in the rebuilt playlist file, keeping the playing video playing
        when it survived the rebuild (an F-mode toggle must not restart it)."""
        entries = read_playlist(self._playlist_file)
        if not entries:
            logger.warning("Reload found an empty playlist; keeping the current one")
            return
        current = self.current_video
        self._entries = entries
        for position, (video, _fs) in enumerate(self._entries):
            if video == current:
                self._index = position
                return
        self._load(0)

    def _cycle_projection(self) -> None:
        self._projection = next_projection(self._projection)
        save_projection(str(self.current_video), self._metadata_root, self._projection)
        logger.info("Projection: %s (%s)", self._projection, self.current_video.name)

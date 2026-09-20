"""The main player's role in the VR process: the desktop main player's contract, in-process.

The main player's file quartet — playlist, command file, paused flag, status file —
spoken from inside the VR player: the verbs of the family's it answers
(:mod:`player_core.player_verbs`), funscript→T-Code through the shared
``player_core`` driver, the same status fields, the headset's own verbs, and
what UNIMPLEMENTED_MAIN_PLAYER_VERBS refuses.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from player_core.control_registry import Control, Verb, bind, look_up
from player_core.funscript import Funscript
from player_core.funscript import load as load_funscript
from player_core.playback_rate import RATE_STEP, clamp_rate, parse_rate
from player_core.player_verbs import (
    DISPLAY_OFF,
    DISPLAY_ON,
    LOCK_OFF,
    LOCK_ON,
    NEXT,
    PLAY_FILE,
    PREV,
    QUIT,
    RELOAD_PLAYLIST,
    SEEK_BACK,
    SEEK_FWD,
    SET_F_MODE,
    SET_PACE,
    SET_SPEED,
    SET_TCODE_ENABLED,
    SET_VOLUME,
    SPEED_DOWN,
    SPEED_UP,
    TOGGLE_LOCK,
    pace_seconds,
)
from player_core.playlist import item_from_line, read_playlist
from player_core.status import PlayerStatus
from player_core.status import status_fields as player_status_fields

from fun_time.event_log import SOURCE_MAIN, notice
from fun_time.media_metadata import load_metadata, metadata_path_for, video_title
from main_player.play_points import PlayPoints
from main_player.seeking import OwedSeek, seek_if_taken

from .projection import next_projection, resolve_projection, save_projection
from .video_scenes import scene_starts_ms

logger = logging.getLogger(__name__)

# The desktop main player's own seek step (main_player.controls), so the primary seeks alike in and out of the headset.
SEEK_STEP_MS = 10_000

SCENE_JUST_BEGUN_MS = 3_000
SCENE_JUMP_LANDS_WITHIN_MS = 500

TILT_STEP_DEG = 5.0
TILT_LIMIT_DEG = 90.0

# The headset's own verbs: a projection to walk, a heading to re-zero onto, a
# tilt, a scene to jump to.  Spelled here, beside the registry that answers them,
# the way a player's own verbs are everywhere in this family.
CYCLE_PROJECTION = "CYCLE_PROJECTION"
RECENTER = "RECENTER"
LAYOUT_RESET = "LAYOUT_RESET"
TILT_UP = "TILT_UP"
TILT_DOWN = "TILT_DOWN"
TILT_RESET = "TILT_RESET"
NEXT_SCENE = "NEXT_SCENE"
PREV_SCENE = "PREV_SCENE"

#: The only place a control may be left dead in VR: the parity suite holds every
#: key and every phrase to this list or to a role that answers it.
UNIMPLEMENTED_MAIN_PLAYER_VERBS: dict[str, str] = {
    "RECORD_DOWN": "loop recording needs the main player's loop machine",
    "RECORD_UP": "loop recording needs the main player's loop machine",
    "RECORD_TAP": "loop recording needs the main player's loop machine",
    "LOOP_CANCEL": "there is no A/B loop here to cancel",
    "SET_LOOP": "there is no A/B loop here to restore",
    "CYCLE_VERSION": "version cycling needs the main player's same-content index",
    "CYCLE_VERSION_BACK": "version cycling needs the main player's same-content index",
    "TOGGLE_LENGTH_MODE": "the length modes need the main player's duration cache",
    "SET_LENGTH_MODE": "the length modes need the main player's duration cache",
    "PLAY_COMPILATION": "a compilation is built from the length modes above",
    "END_COMPILATION": "a compilation is built from the length modes above",
    "PLAY_FULL_VID": "the clip/full-video pair needs the main player's sidecar index",
    "PLAY_CLIP_JUMP": "the clip/full-video pair needs the main player's sidecar index",
    "JUMP_TO_FUNSCRIPT": "funscript navigation needs the main player's parsed-script window",
    "NEXT_FUNSCRIPTED": "funscript navigation needs the main player's parsed-script window",
}


def _recorded_for(video: Path, metadata_root: Path | None) -> dict:
    """Everything Evolver recorded about *video*, ``{}`` where that is nothing."""
    sidecar = metadata_path_for(video, metadata_root)
    return {} if sidecar is None else load_metadata(sidecar)


class HostRequest:
    def __init__(self) -> None:
        self._asked = False

    def ask(self) -> None:
        self._asked = True

    def take(self) -> bool:
        if not self._asked:
            return False
        self._asked = False
        return True


class MainRole:
    def __init__(
        self,
        *,
        player,
        driver,
        playlist_file: Path,
        metadata_root: Path | None,
        vr_dirs: Sequence[Path],
        start_paused: bool = False,
        play_points: PlayPoints | None = None,
    ) -> None:
        self._player = player
        self._driver = driver
        self._playlist_file = Path(playlist_file)
        self._metadata_root = metadata_root
        self._vr_dirs = tuple(vr_dirs)
        self._play_points = play_points or PlayPoints(None)
        self._resume = OwedSeek()
        self._entries = read_playlist(self._playlist_file)
        if not self._entries:
            raise ValueError(f"primary playlist is empty: {playlist_file}")
        self._index = 0
        self._paused = start_paused
        self._held_at: float | None = None  # see set_paused
        self._speed = 1.0
        self._tcode_enabled = True
        self._locked = True
        self._stepped_at_eof = False
        self._scripted_filter = False
        self._funscript: Funscript | None = None
        self._projection = ""
        self._title = ""
        self._scene_starts: tuple[float, ...] = ()
        self._volume = 100
        self._muted = False
        # Until the host says the sound is live, a SET_VOLUME records the level
        # without unmuting (see :meth:`sound_goes_live`).
        self._audio_live = False
        self.recenter = HostRequest()
        self.layout_reset = HostRequest()
        self._tilt_deg = 0.0  # state, not a request; both inputs write here
        # Whether this player is what the headset shows: DISPLAY_OFF rides every
        # switch into genau mode, where the clip takes the scene instead.
        self.displayed = True
        self._load(0)

    # ------------------------------------------------------------------ state

    @property
    def current_video(self) -> Path:
        return self._entries[self._index].path

    @property
    def title(self) -> str:
        """What the console panel calls the video on screen."""
        return self._title

    @property
    def projection(self) -> str:
        return self._projection

    @property
    def tilt_deg(self) -> float:
        return self._tilt_deg

    @property
    def has_funscript(self) -> bool:
        return self._funscript is not None

    @property
    def current_funscript(self) -> Funscript | None:
        return self._funscript

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def paused(self) -> bool:
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

    @property
    def locked(self) -> bool:
        """Published: the console drawing the padlock is not always this player."""
        return self._locked

    @property
    def scripted_filter(self) -> bool:
        """A narrowed playlist looks like any other, so the panel is told."""
        return self._scripted_filter

    def _funscript_resting(self) -> bool:
        if self._funscript is None:
            return False
        return self._funscript.is_resting_at(int(self._player.position_ms))

    # ------------------------------------------------------------------ verbs

    def apply_command(self, command: str, *, on_quit: Callable[[], None]) -> bool:
        """Look one command-file line up in the registry; whether it was handled."""
        return look_up(command, VERBS, _Reach(self, on_quit))

    def step(self, delta: int) -> None:
        self._load(self._index + delta)

    def seek_by(self, delta_ms: float) -> None:
        self.seek_to(self._player.position_ms + delta_ms)

    def adjust_speed(self, delta: float) -> None:
        self._set_speed(self._speed + delta)

    def toggle_lock(self) -> None:
        self.set_locked(not self._locked)

    def set_scripted_filter_from(self, value: str) -> bool:
        self._scripted_filter = value.strip() != "0"
        return True

    def reset_tilt(self) -> None:
        self._tilt_deg = 0.0

    def set_tcode_enabled_from(self, value: str) -> bool:
        enabled = value.strip() != "0"
        # Re-enabling is a takeover — the device is wherever Genau's motion
        # left it — so reset the driver: the next tick re-sends a waypoint at
        # once, with the handoff glide.
        if enabled and not self._tcode_enabled:
            self._driver.reset()
        self._tcode_enabled = enabled
        return True

    def set_displayed(self, displayed: bool) -> None:
        # The mirror of the HUD verb Genau's role gets, so the two roles
        # cannot both claim the scene or both step out of it.
        self.displayed = displayed

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        self._player.set_paused(paused)
        # Un-pausing is a request, not a picture; the device led it on every reveal.
        self._held_at = None if paused else self._player.position_ms

    def _the_screen_has_resumed(self) -> bool:
        if self._held_at is None:
            return True
        if self._player.position_ms == self._held_at and not self._player.showing_picture:
            return False
        self._held_at = None
        return True

    def tick(self, now: float) -> None:
        """One turn of the pump: resume, step off the end of an unlocked video,
        write down where this one is, then drive the OSR2 for this instant --
        waypoints while scripted, parked while unscripted, silent while paused
        or handed to the Robot Hand."""
        self._player.push_still()
        self._resume.pay(self._player, self.seek_to)
        self._step_at_eof()
        if self._paused:
            return
        self._play_points.observe(
            self.current_video, self._player.position_ms, self._player.duration_ms)
        if not self._tcode_enabled:
            return
        if not self._the_screen_has_resumed():
            return
        if self._funscript is not None:
            self._driver.update(
                int(self._player.position_ms), self._funscript, now=now, speed=self._speed
            )
        else:
            self._driver.park(now=now)

    def _step_at_eof(self) -> None:
        """The end of the file, with nothing holding it: on to the next entry,
        under the main player's own latch (``main_player.session.advance``) against a second read."""
        if self._paused or self._locked:
            return
        if not self._player.eof:
            self._stepped_at_eof = False
        elif not self._stepped_at_eof:
            self._stepped_at_eof = True
            self._play_points.ended()
            self._load(self._index + 1)

    def seek_to(self, position_ms: float) -> bool:
        return seek_if_taken(
            self._player, max(0.0, min(self._player.duration_ms, position_ms)))

    def nudge_tilt(self, degrees: float) -> None:
        self._tilt_deg = max(-TILT_LIMIT_DEG, min(TILT_LIMIT_DEG, self._tilt_deg + degrees))

    def status_fields(self, handoff_touch_ms: int | None) -> dict[str, str]:
        """The desktop main player's status contract, read by the dispatch loop the same way.
        *handoff_touch_ms* is where the console panel drew Genau's turn ending
        (None for none, published empty: zero is a real media time)."""
        return {
            **player_status_fields(PlayerStatus(
                video=str(self.current_video),
                position_ms=int(self._player.position_ms),
                duration_ms=int(self._player.duration_ms),
                paused=self._paused,
                locked=self._locked,
                speed=self._speed,
                picture=self._player.showing_picture,
            )),
            "has_funscript": "1" if self.has_funscript else "0",
            "funscript_resting": "1" if self._funscript_resting() else "0",
            "state": "normal",
            "handoff_touch_ms": "" if handoff_touch_ms is None else str(int(handoff_touch_ms)),
            "speed": str(self._speed),
        }

    def close(self) -> None:
        self._play_points.leave()
        self._driver.close()
        self._player.close()

    # ---------------------------------------------------------------- helpers

    def _load(self, index: int) -> None:
        self._play_points.leave()
        self._index = index % len(self._entries)
        item = self._entries[self._index]
        logger.info("Main loading: %s", item.path.name)
        self._player.load(item.path)
        self._player.set_paused(self._paused)
        self._player.set_speed(self._speed)
        self._funscript = self._load_funscript(item.funscript)
        self._driver.reset()
        self._projection = resolve_projection(str(item.path), self._metadata_root, self._vr_dirs)
        self._title = video_title(_recorded_for(item.path, self._metadata_root), item.path)
        self._resume.owe(self._play_points.point_for(item.path) or None)
        self._scene_starts = self._read_scene_starts(item.path)

    def _read_scene_starts(self, video: Path) -> tuple[float, ...]:
        sidecar = metadata_path_for(video, self._metadata_root)
        return scene_starts_ms(load_metadata(sidecar)) if sidecar is not None else ()

    def next_scene(self) -> None:
        position = self._player.position_ms
        later = [start for start in self._scene_starts
                 if start > position + SCENE_JUMP_LANDS_WITHIN_MS]
        self._seek_to_scene(later[0] if later else None, "No next scene")

    def previous_scene(self) -> None:
        position = self._player.position_ms
        earlier = [start for start in self._scene_starts
                   if start < position - SCENE_JUST_BEGUN_MS]
        self._seek_to_scene(earlier[-1] if earlier else None, "No previous scene")

    def _seek_to_scene(self, start: float | None, none_there: str) -> None:
        if start is not None:
            self._player.seek_ms(start)
        elif self._scene_starts:
            notice(logger, none_there, source=SOURCE_MAIN)
        else:
            notice(logger, "No scenes are marked in this video yet", source=SOURCE_MAIN)

    @staticmethod
    def _load_funscript(path: Path | None) -> Funscript | None:
        if path is None or not Path(path).is_file():
            return None
        try:
            return load_funscript(Path(path))
        except (OSError, ValueError, KeyError):
            logger.warning("Unreadable funscript %s", path, exc_info=True)
            return None

    def set_locked(self, locked: bool) -> None:
        """Hold the video on screen or hand its end back to the playlist: mpv's
        own ``loop_file``, this family's one lock, and the latch with it."""
        self._locked = locked
        self._stepped_at_eof = False
        self._player.set_loop_file(locked)

    def _set_speed(self, speed: float) -> None:
        self._speed = clamp_rate(speed)
        self._player.set_speed(self._speed)

    def set_speed_from(self, value: str) -> bool:
        """``SET_SPEED min|max|<multiplier>``; False on a value it cannot read."""
        rate = parse_rate(value)
        if rate is None:
            return False
        self._set_speed(rate)
        return True

    def set_pace_from(self, value: str) -> bool:
        seconds = pace_seconds(value)
        if seconds is None:
            return False
        self._player.set_pace(seconds)
        return True

    def sound_goes_live(self) -> None:
        """The headset is presenting this player: whatever a SET_VOLUME recorded
        while it warmed up comes on now."""
        self._audio_live = True
        self._player.set_volume(self._volume)
        self._player.set_muted(self._muted)

    def set_volume_from(self, value: str) -> bool:
        """``SET_VOLUME <0-100> [muted]``; False on a level it cannot read."""
        parts = value.split()
        try:
            level = max(0, min(100, int(parts[0])))
        except ValueError:
            return False
        self._volume = level
        self._muted = len(parts) > 1 and parts[1].strip() == "1"
        self._player.set_volume(level)
        if self._audio_live:
            self._player.set_muted(self._muted)
        return True

    def play_file_from(self, value: str) -> bool:
        """Jump to the named video if queued, else splice it in after the current
        one; the value is a playlist line, funscript column and all."""
        item = item_from_line(value)
        if item is None:
            return False
        for position, queued in enumerate(self._entries):
            if queued.path == item.path:
                self._load(position)
                return True
        self._entries.insert(self._index + 1, item)
        self._load(self._index + 1)
        return True

    def reload_playlist(self) -> None:
        """Swap in the rebuilt playlist, keeping a playing video that survived it."""
        entries = read_playlist(self._playlist_file)
        if not entries:
            logger.warning("Reload found an empty playlist; keeping the current one")
            return
        current = self.current_video
        self._entries = entries
        for position, item in enumerate(self._entries):
            if item.path == current:
                self._index = position
                return
        self._load(0)

    def reopen(self) -> None:
        """Load the current video again and seek back to where it was -- the way
        out of a wedged pipeline, since mpv builds a whole new one, its audio
        output included, and nothing else about the role moves."""
        position_ms = self._player.position_ms
        logger.warning("Reopening the main player at %.0fms", position_ms)
        self._load(self._index)
        if position_ms:
            self._resume.owe(position_ms)
            self._resume.pay(self._player, self.seek_to)

    def cycle_projection(self) -> None:
        self._projection = next_projection(self._projection)
        save_projection(str(self.current_video), self._metadata_root, self._projection)
        logger.info("Projection: %s (%s)", self._projection, self.current_video.name)


@dataclass(frozen=True)
class _Reach:
    """What a verb reaches: the role, and the host's quit for this one call."""

    role: MainRole
    on_quit: Callable[[], None]


Act = Callable[[_Reach, str], bool]


def _moves(move: Callable[[MainRole], object]) -> Act:
    """A verb that takes no value and always lands."""
    def act(reach: _Reach, _value: str) -> bool:
        move(reach.role)
        return True
    return act


def _reads(move: Callable[[MainRole, str], bool]) -> Act:
    """A verb whose value the role reads, answering whether it could."""
    def act(reach: _Reach, value: str) -> bool:
        return move(reach.role, value)
    return act


def _quit(reach: _Reach, _value: str) -> bool:
    reach.on_quit()
    return True


# One entry per thing a person can move, on the family's spellings where the
# desktop main player answers the same verb, and on the headset's own for the
# rest.  What is refused is UNIMPLEMENTED_MAIN_PLAYER_VERBS above.
CONTROLS: tuple[Control, ...] = (
    Control(
        name="playlist_position",
        verbs=(Verb(NEXT, _moves(lambda role: role.step(1))),
               Verb(PREV, _moves(lambda role: role.step(-1)))),
    ),
    Control(
        name="playhead",
        verbs=(Verb(SEEK_FWD, _moves(lambda role: role.seek_by(SEEK_STEP_MS))),
               Verb(SEEK_BACK, _moves(lambda role: role.seek_by(-SEEK_STEP_MS)))),
    ),
    Control(
        name="speed",
        verbs=(Verb(SPEED_UP, _moves(lambda role: role.adjust_speed(RATE_STEP))),
               Verb(SPEED_DOWN, _moves(lambda role: role.adjust_speed(-RATE_STEP))),
               Verb(SET_SPEED, _reads(MainRole.set_speed_from), takes_a_value=True)),
    ),
    Control(
        name="volume",
        verbs=(Verb(SET_VOLUME, _reads(MainRole.set_volume_from), takes_a_value=True),),
    ),
    Control(
        name="pace",
        verbs=(Verb(SET_PACE, _reads(MainRole.set_pace_from), takes_a_value=True),),
    ),
    Control(
        name="playing_file",
        verbs=(Verb(PLAY_FILE, _reads(MainRole.play_file_from), takes_a_value=True),),
    ),
    Control(name="playlist", verbs=(Verb(RELOAD_PLAYLIST, _moves(MainRole.reload_playlist)),)),
    Control(
        name="lock",
        verbs=(Verb(TOGGLE_LOCK, _moves(MainRole.toggle_lock)),
               Verb(LOCK_ON, _moves(lambda role: role.set_locked(True))),
               Verb(LOCK_OFF, _moves(lambda role: role.set_locked(False)))),
    ),
    Control(
        name="f_mode",
        verbs=(Verb(SET_F_MODE, _reads(MainRole.set_scripted_filter_from), takes_a_value=True),),
    ),
    Control(name="projection", verbs=(Verb(CYCLE_PROJECTION, _moves(MainRole.cycle_projection)),)),
    Control(name="heading", verbs=(Verb(RECENTER, _moves(lambda role: role.recenter.ask())),)),
    Control(name="layout", verbs=(Verb(LAYOUT_RESET, _moves(lambda role: role.layout_reset.ask())),)),
    Control(
        name="scene",
        verbs=(Verb(NEXT_SCENE, _moves(MainRole.next_scene)),
               Verb(PREV_SCENE, _moves(MainRole.previous_scene))),
    ),
    Control(
        name="tilt",
        verbs=(Verb(TILT_UP, _moves(lambda role: role.nudge_tilt(TILT_STEP_DEG))),
               Verb(TILT_DOWN, _moves(lambda role: role.nudge_tilt(-TILT_STEP_DEG))),
               Verb(TILT_RESET, _moves(MainRole.reset_tilt))),
    ),
    Control(
        name="tcode_output",
        verbs=(Verb(SET_TCODE_ENABLED, _reads(MainRole.set_tcode_enabled_from),
                    takes_a_value=True),),
    ),
    Control(
        name="display",
        verbs=(Verb(DISPLAY_ON, _moves(lambda role: role.set_displayed(True))),
               Verb(DISPLAY_OFF, _moves(lambda role: role.set_displayed(False)))),
    ),
    Control(name="quit", verbs=(Verb(QUIT, _quit),)),
)


VERBS = bind(CONTROLS)

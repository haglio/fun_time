"""The headset's own verbs on the Main Funestra, and what they move: the
projection each video is wrapped in and the field of view and height it is
dialed to, kept in the video's record; the heading the room is re-zeroed onto,
its tilt and its layout; the scenes a video's record marks.  Spelled beside the
registry that answers them, the way a player's own verbs are everywhere in this
family."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from player_core.control_registry import Control, Verb, bind, look_up

from fun_time.event_log import SOURCE_MAIN, notice
from fun_time.media_metadata import load_metadata, metadata_path_for

from .layout import clamp_tilt
from .projection import ProjectionMemory, next_projection, previous_projection
from .video_scenes import scene_starts_ms

logger = logging.getLogger(__name__)

SCENE_JUST_BEGUN_MS = 3_000
SCENE_JUMP_LANDS_WITHIN_MS = 500

TILT_STEP_DEG = 5.0

ANGLE_STEP = 1.01
DIAL_STILL_S = 0.6

CYCLE_PROJECTION = "CYCLE_PROJECTION"
CYCLE_PROJECTION_BACK = "CYCLE_PROJECTION_BACK"
WIDEN_PROJECTION = "WIDEN_PROJECTION"
NARROW_PROJECTION = "NARROW_PROJECTION"
RECENTER = "RECENTER"
LAYOUT_RESET = "LAYOUT_RESET"
TILT_UP = "TILT_UP"
TILT_DOWN = "TILT_DOWN"
TILT_RESET = "TILT_RESET"
NEXT_SCENE = "NEXT_SCENE"
PREV_SCENE = "PREV_SCENE"


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


class AngleRequest:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._factor = 1.0

    def ask(self, factor: float) -> None:
        with self._lock:
            self._factor *= factor

    def take(self) -> float:
        with self._lock:
            factor, self._factor = self._factor, 1.0
        return factor


class HeadsetVerbs:
    def __init__(
        self,
        playback,
        *,
        metadata_root: Path | None,
        vr_dirs: Sequence[Path],
        tilt_deg: float = 0.0,
    ) -> None:
        self._playback = playback
        self._metadata_root = metadata_root
        self._vr_dirs = tuple(vr_dirs)
        self._remembered = ProjectionMemory(metadata_root, self._vr_dirs)
        self._look = None
        self._projections: dict[str, str] = {}
        self._fovs: dict[str, float] = {}
        self._heights: dict[str, float] = {}
        self._dial_seen: tuple[float | None, float | None] = (None, None)
        self._dial_changed_at: float | None = None
        self._scene_starts: tuple[float, ...] = ()
        self._tilt_deg = clamp_tilt(tilt_deg)
        self.recenter = HostRequest()
        self.angle_asked = AngleRequest()
        self.layout_reset = HostRequest()
        self._video: Path = playback.current_video
        self._loads_seen = playback.loads
        self._arrive()

    @property
    def projection(self) -> str:
        return self.projection_of(str(self._video))

    def projection_of(self, video: str | None) -> str:
        return self._projections.get(video, "")

    def fov_of(self, video: str | None) -> float | None:
        return self._fovs.get(video)

    def height_of(self, video: str | None) -> float | None:
        return self._heights.get(video)

    @property
    def tilt_deg(self) -> float:
        return self._tilt_deg

    def apply(self, command: str) -> bool:
        return look_up(command, VERBS, self)

    def tick(self, now: float) -> None:
        if self._playback.loads != self._loads_seen:
            self._loads_seen = self._playback.loads
            self._remember_the_dial_before_leaving()
            self._video = self._playback.current_video
            self._arrive()
        self._remember_the_dial_once_still(now)

    def close(self) -> None:
        if self._look is not None:
            self._look.stop_looking()

    def _arrive(self) -> None:
        video = str(self._video)
        self._projections[video] = self._remembered.resolve(video)
        self._restore_fov_and_height(video)
        self._look_at_the_picture(self._video)
        self._scene_starts = scene_starts_ms(self._recorded_for(self._video))

    def _restore_fov_and_height(self, video: str) -> None:
        self._fovs.pop(video, None)
        self._heights.pop(video, None)
        if (fov := self._remembered.saved_fov(video)) is not None:
            self._fovs[video] = fov
        if (height := self._remembered.saved_height(video)) is not None:
            self._heights[video] = height
        self._dial_seen, self._dial_changed_at = self._dial(), None

    def _dial(self) -> tuple[float | None, float | None]:
        video = str(self._video)
        return self._fovs.get(video), self._heights.get(video)

    def _remember_the_dial_once_still(self, now: float) -> None:
        dial = self._dial()
        if dial != self._dial_seen:
            self._dial_seen, self._dial_changed_at = dial, now
        elif self._dial_changed_at is not None and now - self._dial_changed_at >= DIAL_STILL_S:
            self._dial_changed_at = None
            self._remember_fov_and_height()

    def _remember_the_dial_before_leaving(self) -> None:
        if self._dial_changed_at is not None or self._dial() != self._dial_seen:
            self._dial_seen, self._dial_changed_at = self._dial(), None
            self._remember_fov_and_height()

    def look_with(self, look) -> None:
        self._look = look
        self._look_at_the_picture(self._video)

    def _look_at_the_picture(self, video: Path) -> None:
        if self._look is None:
            return
        if self._remembered.wants_a_look(str(video)):
            self._look.look_at(video, self._shape_found)
        else:
            self._look.stop_looking()

    def _shape_found(self, video: Path, shape: str) -> None:
        self._remembered.note_shape(str(video), shape)
        self._projections[str(video)] = self._remembered.resolve(str(video))
        logger.info(
            "Projection: %s (%s, off its picture)", self._projections[str(video)], video.name)

    def _recorded_for(self, video: Path) -> dict:
        sidecar = metadata_path_for(video, self._metadata_root, outlying_dirs=self._vr_dirs)
        return {} if sidecar is None else load_metadata(sidecar)

    def next_scene(self) -> None:
        position = self._playback.position_ms
        later = [start for start in self._scene_starts
                 if start > position + SCENE_JUMP_LANDS_WITHIN_MS]
        self._seek_to_scene(later[0] if later else None, "No next scene")

    def previous_scene(self) -> None:
        position = self._playback.position_ms
        earlier = [start for start in self._scene_starts
                   if start < position - SCENE_JUST_BEGUN_MS]
        self._seek_to_scene(earlier[-1] if earlier else None, "No previous scene")

    def _seek_to_scene(self, start: float | None, none_there: str) -> None:
        if start is not None:
            self._playback.seek_to(start)
        elif self._scene_starts:
            notice(logger, none_there, source=SOURCE_MAIN)
        else:
            notice(logger, "No scenes are marked in this video yet", source=SOURCE_MAIN)

    def cycle_projection(self) -> None:
        self._watch_in(next_projection(self.projection))

    def cycle_projection_back(self) -> None:
        self._watch_in(previous_projection(self.projection))

    def _watch_in(self, projection: str) -> None:
        self._projections[str(self._video)] = projection
        self._remembered.save(str(self._video), projection)
        logger.info("Projection: %s (%s)", projection, self._video.name)

    def set_fov(self, degrees: float) -> None:
        self._fovs[str(self._video)] = degrees

    def set_height(self, height: float) -> None:
        self._heights[str(self._video)] = height

    def _remember_fov_and_height(self) -> None:
        video = str(self._video)
        fov, height = self._fovs.get(video), self._heights.get(video)
        if fov is not None:
            self._remembered.save_fov(video, round(fov, 1))
        if height is not None:
            self._remembered.save_height(video, round(height, 2))
        logger.info(
            "Projection: %s (%s) field of view %s, height %s", self.projection,
            self._video.name, "as mastered" if fov is None else f"{fov:.0f} degrees",
            "as mastered" if height is None else f"{height:.2f}")

    def nudge_tilt(self, degrees: float) -> None:
        self._tilt_deg = clamp_tilt(self._tilt_deg + degrees)

    def reset_tilt(self) -> None:
        self._tilt_deg = 0.0

    def reset_layout(self) -> None:
        self.reset_tilt()
        self.layout_reset.ask()


Act = Callable[[HeadsetVerbs, str], bool]


def _moves(move: Callable[[HeadsetVerbs], object]) -> Act:
    def act(verbs: HeadsetVerbs, _value: str) -> bool:
        move(verbs)
        return True
    return act


CONTROLS: tuple[Control, ...] = (
    Control(
        name="projection",
        verbs=(Verb(CYCLE_PROJECTION, _moves(HeadsetVerbs.cycle_projection)),
               Verb(CYCLE_PROJECTION_BACK, _moves(HeadsetVerbs.cycle_projection_back))),
    ),
    Control(
        name="projection_angle",
        verbs=(Verb(WIDEN_PROJECTION, _moves(lambda verbs: verbs.angle_asked.ask(ANGLE_STEP))),
               Verb(NARROW_PROJECTION,
                    _moves(lambda verbs: verbs.angle_asked.ask(1 / ANGLE_STEP)))),
    ),
    Control(name="heading", verbs=(Verb(RECENTER, _moves(lambda verbs: verbs.recenter.ask())),)),
    Control(name="layout", verbs=(Verb(LAYOUT_RESET, _moves(HeadsetVerbs.reset_layout)),)),
    Control(
        name="scene",
        verbs=(Verb(NEXT_SCENE, _moves(HeadsetVerbs.next_scene)),
               Verb(PREV_SCENE, _moves(HeadsetVerbs.previous_scene))),
    ),
    Control(
        name="tilt",
        verbs=(Verb(TILT_UP, _moves(lambda verbs: verbs.nudge_tilt(TILT_STEP_DEG))),
               Verb(TILT_DOWN, _moves(lambda verbs: verbs.nudge_tilt(-TILT_STEP_DEG))),
               Verb(TILT_RESET, _moves(HeadsetVerbs.reset_tilt))),
    ),
)


VERBS = bind(CONTROLS)

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from app_support.threading_utils import start_daemon_thread

from .picture_shape import FISHEYE_CIRCLE, FULL_FRAME, shape_of

# The library's fisheye recordings open on up to half a minute of black, which
# says nothing; three minutes of looks outlasts that with room to spare.
BETWEEN_LOOKS_S = 2.0
LOOKS = 90
_LOOKS_TO_BELIEVE = {FISHEYE_CIRCLE: 2, FULL_FRAME: 3}


class PictureLook:
    """What a VR video's own picture shows, read from the thread that paints it
    (:mod:`fun_time_vr.video_thread`) -- mpv's render context is thread-bound,
    so a screenshot asked from elsewhere is refused, not answered.
    """

    def __init__(self, video, *, start_thread=start_daemon_thread,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._video = video
        self._start_thread = start_thread
        self._sleep = sleep
        self._latest_look: object = None

    def look_at(self, video: Path, on_shape: Callable[[Path, str], None]) -> None:
        self._latest_look = look = object()
        self._start_thread(target=self._look, args=(look, video, on_shape), name="picture-look")

    def close(self) -> None:
        self._latest_look = None

    def _look(self, look: object, video: Path, on_shape: Callable[[Path, str], None]) -> None:
        seen: Counter[str] = Counter()
        self._video.ask_for_a_still()
        for _ in range(LOOKS):
            self._sleep(BETWEEN_LOOKS_S)
            shape = self._shape_on_screen()
            if look is not self._latest_look:
                return
            if shape is None:
                continue
            seen[shape] += 1
            if seen[shape] == _LOOKS_TO_BELIEVE[shape]:
                on_shape(video, shape)
                return

    def _shape_on_screen(self) -> str | None:
        if self._video.duration_ms <= 0:
            return None
        frame = self._video.take_a_still()
        self._video.ask_for_a_still()  # for the look after this one
        return None if frame is None else shape_of(frame[..., :3].max(axis=2))

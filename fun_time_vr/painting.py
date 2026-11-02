"""The paint thread's decisions, and the showing of what it painted, off the GPU."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .frame_relay import FrameRelay, StillAsked, capped_size
from .paint_watch import (
    FLUSH,
    FLUSH_AFTER_S,
    GIVE_UP,
    GIVE_UP_AFTER_S,
    WAIT,
    FrameWatch,
    GpuWait,
)

logger = logging.getLogger(__name__)


def finished_on_the_gpu(wait: GpuWait, mark, what: str, *, gpu, name: str, now: float) -> bool:
    verdict = wait.judge(reached=mark.reached, now=now)
    if verdict == FLUSH:
        logger.warning("The %s video's %s has waited %.0fs for the GPU; flushing",
                       name, what, FLUSH_AFTER_S)
        gpu.flush()
    elif verdict == GIVE_UP:
        logger.error("The %s video's %s never finished on the GPU in %.0fs; showing it anyway",
                     name, what, GIVE_UP_AFTER_S)
    return verdict not in (WAIT, FLUSH)


class Painting:
    def __init__(self, engine, *, name: str, cap_px: int, gpu, relay: FrameRelay,
                 still_asked: StillAsked, perf=None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._engine = engine
        self._name = name
        self._cap_px = cap_px
        self._gpu = gpu
        self._relay = relay
        self._still = still_asked
        self._perf = perf
        self._clock = clock
        self._mark = gpu.mark()
        self._wait = GpuWait()
        self._watch = FrameWatch()
        self._drawn: tuple[int, int, int, int, str] | None = None

    def paint(self, targets: list, still) -> bool:
        """One pass; whether there was nothing to do but wait."""
        if self._drawn is not None:
            if not finished_on_the_gpu(self._wait, self._mark, "frame", gpu=self._gpu,
                                       name=self._name, now=self._clock()):
                return True
            slot, texture, width, height, video = self._drawn
            self._relay.painted(slot, texture=texture, width=width, height=height, video=video)
            if still is not None and self._still.wanted:
                self._still.leave(self._paint_a_still(still))
        self._drawn = self._draw_next(targets)
        if self._drawn is not None:
            self._watch.drew()
            return False
        now = self._clock()
        if self._watch.wants_a_reading(now):
            gone = self._watch.read(position_ms=self._engine.position_ms, now=now)
            if gone is not None:
                logger.warning("The %s video has drawn nothing for %.0fs while it played on",
                               self._name, gone)
        return True

    def close(self) -> None:
        self._mark.close()

    def _paint_a_still(self, still):
        # mpv paints it only on the thread its render context was made on;
        # asked from any other, it refuses rather than answers.
        self._engine.render(still.fbo, still.width, still.height)
        return self._gpu.read_pixels(still)

    def _draw_next(self, targets: list):
        if not self._engine.has_frame_to_draw:
            return None
        size = capped_size(self._engine.video_dims, self._cap_px)
        if size is None:
            return None
        started = time.perf_counter()
        slot = self._relay.slot_to_paint()
        target = targets[slot]
        target.ensure(*size)
        # flip_y: mpv renders top-left-origin; the scene samples GL lower-left
        # convention (verified against a top-half-white clip).
        video = self._engine.render(target.fbo, target.width, target.height, flip_y=True)
        self._mark.set()
        self._gpu.flush()
        if self._perf is not None:
            self._perf.note("paint", (time.perf_counter() - started) * 1e3)
        if video is None:
            return None
        return slot, target.texture, target.width, target.height, video


class Showing:
    """One copy in flight at a time, on the frame loop's thread."""

    def __init__(self, relay: FrameRelay, *, gpu, name: str,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._relay = relay
        self._gpu = gpu
        self._name = name
        self._clock = clock
        self._copy = None
        self._copy_wait = GpuWait()
        self._copying = False

    def show_newest(self, target) -> bool:
        if self._copying:
            if not finished_on_the_gpu(self._copy_wait, self._copy, "copy", gpu=self._gpu,
                                       name=self._name, now=self._clock()):
                return False
            self._copying = False
            self._relay.copied()
        frame = self._relay.take()
        if frame is None:
            return False
        if self._copy is None:
            self._copy = self._gpu.mark()
        target.ensure(frame.width, frame.height)
        target.video = frame.video
        self._gpu.copy(frame, target)
        self._copy.set()
        self._gpu.flush()
        self._copying = True
        target.painted = True
        return True

    def close(self) -> None:
        if self._copy is not None:
            self._copy.close()
            self._copy = None

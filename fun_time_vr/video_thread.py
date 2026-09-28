from __future__ import annotations

import logging
import threading
import time

import numpy as np
from app_support.threading_utils import start_daemon_thread
from OpenGL import GL

from .frame_relay import SLOTS, FrameRelay, StillAsked, capped_size
from .paint_watch import (
    FLUSH,
    FLUSH_AFTER_S,
    GIVE_UP,
    GIVE_UP_AFTER_S,
    WAIT,
    GpuWait,
    PictureWatch,
)
from .render import RenderTarget
from .scheduling import scheduled_as_a_game

logger = logging.getLogger(__name__)

PAINT_POLL_S = 0.002
# What the picture check reads a frame at: enough for a fisheye circle's edge,
# small enough that painting one costs nothing beside a real frame.
STILL_SIZE_PX = (512, 256)
CLOSE_TIMEOUT_S = 10.0


class GpuMark:
    # A query, because this machine's driver answers glFenceSync with
    # GL_INVALID_ENUM for the one condition the spec allows.

    def __init__(self) -> None:
        self._query = int(GL.glGenQueries(1)[0])

    def set(self) -> None:
        GL.glBeginQuery(GL.GL_TIME_ELAPSED, self._query)
        GL.glEndQuery(GL.GL_TIME_ELAPSED)

    @property
    def reached(self) -> bool:
        return bool(GL.glGetQueryObjectiv(self._query, GL.GL_QUERY_RESULT_AVAILABLE))

    def close(self) -> None:
        GL.glDeleteQueries(1, [self._query])


class VideoThread:
    def __init__(self, contexts, build_player, cap_px: int, *, name: str, perf=None) -> None:
        self._contexts = contexts
        self._cap_px = cap_px
        self._name = name
        self._perf = perf
        self._relay = FrameRelay()
        self._still = StillAsked()
        self._stop = threading.Event()
        self._built = threading.Event()
        self._fault: BaseException | None = None
        self._copy: GpuMark | None = None
        self._copy_wait = GpuWait()
        self._copying = False
        self.player = None
        self._window = contexts.open(name)
        self._thread = start_daemon_thread(
            target=self._paint_until_stopped, args=(build_player,), name=name)
        self._built.wait()
        if self._fault is not None:
            self._thread.join(timeout=CLOSE_TIMEOUT_S)
            contexts.close(self._window)
            raise self._fault

    @property
    def duration_ms(self) -> float:
        """How long the file on screen is -- 0 while there is none yet."""
        return 0.0 if self.player is None else self.player.duration_ms

    def ask_for_a_still(self) -> None:
        """Have the next painted frame left as a small copy, for :meth:`take_a_still`."""
        self._still.ask()

    def take_a_still(self):
        """The small copy asked for, once one has been painted; else None."""
        return self._still.take()

    def show_newest(self, target: RenderTarget) -> bool:
        if self._copying:
            if not self._finished_on_the_gpu(self._copy_wait, self._copy, "copy"):
                return False
            self._copying = False
            self._relay.copied()
        picture = self._relay.take()
        if picture is None:
            return False
        if self._copy is None:
            self._copy = GpuMark()
        target.ensure(picture.width, picture.height)
        target.video = picture.video
        GL.glCopyImageSubData(
            picture.texture, GL.GL_TEXTURE_2D, 0, 0, 0, 0,
            target.texture, GL.GL_TEXTURE_2D, 0, 0, 0, 0,
            picture.width, picture.height, 1,
        )
        self._copy.set()
        GL.glFlush()
        self._copying = True
        target.painted = True
        return True

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=CLOSE_TIMEOUT_S)
        if self._thread.is_alive():
            logger.error(
                "The %s video is still inside mpv after %.0fs; its context is left to the "
                "process to reap, since freeing it under a render is a crash",
                self._name, CLOSE_TIMEOUT_S)
            return
        if self._copy is not None:
            self._copy.close()
            self._copy = None
        self._contexts.close(self._window)

    def _paint_until_stopped(self, build_player) -> None:
        self._contexts.make_current(self._window)
        targets: list[RenderTarget] = []
        still: RenderTarget | None = None
        try:
            targets = [RenderTarget() for _ in range(SLOTS)]
            still = RenderTarget()
            still.ensure(*STILL_SIZE_PX)
            self.player = build_player()
        except BaseException as fault:  # noqa: BLE001 -- raised again on the frame loop's thread
            self._fault = fault
        finally:
            self._built.set()
        try:
            if self._fault is None:
                with scheduled_as_a_game():
                    self._paint(targets, still)
        except Exception:
            logger.exception("The %s video stopped painting", self._name)
        finally:
            if self.player is not None:
                self.player.close()
            for target in (*targets, still):
                if target is not None:
                    target.close()
            self._contexts.make_current(None)

    def _paint(self, targets: list[RenderTarget], still: RenderTarget | None) -> None:
        mark = GpuMark()
        wait, watch = GpuWait(), PictureWatch()
        drawn: tuple[int, int, int, int, str] | None = None
        try:
            while not self._stop.is_set():
                if drawn is not None:
                    if not self._finished_on_the_gpu(wait, mark, "picture"):
                        self._stop.wait(PAINT_POLL_S)
                        continue
                    slot, texture, width, height, video = drawn
                    self._relay.painted(
                        slot, texture=texture, width=width, height=height, video=video)
                    if still is not None and self._still.wanted:
                        self._still.leave(self._paint_a_still(still))
                drawn = self._draw_next(targets, mark)
                if drawn is not None:
                    watch.drew()
                    continue
                now = time.monotonic()
                if watch.wants_a_reading(now):
                    gone = watch.read(position_ms=self.player.position_ms, now=now)
                    if gone is not None:
                        logger.warning("The %s video has drawn nothing for %.0fs while it played on",
                                       self._name, gone)
                self._stop.wait(PAINT_POLL_S)
        finally:
            mark.close()

    def _finished_on_the_gpu(self, wait: GpuWait, mark: GpuMark, what: str) -> bool:
        verdict = wait.judge(reached=mark.reached, now=time.monotonic())
        if verdict == FLUSH:
            logger.warning("The %s video's %s has waited %.0fs for the GPU; flushing",
                           self._name, what, FLUSH_AFTER_S)
            GL.glFlush()
        elif verdict == GIVE_UP:
            logger.error("The %s video's %s never finished on the GPU in %.0fs; showing it anyway",
                         self._name, what, GIVE_UP_AFTER_S)
        return verdict not in (WAIT, FLUSH)

    def _paint_a_still(self, still: RenderTarget):
        """The frame on screen, small, as RGBA rows top-first.

        mpv draws it the way it draws every other frame -- into a framebuffer on
        this thread, whose GL context is the one its render context was made on;
        asking from any other thread is refused, not answered.
        """
        self.player.render(still.fbo, still.width, still.height)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, still.fbo)
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        pixels = GL.glReadPixels(0, 0, still.width, still.height, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        return np.frombuffer(pixels, np.uint8).reshape(still.height, still.width, 4)

    def _draw_next(self, targets: list[RenderTarget], mark: GpuMark):
        if not self.player.has_picture_to_draw:
            return None
        size = capped_size(self.player.video_dims, self._cap_px)
        if size is None:
            return None
        started = time.perf_counter()
        slot = self._relay.slot_to_paint()
        target = targets[slot]
        target.ensure(*size)
        # flip_y: mpv renders top-left-origin; the scene samples GL lower-left
        # convention (verified against a top-half-white clip).
        video = self.player.render(target.fbo, target.width, target.height, flip_y=True)
        mark.set()
        GL.glFlush()
        if self._perf is not None:
            self._perf.note("paint", (time.perf_counter() - started) * 1e3)
        if video is None:
            return None
        return slot, target.texture, target.width, target.height, video

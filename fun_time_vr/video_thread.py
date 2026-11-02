from __future__ import annotations

import logging
import threading
import time

import numpy as np
from app_support.threading_utils import start_daemon_thread
from OpenGL import GL

from .frame_relay import SLOTS, FrameRelay, StillAsked
from .painting import Painting, Showing
from .render import RenderTarget
from .scheduling import scheduled_as_a_game

logger = logging.getLogger(__name__)

PAINT_POLL_S = 0.002
# A still: enough for a fisheye circle's edge, cheap beside a real frame.
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


class Gpu:
    def mark(self) -> GpuMark:
        return GpuMark()

    def flush(self) -> None:
        GL.glFlush()

    def copy(self, frame, target: RenderTarget) -> None:
        GL.glCopyImageSubData(
            frame.texture, GL.GL_TEXTURE_2D, 0, 0, 0, 0,
            target.texture, GL.GL_TEXTURE_2D, 0, 0, 0, 0,
            frame.width, frame.height, 1,
        )

    def read_pixels(self, target: RenderTarget) -> np.ndarray:
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target.fbo)
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        pixels = GL.glReadPixels(0, 0, target.width, target.height, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        return np.frombuffer(pixels, np.uint8).reshape(target.height, target.width, 4)


class VideoThread:
    def __init__(self, contexts, build_engine, cap_px: int, *, name: str, perf=None) -> None:
        self._contexts = contexts
        self._cap_px = cap_px
        self._name = name
        self._perf = perf
        self._relay = FrameRelay()
        self._still = StillAsked()
        self._showing = Showing(self._relay, gpu=Gpu(), name=name)
        self._stop = threading.Event()
        self._built = threading.Event()
        self._fault: BaseException | None = None
        self.engine = None
        self._window = contexts.open(name)
        self._thread = start_daemon_thread(
            target=self._paint_until_stopped, args=(build_engine,), name=name)
        self._built.wait()
        if self._fault is not None:
            self._thread.join(timeout=CLOSE_TIMEOUT_S)
            contexts.close(self._window)
            raise self._fault

    @property
    def duration_ms(self) -> float:
        """How long the file on screen is -- 0 while there is none yet."""
        return 0.0 if self.engine is None else self.engine.duration_ms

    def ask_for_a_still(self) -> None:
        """Have the next painted frame left as a small copy, for :meth:`take_a_still`."""
        self._still.ask()

    def take_a_still(self):
        """The small copy asked for, once one has been painted; else None."""
        return self._still.take()

    def show_newest(self, target: RenderTarget) -> bool:
        return self._showing.show_newest(target)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=CLOSE_TIMEOUT_S)
        if self._thread.is_alive():
            logger.error(
                "The %s video is still inside mpv after %.0fs; its context is left to the "
                "process to reap, since freeing it under a render is a crash",
                self._name, CLOSE_TIMEOUT_S)
            return
        self._showing.close()
        self._contexts.close(self._window)

    def _paint_until_stopped(self, build_engine) -> None:
        self._contexts.make_current(self._window)
        targets: list[RenderTarget] = []
        still: RenderTarget | None = None
        painting: Painting | None = None
        try:
            targets = [RenderTarget() for _ in range(SLOTS)]
            still = RenderTarget()
            still.ensure(*STILL_SIZE_PX)
            self.engine = build_engine()
            painting = Painting(self.engine, name=self._name, cap_px=self._cap_px, gpu=Gpu(),
                                relay=self._relay, still_asked=self._still, perf=self._perf)
        except BaseException as fault:  # noqa: BLE001 -- raised again on the frame loop's thread
            self._fault = fault
        finally:
            self._built.set()
        try:
            if painting is not None:
                with scheduled_as_a_game():
                    self._paint(painting, targets, still)
        except Exception:
            logger.exception("The %s video stopped painting", self._name)
        finally:
            if painting is not None:
                painting.close()
            if self.engine is not None:
                self.engine.close()
            for target in (*targets, still):
                if target is not None:
                    target.close()
            self._contexts.make_current(None)

    def _paint(self, painting: Painting, targets: list[RenderTarget], still: RenderTarget) -> None:
        while not self._stop.is_set():
            if painting.paint(targets, still):
                time.sleep(PAINT_POLL_S)

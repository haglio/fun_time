from __future__ import annotations

import logging
import threading
import time

from app_support.threading_utils import start_daemon_thread
from OpenGL import GL

from .frame_relay import SLOTS, FrameRelay, capped_size
from .render import RenderTarget
from .scheduling import scheduled_as_a_game

logger = logging.getLogger(__name__)

PAINT_POLL_S = 0.002
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
        self._stop = threading.Event()
        self._built = threading.Event()
        self._fault: BaseException | None = None
        self._copy: GpuMark | None = None
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

    def show_newest(self, target: RenderTarget) -> bool:
        if self._copying:
            if not self._copy.reached:
                return False
            self._copying = False
            self._relay.copied()
        picture = self._relay.take()
        if picture is None:
            return False
        if self._copy is None:
            self._copy = GpuMark()
        target.ensure(picture.width, picture.height)
        GL.glCopyImageSubData(
            picture.texture, GL.GL_TEXTURE_2D, 0, 0, 0, 0,
            target.texture, GL.GL_TEXTURE_2D, 0, 0, 0, 0,
            picture.width, picture.height, 1,
        )
        self._copy.set()
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
        try:
            targets = [RenderTarget() for _ in range(SLOTS)]
            self.player = build_player()
        except BaseException as fault:  # noqa: BLE001 -- raised again on the frame loop's thread
            self._fault = fault
        finally:
            self._built.set()
        try:
            if self._fault is None:
                with scheduled_as_a_game():
                    self._paint(targets)
        except Exception:
            logger.exception("The %s video stopped painting", self._name)
        finally:
            if self.player is not None:
                self.player.close()
            for target in targets:
                target.close()
            self._contexts.make_current(None)

    def _paint(self, targets: list[RenderTarget]) -> None:
        mark = GpuMark()
        drawn: tuple[int, int, int, int] | None = None
        try:
            while not self._stop.is_set():
                if drawn is not None:
                    if not mark.reached:
                        self._stop.wait(PAINT_POLL_S)
                        continue
                    slot, texture, width, height = drawn
                    self._relay.painted(slot, texture=texture, width=width, height=height)
                drawn = self._draw_next(targets, mark)
                if drawn is None:
                    self._stop.wait(PAINT_POLL_S)
        finally:
            mark.close()

    def _draw_next(self, targets: list[RenderTarget], mark: GpuMark):
        fresh = self.player.has_new_frame
        size = capped_size(self.player.video_dims, self._cap_px)
        if not fresh or size is None:
            return None
        started = time.perf_counter()
        slot = self._relay.slot_to_paint()
        target = targets[slot]
        target.ensure(*size)
        # flip_y: mpv renders top-left-origin; the scene samples GL lower-left
        # convention (verified against a top-half-white clip).
        self.player.render(target.fbo, target.width, target.height, flip_y=True)
        mark.set()
        if self._perf is not None:
            self._perf.note("paint", (time.perf_counter() - started) * 1e3)
        return slot, target.texture, target.width, target.height

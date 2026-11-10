from __future__ import annotations

import logging
import threading
import time

from app_support.threading_utils import start_daemon_thread

from .frame_relay import SLOTS, FrameHandover
from .painting import Painting, Showing
from .render import RenderTarget
from .scheduling import scheduled_as_a_game

logger = logging.getLogger(__name__)

PAINT_POLL_S = 0.002
STILL_ENOUGH_FOR_A_FISHEYES_EDGE_PX = (512, 256)
CLOSE_TIMEOUT_S = 10.0


class VideoThread:
    close_timeout_s = CLOSE_TIMEOUT_S

    def __init__(self, contexts, build_engine, cap_px: int, *, name: str, perf=None) -> None:
        self._contexts = contexts
        self._name = name
        self._perf = perf
        self._frames = FrameHandover(name, cap_px)
        self._showing = Showing(self._frames, gpu=contexts.gpu())
        self._new_gpu = contexts.gpu
        self._new_target = contexts.render_target
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
        return 0.0 if self.engine is None else self.engine.duration_ms

    def ask_for_a_still(self) -> None:
        self._frames.still.ask()

    def take_a_still(self):
        return self._frames.still.take()

    def show_newest(self, target: RenderTarget) -> bool:
        return self._showing.show_newest(target)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.close_timeout_s)
        if self._thread.is_alive():
            logger.error(
                "The %s video is still inside mpv after %.0fs; its context is left to the "
                "process to reap, since freeing it under a render is a crash",
                self._name, self.close_timeout_s)
            return
        self._showing.close()
        self._contexts.close(self._window)

    def _paint_until_stopped(self, build_engine) -> None:
        self._contexts.make_current(self._window)
        targets: list[RenderTarget] = []
        still: RenderTarget | None = None
        painting: Painting | None = None
        try:
            targets = [self._new_target() for _ in range(SLOTS)]
            still = self._new_target()
            still.ensure(*STILL_ENOUGH_FOR_A_FISHEYES_EDGE_PX)
            self.engine = build_engine()
            painting = Painting(self.engine, self._frames, gpu=self._new_gpu(), perf=self._perf)
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

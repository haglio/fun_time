from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from funestra.frame_record import FrameRecord


class FramePerf:
    def __init__(
        self,
        *,
        logger: logging.Logger,
        interval_s: float = 5.0,
        clock=time.monotonic,
    ) -> None:
        self._logger = logger
        self._interval_s = interval_s
        self._clock = clock
        self._lock = threading.Lock()
        self._samples: dict[str, list[float]] = {}
        self._frames = 0
        self._window_started = clock()

    def note(self, stage: str, ms: float) -> None:
        with self._lock:
            self._samples.setdefault(stage, []).append(ms)

    def frame_done(self) -> None:
        with self._lock:
            self._frames += 1

    def flush_off_the_render_thread(self) -> None:
        now = self._clock()
        with self._lock:
            elapsed = now - self._window_started
            if elapsed < self._interval_s:
                return
            frames, samples = self._frames, self._samples
            self._frames, self._samples = 0, {}
            self._window_started = now
        if not frames and not samples:
            return
        fps = frames / elapsed if elapsed > 0 else 0.0
        stages = "  ".join(
            f"{stage}={_percentile(values, 0.5):.1f}/{_percentile(values, 0.95):.1f}"
            f"/{max(values):.1f}"
            for stage, values in sorted(samples.items())
        )
        self._logger.info("PERF %.1fs fps=%.1f  %s  (ms p50/p95/max)", elapsed, fps, stages)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


class UploadedBytes:
    def __init__(self) -> None:
        self._bytes = 0

    def note(self, count: int) -> None:
        self._bytes += count

    def take(self) -> int:
        taken, self._bytes = self._bytes, 0
        return taken


UPLOADED = UploadedBytes()


class FrameClock:
    def __init__(self, perf: FramePerf, *, uploaded: UploadedBytes,
                 stopwatch: Callable[[], float] = time.perf_counter) -> None:
        self._perf = perf
        self._uploaded = uploaded
        self._stopwatch = stopwatch
        self._record: FrameRecord | None = None
        self._at = 0.0

    def waiting(self) -> None:
        self._at = self._stopwatch()

    def waited(self, display_period_ms: float | None) -> None:
        self._note("wait")
        if self._record is None and display_period_ms:
            self._record = FrameRecord(display_period_ms, every_step=True, clock=self._stopwatch)
        self._uploaded.take()
        if self._record is not None:
            self._record.begin()

    def lap(self, stage: str) -> None:
        self._note(stage)
        if self._record is not None:
            self._record.lap(stage)

    def ended(self) -> None:
        self._perf.frame_done()
        if self._record is not None:
            self._record.end(uploaded_bytes=self._uploaded.take())

    def _note(self, stage: str) -> None:
        now = self._stopwatch()
        self._perf.note(stage, (now - self._at) * 1e3)
        self._at = now

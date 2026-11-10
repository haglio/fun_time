from __future__ import annotations

import logging
import threading
import time


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

"""A rolling wall-clock summary of the VR frame loop's stages.

The loop must hand the compositor a frame every refresh period; a stage that
spikes past the budget is a dropped frame the user feels as judder, and
nothing on a desktop monitor shows it.  So the loop notes each stage's
duration and this logs one percentile line every few seconds — cheap enough
to stay on permanently, so any headset session's ``vr_player.log`` says where
its time went.

Thread-aware: the file-channel pump reports from its worker thread while the
frame loop reports from the render thread, so samples are taken under a lock.
"""
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
        """Record one stage duration in milliseconds (any thread)."""
        with self._lock:
            self._samples.setdefault(stage, []).append(ms)

    def frame_done(self) -> None:
        """Count a completed frame — cheap enough for the render thread."""
        with self._lock:
            self._frames += 1

    def maybe_flush(self) -> None:
        """Log and reset once the window elapses.

        Called from the file-channel worker, never the render thread: the log
        lands in the state directory, and a write that stalls there is the
        exact class of hitch the two-thread split exists to keep off frames.
        """
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

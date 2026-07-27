from __future__ import annotations

import logging

from fun_time_vr.perf import FramePerf


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _perf_with_capture(caplog):
    logger = logging.getLogger("test_vr_perf")
    caplog.set_level(logging.INFO, logger=logger.name)
    clock = _Clock()
    return FramePerf(logger=logger, interval_s=5.0, clock=clock), clock


class TestFramePerf:
    def test_quiet_until_the_window_elapses(self, caplog):
        perf, clock = _perf_with_capture(caplog)
        for _ in range(10):
            perf.note("wait", 8.0)
            clock.now += 0.4
            perf.frame_done()
            perf.maybe_flush()
        assert not caplog.records

    def test_one_line_per_window_with_fps_and_percentiles(self, caplog):
        # 51 frames, not 50: accumulated 0.1s steps land a hair under the
        # 5s window by floating-point, and the window must elapse to flush.
        perf, clock = _perf_with_capture(caplog)
        for _ in range(51):
            perf.note("wait", 8.0)
            perf.note("mpv", 1.0)
            clock.now += 0.1
            perf.frame_done()
            perf.maybe_flush()
        assert len(caplog.records) == 1
        line = caplog.records[0].getMessage()
        assert "fps=10.0" in line
        assert "mpv=1.0/1.0/1.0" in line
        assert "wait=8.0/8.0/8.0" in line

    def test_the_window_resets_after_logging(self, caplog):
        perf, clock = _perf_with_capture(caplog)
        for _ in range(120):
            perf.note("wait", 5.0)
            clock.now += 0.1
            perf.frame_done()
            perf.maybe_flush()
        assert len(caplog.records) == 2

    def test_spikes_surface_in_the_max_column(self, caplog):
        perf, clock = _perf_with_capture(caplog)
        for index in range(51):
            perf.note("pump", 250.0 if index == 25 else 1.0)
            clock.now += 0.1
            perf.frame_done()
            perf.maybe_flush()
        line = caplog.records[0].getMessage()
        assert "pump=1.0/1.0/250.0" in line

    def test_an_idle_window_logs_nothing(self, caplog):
        # The flush runs on the pump worker even while the headset warms up
        # and no frames flow; an all-zero line every window would be noise.
        perf, clock = _perf_with_capture(caplog)
        for _ in range(4):
            clock.now += 2.0
            perf.maybe_flush()
        assert not caplog.records

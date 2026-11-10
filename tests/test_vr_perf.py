from __future__ import annotations

import logging

from fun_time_vr.perf import FramePerf


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


_WINDOW_S = 5.0
_FRAME_S = 0.1
_ONE_MORE_SINCE_SUMMED_FLOAT_STEPS_FALL_A_HAIR_SHORT = 1
_FRAMES_THAT_FILL_A_WINDOW = (
    round(_WINDOW_S / _FRAME_S) + _ONE_MORE_SINCE_SUMMED_FLOAT_STEPS_FALL_A_HAIR_SHORT)


def _perf_with_capture(caplog):
    logger = logging.getLogger("test_vr_perf")
    caplog.set_level(logging.INFO, logger=logger.name)
    clock = _Clock()
    return FramePerf(logger=logger, interval_s=_WINDOW_S, clock=clock), clock


class TestFramePerf:
    def test_quiet_until_the_window_elapses(self, caplog):
        perf, clock = _perf_with_capture(caplog)
        for _ in range(10):
            perf.note("wait", 8.0)
            clock.now += 0.4
            perf.frame_done()
            perf.flush_off_the_render_thread()
        assert not caplog.records

    def test_one_line_per_window_with_fps_and_percentiles(self, caplog):
        perf, clock = _perf_with_capture(caplog)
        for _ in range(_FRAMES_THAT_FILL_A_WINDOW):
            perf.note("wait", 8.0)
            perf.note("mpv", 1.0)
            clock.now += _FRAME_S
            perf.frame_done()
            perf.flush_off_the_render_thread()
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
            perf.flush_off_the_render_thread()
        assert len(caplog.records) == 2

    def test_spikes_surface_in_the_max_column(self, caplog):
        perf, clock = _perf_with_capture(caplog)
        for index in range(_FRAMES_THAT_FILL_A_WINDOW):
            perf.note("pump", 250.0 if index == 25 else 1.0)
            clock.now += _FRAME_S
            perf.frame_done()
            perf.flush_off_the_render_thread()
        line = caplog.records[0].getMessage()
        assert "pump=1.0/1.0/250.0" in line

    def test_an_idle_window_logs_nothing(self, caplog):
        perf, clock = _perf_with_capture(caplog)
        for _ in range(4):
            clock.now += 2.0
            perf.flush_off_the_render_thread()
        assert not caplog.records

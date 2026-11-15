from __future__ import annotations

import logging

import pytest

from fun_time.startup_steps import timed_step


class _Clock:
    def __init__(self, *readings: float) -> None:
        self._readings = list(readings)

    def __call__(self) -> float:
        return self._readings.pop(0)


def test_a_step_logs_what_it_was_and_how_long_it_took(caplog):
    with caplog.at_level(logging.INFO), timed_step(
            logging.getLogger("a startup"), "checking the video engine", clock=_Clock(10.0, 22.34)):
        pass

    assert caplog.messages == ["Startup step: checking the video engine took 12.3s"]


def test_a_step_that_fails_still_says_how_long_it_ran(caplog):
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError), timed_step(
            logging.getLogger("a startup"), "launching the dashboard", clock=_Clock(1.0, 4.0)):
        raise RuntimeError("the dashboard would not start")

    assert caplog.messages == ["Startup step: launching the dashboard took 3.0s"]

"""Unit tests: an integration run keeps its own time, and its tests time out on it."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
import pytest_timeout

from tests.integration import conftest as integration_conftest
from tests.integration import run_clock
from tests.integration.run_clock import RunClock, keep_time_by


class _Frozen:
    def __init__(self) -> None:
        self.at = 0.0

    def seconds(self) -> float:
        return self.at


@pytest.fixture
def frozen():
    clock = _Frozen()
    keep_time_by(clock)
    yield clock
    keep_time_by(run_clock.WALL)


def _settings(timeout: float) -> pytest_timeout.Settings:
    return pytest_timeout.Settings(timeout=timeout, method="thread", func_only=False,
                                   disable_debugger_detection=False)


def test_a_test_times_out_on_the_seconds_the_run_could_run(frozen, monkeypatch):
    timed_out = threading.Event()
    monkeypatch.setattr(integration_conftest.pytest_timeout, "timeout_timer",
                        lambda _item, _settings: timed_out.set())
    item = SimpleNamespace()

    assert integration_conftest.pytest_timeout_set_timer(item, _settings(240.0)) is True
    try:
        assert not timed_out.wait(1.5)
        frozen.at = 240.0
        assert timed_out.wait(5.0)
    finally:
        item.cancel_timeout()


def test_a_run_on_the_hidden_desktop_keeps_time_by_a_clock_of_its_own(monkeypatch):
    cleanups = []
    monkeypatch.setattr(integration_conftest, "on_hidden_desktop", lambda: True)
    integration_conftest.pytest_configure(SimpleNamespace(add_cleanup=cleanups.append))
    try:
        assert isinstance(run_clock._clock, RunClock)
    finally:
        for cleanup in cleanups:
            cleanup()

    assert run_clock._clock is run_clock.WALL


def test_a_run_says_how_much_of_its_time_it_could_run(monkeypatch, capsys):
    cleanups = []
    monkeypatch.setattr(integration_conftest, "on_hidden_desktop", lambda: True)
    integration_conftest.pytest_configure(SimpleNamespace(add_cleanup=cleanups.append))

    for cleanup in reversed(cleanups):
        cleanup()

    assert "[integration] the run could run for" in capsys.readouterr().err


def test_a_run_off_the_hidden_desktop_keeps_the_walls_time(monkeypatch):
    monkeypatch.setattr(integration_conftest, "on_hidden_desktop", lambda: False)

    integration_conftest.pytest_configure(SimpleNamespace(add_cleanup=lambda _cleanup: None))

    assert run_clock._clock is run_clock.WALL

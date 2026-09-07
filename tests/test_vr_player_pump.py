"""The file-channel worker surviving one unit's fault.

Every unit was pumped inside one unguarded ``for``, so a single raise ended the
worker thread and every file channel with it: no command drained, no paused flag
read, no status written, and -- because the main role's OSR2 tick runs there --
the device stopped where it stood.  The frame loop knew nothing of it and kept
drawing, so the session looked alive with nothing in it answering, which is what
an OSR2 that "crashed at some point" and left no trace looks like.

These pin the guard: the loop survives, the other units still pump, the fault is
logged with its traceback the first time and counted after, and a unit that comes
right closes its run out.
"""
from __future__ import annotations

import logging
import threading

import pytest

from fun_time_vr.perf import FramePerf
from fun_time_vr.player import _pump_channels


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def recorded_player_log():
    """The player module's own logger, which is what the guard reports through."""
    logger = logging.getLogger("fun_time_vr.player")
    handler = _Recorder()
    before, logger.propagate = logger.propagate, False
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)
    logger.propagate = before


class _Unit:
    """One pumped unit, standing in for a video/panel/keeper unit."""

    def __init__(self, raises=None) -> None:
        self.turns = 0
        self._raises = raises

    def pump(self, _stop, _now) -> None:
        self.turns += 1
        fault = self._raises(self.turns) if self._raises else None
        if fault is not None:
            raise fault


class _Stopper:
    """Ends the worker after *turns*, so the loop is bounded."""

    def __init__(self, stop: threading.Event, turns: int) -> None:
        self._stop = stop
        self._turns = turns
        self.turns = 0

    def pump(self, _stop, _now) -> None:
        self.turns += 1
        if self.turns >= self._turns:
            self._stop.set()


def _run(units, *, turns):
    stop = threading.Event()
    _pump_channels([*units, _Stopper(stop, turns)], stop,
                   FramePerf(logger=logging.getLogger("fun_time_vr.player")))


class TestOneUnitsFaultIsNotTheOthers:
    def test_the_worker_survives_and_the_rest_keep_pumping(self, recorded_player_log):
        """The OSR2 is driven from this thread; a satellite's file channel
        tripping over a half-written playlist must not be what stops it."""
        broken = _Unit(raises=lambda _turn: RuntimeError("channel is wedged"))
        healthy = _Unit()

        _run([broken, healthy], turns=4)

        assert broken.turns == 4, "the broken unit is tried again every turn"
        assert healthy.turns == 4, "and the one after it never missed a turn"
        assert [r.levelno for r in recorded_player_log.records] == [logging.ERROR]

    def test_repeats_are_counted_rather_than_traced_again(self, recorded_player_log):
        _run([_Unit(raises=lambda _turn: OSError(13, "Access is denied"))], turns=6)

        traced = [r for r in recorded_player_log.records if r.exc_info is not None]
        assert len(traced) == 1, "six identical faults, one traceback"

    def test_a_different_fault_gets_its_own_traceback(self, recorded_player_log):
        def two_kinds(turn):
            return OSError("gone") if turn <= 2 else ValueError("different")

        _run([_Unit(raises=two_kinds)], turns=5)

        traced = [r for r in recorded_player_log.records if r.exc_info is not None]
        assert len(traced) == 2

    def test_a_unit_that_comes_right_closes_its_run_out(self, recorded_player_log):
        def only_at_first(turn):
            return OSError("gone") if turn <= 3 else None

        _run([_Unit(raises=only_at_first)], turns=5)

        messages = [r.getMessage() for r in recorded_player_log.records]
        assert messages[0] == "_Unit.pump failed"
        assert messages[-1] == "...and 2 more _Unit.pump failures like it: gone"

    def test_the_fault_says_which_unit_it_was(self, recorded_player_log):
        class _Panel(_Unit):
            pass

        _run([_Panel(raises=lambda _turn: OSError("gone"))], turns=2)

        assert recorded_player_log.records[0].getMessage() == "_Panel.pump failed"

    def test_two_units_of_one_class_keep_their_own_runs(self, recorded_player_log):
        """The two satellites are one class: one coming right must not close out
        the other's run, nor absorb its fault as a repeat."""
        class _Side(_Unit):
            def __init__(self, side, raises=None):
                super().__init__(raises)
                self.side = side

        broken = _Side("portrait", raises=lambda _turn: OSError("gone"))
        healthy = _Side("landscape")

        _run([broken, healthy], turns=4)

        traced = [r for r in recorded_player_log.records if r.exc_info is not None]
        assert len(traced) == 1
        assert traced[0].getMessage() == "_Side[portrait].pump failed"

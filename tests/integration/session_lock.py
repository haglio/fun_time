from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator
from enum import Enum, auto
from time import sleep
from typing import NamedTuple

from app_support.machine_line import (
    THE_NAMESPACE_EVERY_LOGIN_SESSION_SHARES,
    Lane,
    SingleInstanceLock,
    held_elsewhere,
)

INTEGRATION_LOCK_NAME = f"{THE_NAMESPACE_EVERY_LOGIN_SESSION_SHARES}fun_time_integration_run"

FULL_RUN_GIVES_WAY_S = 20 * 60


class Place(Enum):
    AHEAD_OF_FULL_RUNS = auto()
    AFTER_THE_FULL_RUN_GOING_NEXT = auto()
    BEHIND_OTHER_FULL_RUNS = auto()
    GIVING_WAY_TO_SHORT_RUNS = auto()
    GOING_NEXT = auto()


class Waiting(NamedTuple):
    seconds: float
    place: Place


@contextlib.contextmanager
def hold_integration_lock(
    *,
    name: str = INTEGRATION_LOCK_NAME,
    short: bool = False,
    notify_every_s: float = 2.0,
    full_run_gives_way_s: float = FULL_RUN_GIVES_WAY_S,
    notify: Callable[[Waiting], None] | None = None,
) -> Iterator[SingleInstanceLock]:
    line = _Line(name)
    try:
        line.join(short=short)
        if short:
            with _saying_it_waits(notify, notify_every_s, _ShortRunsPlace(name)):
                line.wait_as_a_short_run()
        else:
            patience = _Patience(name, full_run_gives_way_s, notify_every_s)
            with _saying_it_waits(notify, notify_every_s, patience):
                line.wait_as_a_full_run(patience)
        yield line.run
    finally:
        line.close()


_A_FULL_RUN_STEPS_ASIDE_S = 0.05


class _Line:
    def __init__(self, name: str) -> None:
        self.run = SingleInstanceLock(name)
        self._short_lane = Lane(f"{name}_short_lane")
        self._full_lane = Lane(f"{name}_full_lane")
        self._full_goes_next = SingleInstanceLock(f"{name}_full_goes_next")
        self._short_running = SingleInstanceLock(f"{name}_short_running")

    def join(self, *, short: bool) -> None:
        (self._short_lane if short else self._full_lane).join()

    def wait_as_a_short_run(self) -> None:
        self._short_lane.wait_for_the_front()
        while True:
            if held_elsewhere(self._full_goes_next):
                self._full_goes_next.acquire()
                self._full_goes_next.release()
            self.run.acquire()
            if not held_elsewhere(self._full_goes_next):
                break
            self.run.release()
        self._short_running.acquire()
        self._short_lane.leave()

    def wait_as_a_full_run(self, patience: _Patience) -> None:
        self._full_lane.wait_for_the_front()
        patience.heads_the_full_runs()
        while True:
            self.run.acquire()
            if patience.spent() or not held_elsewhere(self._short_lane.front):
                break
            self.run.release()
            sleep(_A_FULL_RUN_STEPS_ASIDE_S)
        self._full_lane.leave()

    def close(self) -> None:
        self._short_running.close()
        self._short_lane.leave()
        self._full_lane.leave()
        self._full_goes_next.close()
        self.run.close()


class _Patience:
    def __init__(self, name: str, gives_way_s: float, looks_every_s: float) -> None:
        self._short_running = SingleInstanceLock(f"{name}_short_running")
        self._goes_next = SingleInstanceLock(f"{name}_full_goes_next")
        self._gives_way_s = gives_way_s
        self._looks_every_s = looks_every_s
        self._given_way = 0.0
        self._going_next = False
        self._at_the_head = threading.Event()

    def heads_the_full_runs(self) -> None:
        self._at_the_head.set()

    def spent(self) -> bool:
        return self._given_way >= self._gives_way_s

    def waited(self) -> Place:
        if not self._at_the_head.is_set():
            return Place.BEHIND_OTHER_FULL_RUNS
        if held_elsewhere(self._short_running):
            self._given_way += self._looks_every_s
        if self.spent() and not self._going_next:
            self._going_next = self._goes_next.acquire(timeout=0)
        return Place.GOING_NEXT if self._going_next else Place.GIVING_WAY_TO_SHORT_RUNS

    def close(self) -> None:
        self._goes_next.close()
        self._short_running.close()


class _ShortRunsPlace:
    def __init__(self, name: str) -> None:
        self._full_goes_next = SingleInstanceLock(f"{name}_full_goes_next")

    def waited(self) -> Place:
        if held_elsewhere(self._full_goes_next):
            return Place.AFTER_THE_FULL_RUN_GOING_NEXT
        return Place.AHEAD_OF_FULL_RUNS

    def close(self) -> None:
        self._full_goes_next.close()


@contextlib.contextmanager
def _saying_it_waits(notify: Callable[[Waiting], None] | None, every_s: float,
                     place: _Patience | _ShortRunsPlace) -> Iterator[None]:
    stop = threading.Event()

    def say() -> None:
        waited = 0.0
        try:
            while not stop.wait(every_s):
                waited += every_s
                where = place.waited()
                if notify is not None:
                    notify(Waiting(waited, where))
        finally:
            place.close()

    sayer = threading.Thread(target=say, name="integration-queue-notice", daemon=True)
    sayer.start()
    try:
        yield
    finally:
        stop.set()
        sayer.join()

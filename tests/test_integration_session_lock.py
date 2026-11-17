from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

import pytest
from app_support.win32_loader import load_dll

from tests.child_reports import a_child_of_this_venv, text_written_to
from tests.integration import session_lock
from tests.integration.session_lock import (
    Place,
    SingleInstanceLock,
    hold_integration_lock,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the named-mutex lock is Windows-only"
)


def _unique_name() -> str:
    return rf"Global\fun_time_test_lock_{uuid.uuid4().hex}"


_LOOKS_EVERY_S = 0.05


def _seen(look: Callable[[], object], *, while_running: threading.Thread) -> bool:
    while not look():
        if not while_running.is_alive():
            return bool(look())
        time.sleep(_LOOKS_EVERY_S)
    return True


_HOLD_UNTIL_KILLED = """
import pathlib, sys, time
sys.path.insert(0, {root!r})
from tests.integration.session_lock import SingleInstanceLock
lock = SingleInstanceLock({name!r})
lock.acquire()
pathlib.Path({sentinel!r}).write_text("held")
time.sleep(120)
"""


def test_a_run_waiting_its_turn_gets_the_lock_when_the_run_holding_it_dies(tmp_path):
    name = _unique_name()
    sentinel = tmp_path / "held.flag"
    root = str(Path(__file__).resolve().parents[1])
    holder_proc = a_child_of_this_venv(
        _HOLD_UNTIL_KILLED.format(root=root, name=name, sentinel=str(sentinel)), cwd=root)
    entered = threading.Event()
    noticed = threading.Event()

    def run() -> None:
        with hold_integration_lock(name=name, notify_every_s=0.05,
                                   notify=lambda _waited: noticed.set()):
            entered.set()

    waiter = threading.Thread(target=run)
    try:
        text_written_to(sentinel, by=holder_proc)
        waiter.start()
        assert _seen(noticed.is_set, while_running=waiter)

        holder_proc.kill()
        holder_proc.wait(timeout=10)

        assert _seen(entered.is_set, while_running=waiter)
    finally:
        if holder_proc.poll() is None:
            holder_proc.kill()
            holder_proc.wait(timeout=10)
        if waiter.ident is not None:
            waiter.join()


def test_a_run_is_never_starved_by_runs_that_joined_the_line_after_it():
    name = _unique_name()
    blocker = _held_by_the_test(name)
    order: list[str] = []
    runs = _join_in_turn(name, order, 6)

    blocker.close()
    for run in runs:
        run.join()

    assert order == [str(number) for number in range(6)]


def test_hold_integration_lock_queues_until_free_then_releases():
    name = _unique_name()
    blocker = SingleInstanceLock(name)
    assert blocker.acquire(timeout=1.0) is True

    notifications: list[float] = []
    entered = threading.Event()

    def contender() -> None:
        with hold_integration_lock(name=name, notify_every_s=0.1, notify=notifications.append):
            entered.set()

    def the_contender_says_it_is_waiting() -> bool:
        return bool(notifications)

    thread = threading.Thread(target=contender)
    thread.start()
    assert _seen(the_contender_says_it_is_waiting, while_running=thread)
    assert not entered.is_set()

    blocker.release()
    blocker.close()
    thread.join()
    assert entered.is_set()

    after = SingleInstanceLock(name)
    try:
        assert after.acquire(timeout=1.0) is True
    finally:
        after.close()


def _start_run(name: str, order: list[str], label: str, *, once=lambda _state: True,
               **kind) -> tuple[threading.Thread, threading.Event]:
    waiting = threading.Event()

    def tell(state) -> None:
        if once(state):
            waiting.set()

    def run() -> None:
        with hold_integration_lock(name=name, notify_every_s=0.05, notify=tell, **kind):
            order.append(label)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, waiting


def _join_in_turn(name: str, order: list[str], runs: int, **kind) -> list[threading.Thread]:
    started = []
    for number in range(runs):
        run, waiting = _start_run(name, order, str(number), **kind)
        assert _seen(waiting.is_set, while_running=run), f"run {number} never started waiting"
        started.append(run)
    return started


def _held_by_the_test(name: str) -> SingleInstanceLock:
    blocker = SingleInstanceLock(name)
    assert blocker.acquire(timeout=1.0)
    return blocker


def _a_short_run_holding_the_line(name: str) -> tuple[threading.Thread, threading.Event]:
    holding, may_leave = threading.Event(), threading.Event()

    def hold() -> None:
        with hold_integration_lock(name=name, short=True, notify_every_s=0.05):
            holding.set()
            may_leave.wait(timeout=10)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert _seen(holding.is_set, while_running=holder)
    return holder, may_leave


def test_a_full_run_lets_a_short_run_that_came_after_it_go_first():
    name = _unique_name()
    blocker = _held_by_the_test(name)
    order: list[str] = []
    full, full_giving_way = _start_run(
        name, order, "full", once=lambda state: state.place is Place.GIVING_WAY_TO_SHORT_RUNS)
    assert _seen(full_giving_way.is_set, while_running=full)
    short, short_ahead = _start_run(name, order, "short", short=True,
                                    once=lambda state: state.place is Place.AHEAD_OF_FULL_RUNS)
    assert _seen(short_ahead.is_set, while_running=short)

    blocker.close()
    for run in (full, short):
        run.join()

    assert order == ["short", "full"]


def test_short_runs_get_the_lock_in_the_order_they_started_waiting():
    name = _unique_name()
    blocker = _held_by_the_test(name)
    order: list[str] = []
    runs = _join_in_turn(name, order, 4, short=True)

    blocker.close()
    for run in runs:
        run.join()

    assert order == [str(number) for number in range(4)]


_kernel32 = load_dll("kernel32", use_last_error=True)
_kernel32.OpenThread.restype = wintypes.HANDLE
_kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.SuspendThread.argtypes = [wintypes.HANDLE]
_kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_THREAD_SUSPEND_RESUME = 0x0002


def _suspend_a_moment(run: threading.Thread) -> None:
    thread = _kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, run.native_id)
    assert thread
    try:
        _kernel32.SuspendThread(thread)
        time.sleep(0.05)
        _kernel32.ResumeThread(thread)
    finally:
        _kernel32.CloseHandle(thread)


@pytest.mark.parametrize("short", [False, True], ids=["full runs", "short runs"])
def test_a_run_keeps_its_place_in_line_while_something_suspends_it(short):
    name = _unique_name()
    blocker = _held_by_the_test(name)
    order: list[str] = []
    runs = _join_in_turn(name, order, 4, short=short)

    _suspend_a_moment(runs[1])

    blocker.close()
    for run in runs:
        run.join()

    assert order == [str(number) for number in range(4)]


@pytest.mark.parametrize("short", [False, True], ids=["full runs", "short runs"])
def test_a_run_says_it_is_waiting_only_once_it_has_its_place_in_line(monkeypatch, short):
    joining = session_lock.Lane.join
    slowed = threading.Event()

    def slow_to_join_the_first_time(lane) -> None:
        if not slowed.is_set():
            slowed.set()
            time.sleep(0.3)
        joining(lane)

    monkeypatch.setattr(session_lock.Lane, "join", slow_to_join_the_first_time)
    name = _unique_name()
    blocker = _held_by_the_test(name)
    order: list[str] = []
    first, first_waiting = _start_run(name, order, "first", short=short)
    assert _seen(first_waiting.is_set, while_running=first)
    second, second_waiting = _start_run(name, order, "second", short=short)
    assert _seen(second_waiting.is_set, while_running=second)

    blocker.close()
    for run in (first, second):
        run.join()

    assert order == ["first", "second"]


def test_a_full_run_goes_first_once_short_runs_have_held_the_lock_as_long_as_it_gives_way():
    name = _unique_name()
    holder, first_short_may_leave = _a_short_run_holding_the_line(name)
    order: list[str] = []
    full, full_goes_next = _start_run(name, order, "full", full_run_gives_way_s=0.2,
                                      once=lambda state: state.place is Place.GOING_NEXT)
    assert _seen(full_goes_next.is_set, while_running=full)
    short, short_waiting = _start_run(name, order, "second short", short=True)
    assert _seen(short_waiting.is_set, while_running=short)

    first_short_may_leave.set()
    for run in (holder, full, short):
        run.join()

    assert order == ["full", "second short"]


def test_a_short_run_says_so_when_a_full_run_goes_before_it():
    name = _unique_name()
    holder, first_short_may_leave = _a_short_run_holding_the_line(name)
    order: list[str] = []
    full, full_goes_next = _start_run(name, order, "full", full_run_gives_way_s=0.2,
                                      once=lambda state: state.place is Place.GOING_NEXT)
    assert _seen(full_goes_next.is_set, while_running=full)
    short, short_behind_it = _start_run(
        name, order, "second short", short=True,
        once=lambda state: state.place is Place.AFTER_THE_FULL_RUN_GOING_NEXT)

    try:
        assert _seen(short_behind_it.is_set, while_running=short)
    finally:
        first_short_may_leave.set()
        for run in (holder, full, short):
            run.join()


def test_a_full_run_counts_toward_giving_way_only_the_time_short_runs_hold_the_lock():
    name = _unique_name()
    another_full_run = _held_by_the_test(name)
    order: list[str] = []
    full, full_waited_long = _start_run(name, order, "full", full_run_gives_way_s=0.2,
                                        once=lambda state: state.seconds > 1.0)
    assert _seen(full_waited_long.is_set, while_running=full)
    short, short_waiting = _start_run(name, order, "short", short=True)
    assert _seen(short_waiting.is_set, while_running=short)

    another_full_run.close()
    for run in (full, short):
        run.join()

    assert order == ["short", "full"]


def test_a_full_run_counts_toward_giving_way_only_once_it_is_next_in_line():
    name = _unique_name()
    holder, first_short_may_leave = _a_short_run_holding_the_line(name)
    order: list[str] = []
    first_full, first_full_goes_next = _start_run(name, order, "first full", full_run_gives_way_s=0.2,
                                                  once=lambda state: state.place is Place.GOING_NEXT)
    assert _seen(first_full_goes_next.is_set, while_running=first_full)
    second_full, second_full_waited = _start_run(name, order, "second full", full_run_gives_way_s=0.2,
                                                 once=lambda state: state.seconds > 0.5)
    assert _seen(second_full_waited.is_set, while_running=second_full)
    short, short_waiting = _start_run(name, order, "second short", short=True)
    assert _seen(short_waiting.is_set, while_running=short)

    first_short_may_leave.set()
    for run in (holder, first_full, second_full, short):
        run.join()

    assert order == ["first full", "second short", "second full"]


def test_only_the_full_run_next_in_line_makes_short_runs_wait_for_it():
    name = _unique_name()
    blocker = _held_by_the_test(name)
    order: list[str] = []
    first_full, first_full_waiting = _start_run(name, order, "first full")
    assert _seen(first_full_waiting.is_set, while_running=first_full)
    second_full, second_full_behind = _start_run(
        name, order, "second full", full_run_gives_way_s=0,
        once=lambda state: state.place is Place.BEHIND_OTHER_FULL_RUNS)
    assert _seen(second_full_behind.is_set, while_running=second_full)
    short, short_waiting = _start_run(name, order, "short", short=True)
    assert _seen(short_waiting.is_set, while_running=short)

    blocker.close()
    for run in (first_full, second_full, short):
        run.join()

    assert order == ["short", "first full", "second full"]


_WAIT_IN_LINE = """
import pathlib, sys
sys.path.insert(0, {root!r})
from tests.integration.session_lock import Place, hold_integration_lock
def waiting(state):
    if state.seconds >= 0.15 and (state.place is Place.GOING_NEXT or {short!r}):
        pathlib.Path({sentinel!r}).write_text("waiting")
with hold_integration_lock(name={name!r}, short={short!r}, notify_every_s=0.05,
                           full_run_gives_way_s=0, notify=waiting):
    pass
"""


@pytest.mark.parametrize(("dies", "waits"), [("short", "full"), ("full", "short"), ("full", "full")])
def test_a_run_that_dies_waiting_holds_no_other_run_back(tmp_path, dies, waits):
    name = _unique_name()
    blocker = _held_by_the_test(name)
    sentinel = tmp_path / "waiting.flag"
    root = str(Path(__file__).resolve().parents[1])
    code = _WAIT_IN_LINE.format(root=root, name=name, short=dies == "short", sentinel=str(sentinel))
    dying = a_child_of_this_venv(code, cwd=root, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        text_written_to(sentinel, by=dying)
    finally:
        dying.kill()
        dying.wait(timeout=10)
    order: list[str] = []
    survivor, survivor_waiting = _start_run(name, order, waits, short=waits == "short")
    assert _seen(survivor_waiting.is_set, while_running=survivor)

    blocker.close()
    survivor.join()

    assert order == [waits]

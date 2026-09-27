"""Unit tests for the machine-wide integration-run lock.

Multiple worktree agents share this repo and may launch the integration suite
concurrently.  Each run launches the players/AHK and runs a global name+age process
reap (``_reap_leftover_runtime_processes``) that would murder a sibling run's
freshly-spawned processes; the AHK bridge's ``#SingleInstance Force`` evicts a
sibling bridge.  ``SingleInstanceLock`` serializes runs machine-wide so only one
integration session's processes are ever live at a time.

These tests exercise the lock primitive itself — no player/AHK/orchestrator is
launched.  Cross-process behavior (a real second holder, and crash recovery) is
driven with a tiny Python subprocess that only acquires the lock and sleeps.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from app_support.threading_utils import wait_until

from tests.integration.session_lock import (
    SingleInstanceLock,
    hold_integration_lock,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the named-mutex lock is Windows-only"
)


def _unique_name() -> str:
    """A fresh Global\\ mutex name so concurrent test runs never collide."""
    return rf"Global\fun_time_test_lock_{uuid.uuid4().hex}"


def test_held_lock_blocks_a_second_caller():
    """While one instance holds the lock, another cannot acquire it."""
    name = _unique_name()
    holder = SingleInstanceLock(name)
    assert holder.acquire(timeout=1.0) is True
    try:
        result: dict[str, bool] = {}

        def worker() -> None:
            other = SingleInstanceLock(name)
            try:
                result["acquired"] = other.acquire(timeout=0.3)
            finally:
                other.close()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        assert result.get("acquired") is False
    finally:
        holder.close()


def test_release_lets_the_next_caller_acquire():
    """Once the holder releases, a waiting caller is granted the lock."""
    name = _unique_name()
    holder = SingleInstanceLock(name)
    assert holder.acquire(timeout=1.0) is True

    result: dict[str, bool] = {}

    def worker() -> None:
        other = SingleInstanceLock(name)
        try:
            result["acquired"] = other.acquire(timeout=3.0)
        finally:
            other.close()

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.2)  # let the worker start blocking on the held lock
    holder.release()
    thread.join(timeout=5)
    holder.close()
    assert result.get("acquired") is True


# Code for a throwaway subprocess that only takes the lock and waits to be
# killed — it never launches a player/AHK, so this stays a pure unit test of the
# lock's crash-recovery, not an integration run.
_HOLD_UNTIL_KILLED = """
import pathlib, sys, time
sys.path.insert(0, {root!r})
from tests.integration.session_lock import SingleInstanceLock
lock = SingleInstanceLock({name!r})
lock.acquire()
pathlib.Path({sentinel!r}).write_text("held")
time.sleep(120)
"""


def test_dead_holder_does_not_deadlock_the_queue(tmp_path):
    """If the holder dies without releasing, the next caller still acquires.

    This is the crash-recovery guarantee: a Windows mutex owned by a terminated
    process is *abandoned* by the OS, and the next waiter is granted ownership
    (WAIT_ABANDONED).  Without it, one crashed run would wedge the queue forever.
    """
    name = _unique_name()
    sentinel = tmp_path / "held.flag"
    root = str(Path(__file__).resolve().parents[1])
    code = _HOLD_UNTIL_KILLED.format(root=root, name=name, sentinel=str(sentinel))
    holder_proc = subprocess.Popen([sys.executable, "-c", code], cwd=root)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not sentinel.exists():
            time.sleep(0.05)
        assert sentinel.exists(), "subprocess never acquired the lock"

        contender = SingleInstanceLock(name)
        try:
            # While the (living) subprocess holds it, we cannot acquire.
            assert contender.acquire(timeout=0.3) is False
            # Kill the holder mid-hold, simulating a crashed run.
            holder_proc.kill()
            holder_proc.wait(timeout=10)
            # The abandoned mutex must now be acquirable.
            assert contender.acquire(timeout=5.0) is True
        finally:
            contender.close()
    finally:
        if holder_proc.poll() is None:
            holder_proc.kill()
            holder_proc.wait(timeout=10)


def test_a_run_waiting_its_turn_gets_the_lock_when_the_run_holding_it_dies(tmp_path):
    name = _unique_name()
    sentinel = tmp_path / "held.flag"
    root = str(Path(__file__).resolve().parents[1])
    holder_proc = subprocess.Popen(
        [sys.executable, "-c",
         _HOLD_UNTIL_KILLED.format(root=root, name=name, sentinel=str(sentinel))],
        cwd=root)
    entered = threading.Event()
    noticed = threading.Event()

    def run() -> None:
        with hold_integration_lock(name=name, notify_every_s=0.05,
                                   notify=lambda _waited: noticed.set()):
            entered.set()

    waiter = threading.Thread(target=run)
    try:
        wait_until(sentinel.exists, timeout=30.0)
        waiter.start()
        assert noticed.wait(timeout=10.0)

        holder_proc.kill()
        holder_proc.wait(timeout=10)

        assert entered.wait(timeout=10.0)
    finally:
        if holder_proc.poll() is None:
            holder_proc.kill()
            holder_proc.wait(timeout=10)
        waiter.join(timeout=10)


def test_runs_waiting_for_the_lock_get_it_in_the_order_they_started_waiting():
    """On 2026-09-26 a run waited five and a half hours while about twenty
    others that queued after it went first: each waiter asked again every two
    seconds, so whichever asked at the right moment won."""
    name = _unique_name()
    blocker = SingleInstanceLock(name)
    assert blocker.acquire(timeout=1.0) is True
    entered: list[int] = []
    waiting: list[threading.Event] = []

    def run(number: int, noticed: threading.Event) -> None:
        with hold_integration_lock(name=name, notify_every_s=0.05,
                                   notify=lambda _waited: noticed.set()):
            entered.append(number)

    threads = []
    for number in range(6):
        noticed = threading.Event()
        waiting.append(noticed)
        threads.append(threading.Thread(target=run, args=(number, noticed)))
        threads[-1].start()
        assert noticed.wait(timeout=10.0), f"run {number} never started waiting"
        time.sleep(0.1)

    blocker.release()
    blocker.close()
    for thread in threads:
        thread.join(timeout=10)

    assert entered == list(range(6))


def test_hold_integration_lock_queues_until_free_then_releases():
    """The context manager blocks (reporting the wait) until the lock frees,
    enters once it holds it, and releases on exit so the next caller proceeds."""
    name = _unique_name()
    blocker = SingleInstanceLock(name)
    assert blocker.acquire(timeout=1.0) is True

    notifications: list[float] = []
    entered = threading.Event()

    def contender() -> None:
        with hold_integration_lock(name=name, notify_every_s=0.1, notify=notifications.append):
            entered.set()

    thread = threading.Thread(target=contender)
    thread.start()
    # Wait for the SIGNAL that the contender is queuing — its notify callback —
    # not for a fixed nap.  The old 0.35s sleep made this the one test that
    # could fail for the runner's load rather than for the code.
    wait_until(lambda: bool(notifications), timeout=10.0)
    # Notified it is waiting, and still blocked by the held lock.
    assert not entered.is_set()

    blocker.release()
    blocker.close()
    assert entered.wait(timeout=5) is True
    thread.join(timeout=5)

    # The context manager released on exit: a fresh caller can now acquire.
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


def _held_by_the_test(name: str) -> SingleInstanceLock:
    blocker = SingleInstanceLock(name)
    assert blocker.acquire(timeout=1.0)
    return blocker


def test_a_full_run_lets_a_short_run_that_came_after_it_go_first():
    name = _unique_name()
    blocker = _held_by_the_test(name)
    order: list[str] = []
    full, full_waiting = _start_run(name, order, "full")
    assert full_waiting.wait(timeout=10)
    short, short_waiting = _start_run(name, order, "short", short=True)
    assert short_waiting.wait(timeout=10)

    blocker.close()
    for run in (full, short):
        run.join(timeout=10)

    assert order == ["short", "full"]


def test_short_runs_get_the_lock_in_the_order_they_started_waiting():
    name = _unique_name()
    blocker = _held_by_the_test(name)
    order: list[str] = []
    runs = []
    for number in range(4):
        run, waiting = _start_run(name, order, f"short {number}", short=True)
        assert waiting.wait(timeout=10)
        runs.append(run)

    blocker.close()
    for run in runs:
        run.join(timeout=10)

    assert order == [f"short {number}" for number in range(4)]


def test_a_full_run_goes_first_once_short_runs_have_held_the_lock_as_long_as_it_gives_way():
    name = _unique_name()
    first_short_in, first_short_may_leave = threading.Event(), threading.Event()

    def first_short() -> None:
        with hold_integration_lock(name=name, short=True, notify_every_s=0.05):
            first_short_in.set()
            first_short_may_leave.wait(timeout=10)

    holder = threading.Thread(target=first_short, daemon=True)
    holder.start()
    assert first_short_in.wait(timeout=10)
    order: list[str] = []
    full, full_goes_next = _start_run(name, order, "full", full_run_gives_way_s=0.2,
                                      once=lambda state: state.goes_next)
    assert full_goes_next.wait(timeout=10)
    short, short_waiting = _start_run(name, order, "second short", short=True)
    assert short_waiting.wait(timeout=10)

    first_short_may_leave.set()
    for run in (holder, full, short):
        run.join(timeout=10)

    assert order == ["full", "second short"]


def test_a_full_run_counts_toward_giving_way_only_the_time_short_runs_hold_the_lock():
    name = _unique_name()
    another_full_run = _held_by_the_test(name)
    order: list[str] = []
    full, full_waited_long = _start_run(name, order, "full", full_run_gives_way_s=0.2,
                                        once=lambda state: state.seconds > 1.0)
    assert full_waited_long.wait(timeout=10)
    short, short_waiting = _start_run(name, order, "short", short=True)
    assert short_waiting.wait(timeout=10)

    another_full_run.close()
    for run in (full, short):
        run.join(timeout=10)

    assert order == ["short", "full"]


def test_only_the_full_run_next_in_line_makes_short_runs_wait_for_it():
    name = _unique_name()
    blocker = _held_by_the_test(name)
    order: list[str] = []
    first_full, first_full_waiting = _start_run(name, order, "first full")
    assert first_full_waiting.wait(timeout=10)
    second_full, second_full_waiting = _start_run(name, order, "second full", full_run_gives_way_s=0)
    assert second_full_waiting.wait(timeout=10)
    short, short_waiting = _start_run(name, order, "short", short=True)
    assert short_waiting.wait(timeout=10)

    blocker.close()
    for run in (first_full, second_full, short):
        run.join(timeout=10)

    assert order == ["short", "first full", "second full"]


_WAIT_IN_LINE = """
import pathlib, sys
sys.path.insert(0, {root!r})
from tests.integration.session_lock import hold_integration_lock
def waiting(state):
    if state.seconds >= 0.15 and (state.goes_next or {short!r}):
        pathlib.Path({sentinel!r}).write_text("waiting")
with hold_integration_lock(name={name!r}, short={short!r}, notify_every_s=0.05,
                           full_run_gives_way_s=0, notify=waiting):
    pass
"""


@pytest.mark.parametrize("dies, waits", [("short", "full"), ("full", "short"), ("full", "full")])
def test_a_run_that_dies_waiting_holds_no_other_run_back(tmp_path, dies, waits):
    name = _unique_name()
    blocker = _held_by_the_test(name)
    sentinel = tmp_path / "waiting.flag"
    root = str(Path(__file__).resolve().parents[1])
    code = _WAIT_IN_LINE.format(root=root, name=name, short=dies == "short", sentinel=str(sentinel))
    dying = subprocess.Popen([sys.executable, "-c", code], cwd=root,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        wait_until(sentinel.exists, timeout=60.0)
    finally:
        dying.kill()
        dying.wait(timeout=10)
    order: list[str] = []
    survivor, survivor_waiting = _start_run(name, order, waits, short=waits == "short")
    assert survivor_waiting.wait(timeout=10)

    blocker.close()
    survivor.join(timeout=10)

    assert order == [waits]

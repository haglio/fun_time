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


def test_hold_integration_lock_queues_until_free_then_releases():
    """The context manager blocks (reporting the wait) until the lock frees,
    enters once it holds it, and releases on exit so the next caller proceeds."""
    name = _unique_name()
    blocker = SingleInstanceLock(name)
    assert blocker.acquire(timeout=1.0) is True

    notifications: list[float] = []
    entered = threading.Event()

    def contender() -> None:
        with hold_integration_lock(name=name, poll_seconds=0.1, notify=notifications.append):
            entered.set()

    thread = threading.Thread(target=contender)
    thread.start()
    # Wait for the SIGNAL that the contender is queuing — its notify callback —
    # not for a fixed nap.  The old 0.35s sleep made this the one test that
    # could fail for the runner's load rather than for the code.
    wait_until(lambda: bool(notifications), timeout=10.0)
    # Notified it is waiting, and still blocked behind the held lock.
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

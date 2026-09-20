"""Tests for fun_time.single_instance: the name Fun Time claims and the notice
it shows.  The mutex itself is app_support.win32's, and tested there."""
from __future__ import annotations

import ast
import ctypes
import threading
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from shared_ui.alert import Level

from fun_time import single_instance
from fun_time.project_paths import PROJECT_ICON
from fun_time.single_instance import (
    MUTEX_ORCHESTRATOR,
    claim_the_session,
    let_the_session_go,
    show_already_running_message,
)


class TestClaimingTheSession:
    """What tells a second launch that a session is already playing."""

    def _a_name(self) -> str:
        return f"Local\\FunTimeTest.{uuid4().hex}"

    def test_a_name_nobody_holds_is_claimed(self):
        claimed = claim_the_session(self._a_name())

        assert claimed
        let_the_session_go(claimed)

    def test_a_name_a_live_session_holds_is_refused(self):
        """Asked from a thread of its own, because ownership is a thread's: the
        second launch is another process, and this is the nearest a test gets."""
        name = self._a_name()
        first = claim_the_session(name)

        assert _claimed_elsewhere(name) is None

        let_the_session_go(first)

    def test_the_name_is_free_again_once_that_session_lets_go(self):
        name = self._a_name()
        let_the_session_go(claim_the_session(name))

        second = claim_the_session(name)

        assert second
        let_the_session_go(second)

    def test_a_name_left_behind_by_a_session_nothing_is_playing_from_is_claimed(self):
        """Windows keeps a mutex alive while any handle to it is open, and a
        session whose process it has not finished reaping still holds one -- so
        a launch that refused on the name alone refused for as long as the
        machine was up, with no Fun Time running at all.  Ownership is what says
        a session is live, and this name is owned by nobody."""
        name = self._a_name()
        left_behind = _left_behind(name)

        claimed = claim_the_session(name)

        assert claimed
        let_the_session_go(claimed)
        _close(left_behind)

    def test_letting_go_of_a_name_a_wedged_session_abandoned_frees_it_for_the_next(self):
        """A session that hangs on the way out leaves the name ABANDONED: its
        owning thread is gone, its process lingers holding a handle open.  The
        relay that carries a crossing only ASKS whether the outgoing session has
        let go -- and asking took the name and did not give it back, so the wait
        said free, the relay started the other session, and that session was
        refused by the relay itself.  A crossing on 2026-09-20 left nothing
        running at all that way, with the desktop session already shut down.
        """
        name = self._a_name()
        wedged = _abandoned_by_a_dead_owner(name)

        let_the_session_go(claim_the_session(name))

        assert _claimed_elsewhere(name) is not None
        _close(wedged)


def _claimed_elsewhere(name: str) -> int | None:
    """What a claim from another thread answers -- a mutex is re-entrant for the
    thread that owns it, so one from this thread would say yes to itself."""
    answer: list[int | None] = []
    thread = threading.Thread(target=lambda: answer.append(claim_the_session(name)))
    thread.start()
    thread.join()
    let_the_session_go(answer[0])
    return answer[0]


def _abandoned_by_a_dead_owner(name: str) -> int:
    """A handle to *name* whose owner thread has gone, as a session wedged on
    the way out leaves: the object stays alive on the handle, owned by nobody."""
    claimed: list[int | None] = []
    owner = threading.Thread(target=lambda: claimed.append(claim_the_session(name)))
    owner.start()
    owner.join()
    handle = claimed[0]
    assert handle, "the owner thread could not claim the name to abandon"
    return handle


def _left_behind(name: str) -> int:
    """A handle to *name* that owns nothing, as a session that died leaves."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    return kernel32.CreateMutexW(None, False, name)


def _close(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle(ctypes.c_void_p(handle))


def test_the_mutex_name_is_the_one_every_running_session_was_started_under():
    # Spelled out rather than derived: a session started before a change to
    # this string is not refused by a session started after it, and the two
    # then drive the same players' channels together.
    assert MUTEX_ORCHESTRATOR == "Global\\FunTime.Orchestrator"


class TestShowAlreadyRunningMessage:
    def test_it_is_the_familys_notice_under_fun_times_icon(self):
        with patch("shared_ui.alert.show_alert") as show_alert:
            show_already_running_message("Test text", "Test Title")

        show_alert.assert_called_once_with(
            "Test Title", "Test text", level=Level.INFO, icon=PROJECT_ICON,
        )

    def test_default_title(self):
        with patch("shared_ui.alert.show_alert") as show_alert:
            show_already_running_message("Some message")

        assert show_alert.call_args.args[0] == "Fun Time"

    def test_asking_whether_it_is_alone_does_not_drag_in_qt(self):
        """The orchestrator asks this long before it has any use for Qt, and
        on the answer it wants it never builds a window at all -- so the
        dialog's imports live inside the call, not at the top of the module."""
        module = ast.parse(Path(single_instance.__file__).read_text(encoding="utf-8"))
        at_the_top = [
            name
            for node in module.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in ([node.module] if isinstance(node, ast.ImportFrom)
                         else [alias.name for alias in node.names])
        ]

        assert not [name for name in at_the_top if name and name.startswith("shared_ui")]

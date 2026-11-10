from __future__ import annotations

import ast
import ctypes
import threading
from pathlib import Path
from uuid import uuid4

from shared_ui.alert import Level
from shared_ui.preview import Preview
from shared_ui.testing import alert_shown

from fun_time import preview_marker, single_instance
from fun_time.project_paths import PROJECT_ICON
from fun_time.single_instance import (
    MUTEX_ORCHESTRATOR,
    claim_the_session,
    let_the_session_go,
    show_already_running_message,
)


class TestClaimingTheSession:
    def _a_name(self) -> str:
        return f"Local\\FunTimeTest.{uuid4().hex}"

    def test_a_name_nobody_holds_is_claimed(self):
        claimed = claim_the_session(self._a_name())

        assert claimed
        let_the_session_go(claimed)

    def test_a_name_a_live_session_holds_is_refused(self):
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
        name = self._a_name()
        kept_alive = a_handle_windows_keeps_the_name_alive_for(name)

        claimed = claim_the_session(name)

        assert claimed
        let_the_session_go(claimed)
        close_the_handle(kept_alive)

    def test_letting_go_of_a_name_a_wedged_session_abandoned_frees_it_for_the_next(self):
        name = self._a_name()
        wedged = _abandoned_by_a_dead_owner(name)

        let_the_session_go(claim_the_session(name))

        assert _claimed_elsewhere(name) is not None
        close_the_handle(wedged)


def _claimed_elsewhere(name: str) -> int | None:
    answer: list[int | None] = []
    thread = threading.Thread(target=lambda: answer.append(claim_the_session(name)))
    thread.start()
    thread.join()
    let_the_session_go(answer[0])
    return answer[0]


def _abandoned_by_a_dead_owner(name: str) -> int:
    claimed: list[int | None] = []
    owner = threading.Thread(target=lambda: claimed.append(claim_the_session(name)))
    owner.start()
    owner.join()
    handle = claimed[0]
    assert handle, "the owner thread could not claim the name to abandon"
    return handle


def a_handle_windows_keeps_the_name_alive_for(name: str) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    return kernel32.CreateMutexW(None, False, name)


def close_the_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle(ctypes.c_void_p(handle))


def test_the_mutex_name_is_the_one_every_running_session_was_started_under():
    assert MUTEX_ORCHESTRATOR == "Global\\FunTime.Orchestrator"


class TestShowAlreadyRunningMessage:
    def test_it_is_the_familys_message_under_fun_times_icon(self):
        with alert_shown() as show_alert:
            show_already_running_message("Test text", "Test Title")

        show_alert.assert_called_once_with(
            "Test Title", "Test text", level=Level.INFO, icon=PROJECT_ICON,
        )

    def test_default_title(self):
        with alert_shown() as show_alert:
            show_already_running_message("Some message")

        assert show_alert.call_args.args[0] == "Fun Time"

    def test_asking_whether_it_is_alone_does_not_drag_in_qt(self):
        module = ast.parse(Path(single_instance.__file__).read_text(encoding="utf-8"))
        at_the_top = [
            name
            for node in module.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in ([node.module] if isinstance(node, ast.ImportFrom)
                         else [alias.name for alias in node.names])
        ]

        assert not [name for name in at_the_top if name and name.startswith("shared_ui")]


def test_a_branch_sessions_already_running_message_is_marked_as_the_preview(monkeypatch, tmp_path):
    monkeypatch.setenv(preview_marker.FLAG, "1")
    monkeypatch.setattr(preview_marker, "preview_of", lambda checkout: Preview(feature="the new crossing"))
    monkeypatch.setattr(preview_marker, "INKED_ICON_FOLDER", tmp_path)
    with alert_shown() as show_alert:
        show_already_running_message("Some message")

    assert show_alert.call_args.args[0] == "Fun Time \u2014 preview of the new crossing"
    assert show_alert.call_args.kwargs["icon"].parent == tmp_path

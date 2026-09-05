"""Tests for fun_time.single_instance: the name Fun Time claims and the notice
it shows.  The mutex itself is app_support.win32's, and tested there."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

from shared_ui.alert import Level

from fun_time import single_instance
from fun_time.project_paths import PROJECT_ICON
from fun_time.single_instance import MUTEX_ORCHESTRATOR, show_already_running_message


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

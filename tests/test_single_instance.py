"""Tests for fun_time.single_instance."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from shared_ui.alert import Level

from fun_time import single_instance
from fun_time.project_paths import PROJECT_ICON
from fun_time.single_instance import (
    ERROR_ALREADY_EXISTS,
    MUTEX_ORCHESTRATOR,
    mutex_name_for_config,
    show_already_running_message,
    try_acquire_mutex,
)


class TestTryAcquireMutex:
    def test_returns_handle_when_first_instance(self):
        with patch("fun_time.single_instance._kernel32") as k32, \
             patch("fun_time.single_instance._get_last_error", return_value=0):
            k32.CreateMutexW.return_value = 12345
            result = try_acquire_mutex("Global\\Test")

        assert result == 12345
        k32.CloseHandle.assert_not_called()

    def test_returns_none_when_already_running(self):
        with patch("fun_time.single_instance._kernel32") as k32, \
             patch("fun_time.single_instance._get_last_error", return_value=ERROR_ALREADY_EXISTS):
            k32.CreateMutexW.return_value = 99
            result = try_acquire_mutex("Global\\Test")

        assert result is None
        k32.CloseHandle.assert_called_once_with(99)

    def test_returns_none_when_create_mutex_fails(self):
        with patch("fun_time.single_instance._kernel32") as k32:
            k32.CreateMutexW.return_value = 0
            result = try_acquire_mutex("Global\\Test")

        assert result is None

    def test_uses_ctypes_get_last_error_not_kernel32(self):
        """Regression: _kernel32.GetLastError() can return stale data
        because Python resets the per-thread error between ctypes calls.
        Must use ctypes.get_last_error() with use_last_error=True."""
        with patch("fun_time.single_instance._kernel32") as k32, \
             patch("fun_time.single_instance._get_last_error", return_value=ERROR_ALREADY_EXISTS):
            k32.CreateMutexW.return_value = 99
            # Simulate stale GetLastError (the bug scenario)
            k32.GetLastError.return_value = 0
            result = try_acquire_mutex("Global\\Test")

        assert result is None
        k32.CloseHandle.assert_called_once_with(99)


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
        source = Path(single_instance.__file__).read_text(encoding="utf-8")
        header = source[:source.index("def show_already_running_message")]

        assert "shared_ui" not in header


class TestMutexNameForConfig:
    def test_same_path_gives_same_name(self):
        a = mutex_name_for_config(MUTEX_ORCHESTRATOR, Path("C:/foo/config.json"))
        b = mutex_name_for_config(MUTEX_ORCHESTRATOR, Path("C:/foo/config.json"))
        assert a == b

    def test_different_paths_give_different_names(self):
        a = mutex_name_for_config(MUTEX_ORCHESTRATOR, Path("C:/foo/config.json"))
        b = mutex_name_for_config(MUTEX_ORCHESTRATOR, Path("C:/bar/config.json"))
        assert a != b

    def test_includes_base_prefix(self):
        name = mutex_name_for_config(MUTEX_ORCHESTRATOR, Path("C:/foo/config.json"))
        assert name.startswith(MUTEX_ORCHESTRATOR)

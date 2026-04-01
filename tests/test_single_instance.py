"""Tests for fun_time.single_instance."""
from __future__ import annotations

from unittest.mock import patch

from pathlib import Path

from fun_time.single_instance import (
    ERROR_ALREADY_EXISTS,
    MUTEX_BROKER,
    MUTEX_ORCHESTRATOR,
    mutex_name_for_config,
    show_already_running_message,
    try_acquire_mutex,
)


class TestTryAcquireMutex:
    def test_returns_handle_when_first_instance(self):
        with patch("fun_time.single_instance._kernel32") as k32:
            k32.CreateMutexW.return_value = 12345
            k32.GetLastError.return_value = 0
            result = try_acquire_mutex("Global\\Test")

        assert result == 12345
        k32.CloseHandle.assert_not_called()

    def test_returns_none_when_already_running(self):
        with patch("fun_time.single_instance._kernel32") as k32:
            k32.CreateMutexW.return_value = 99
            k32.GetLastError.return_value = ERROR_ALREADY_EXISTS
            result = try_acquire_mutex("Global\\Test")

        assert result is None
        k32.CloseHandle.assert_called_once_with(99)

    def test_returns_none_when_create_mutex_fails(self):
        with patch("fun_time.single_instance._kernel32") as k32:
            k32.CreateMutexW.return_value = 0
            result = try_acquire_mutex("Global\\Test")

        assert result is None


class TestShowAlreadyRunningMessage:
    def test_calls_message_box_with_correct_flags(self):
        MB_OK = 0
        MB_ICONINFORMATION = 0x40
        MB_SETFOREGROUND = 0x00010000
        expected_flags = MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND

        with patch("fun_time.single_instance._user32") as u32:
            show_already_running_message("Test text", "Test Title")

        u32.MessageBoxW.assert_called_once_with(None, "Test text", "Test Title", expected_flags)

    def test_default_title(self):
        with patch("fun_time.single_instance._user32") as u32:
            show_already_running_message("Some message")

        args = u32.MessageBoxW.call_args[0]
        assert args[2] == "Fun Time"


class TestConstants:
    def test_mutex_names_use_global_namespace(self):
        assert MUTEX_BROKER.startswith("Global\\")
        assert MUTEX_ORCHESTRATOR.startswith("Global\\")

    def test_mutex_names_are_distinct(self):
        assert MUTEX_BROKER != MUTEX_ORCHESTRATOR


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
        name = mutex_name_for_config(MUTEX_BROKER, Path("C:/foo/config.json"))
        assert name.startswith(MUTEX_BROKER)

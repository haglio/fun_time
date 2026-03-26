from __future__ import annotations

from unittest.mock import patch

import pytest

from fun_time.windows_bridge_win32 import (
    wait_for_window,
    move_window,
    set_always_on_top,
    activate_window,
    get_window_rect,
    find_window_by_pid,
    find_dialog_by_pid,
    send_ctrl_o,
    wait_for_window_close,
    HWND_TOPMOST,
    HWND_NOTOPMOST,
    SW_RESTORE,
    SWP_NOZORDER,
    SWP_NOACTIVATE,
    SWP_NOMOVE,
    SWP_NOSIZE,
)


class TestWaitForWindow:
    def test_returns_hwnd_immediately_if_found(self):
        with patch("fun_time.windows_bridge_win32.find_window_by_pid", return_value=12345):
            assert wait_for_window(42, timeout_s=5.0) == 12345

    def test_returns_zero_on_timeout(self):
        with patch("fun_time.windows_bridge_win32.find_window_by_pid", return_value=0):
            assert wait_for_window(42, timeout_s=0.05) == 0

    def test_retries_until_found(self):
        attempts = [0, 0, 99999]

        with patch("fun_time.windows_bridge_win32.find_window_by_pid", side_effect=attempts):
            assert wait_for_window(42, timeout_s=5.0) == 99999


class TestMoveWindow:
    def test_restores_then_repositions(self):
        calls: list[tuple[str, tuple]] = []

        def fake_show(hwnd, cmd):
            calls.append(("ShowWindow", (hwnd, cmd)))

        def fake_setpos(hwnd, insert_after, x, y, w, h, flags):
            calls.append(("SetWindowPos", (hwnd, insert_after, x, y, w, h, flags)))

        with patch("fun_time.windows_bridge_win32._user32") as mock:
            mock.ShowWindow.side_effect = fake_show
            mock.SetWindowPos.side_effect = fake_setpos
            move_window(111, 10, 20, 800, 600)

        assert calls[0] == ("ShowWindow", (111, SW_RESTORE))
        assert calls[1] == ("SetWindowPos", (111, 0, 10, 20, 800, 600, SWP_NOZORDER | SWP_NOACTIVATE))


class TestSetAlwaysOnTop:
    def test_sets_topmost(self):
        with patch("fun_time.windows_bridge_win32._user32") as mock:
            set_always_on_top(111, True)

        args = mock.SetWindowPos.call_args[0]
        assert args[0] == 111
        assert args[1] == HWND_TOPMOST

    def test_clears_topmost(self):
        with patch("fun_time.windows_bridge_win32._user32") as mock:
            set_always_on_top(111, False)

        args = mock.SetWindowPos.call_args[0]
        assert args[1] == HWND_NOTOPMOST


class TestActivateWindow:
    def test_calls_set_foreground(self):
        with patch("fun_time.windows_bridge_win32._user32") as mock:
            activate_window(111)

        mock.SetForegroundWindow.assert_called_once_with(111)


class TestSendCtrlO:
    def test_calls_send_input_with_four_key_events(self):
        with patch("fun_time.windows_bridge_win32._user32") as mock:
            mock.SendInput.return_value = 4
            send_ctrl_o()

        mock.SendInput.assert_called_once()
        args = mock.SendInput.call_args[0]
        assert args[0] == 4  # four key events: ctrl down, o down, o up, ctrl up


class TestFindDialogByPid:
    def test_finds_dialog_window(self):
        def fake_enum(callback, _lparam):
            # Simulate a dialog window with class #32770 belonging to pid 100
            callback(55555, 0)
            return True

        def fake_get_class(hwnd, buf, size):
            if hwnd == 55555:
                for i, c in enumerate("#32770"):
                    buf[i] = c
                buf[len("#32770")] = "\x00"
            return len("#32770")

        def fake_get_pid(hwnd, pid_ptr):
            pid_ptr._obj.value = 100

        with patch("fun_time.windows_bridge_win32._user32") as mock:
            mock.EnumWindows.side_effect = fake_enum
            mock.GetClassNameW.side_effect = fake_get_class
            mock.GetWindowThreadProcessId.side_effect = fake_get_pid
            mock.IsWindowVisible.return_value = True
            result = find_dialog_by_pid(100, timeout_s=0.1)

        assert result == 55555

    def test_returns_zero_on_timeout(self):
        with patch("fun_time.windows_bridge_win32._user32") as mock:
            mock.EnumWindows.return_value = True  # no windows found
            result = find_dialog_by_pid(100, timeout_s=0.05)

        assert result == 0


class TestWaitForWindowClose:
    def test_returns_when_window_destroyed(self):
        calls = [True, True, False]  # window exists, exists, gone

        with patch("fun_time.windows_bridge_win32._user32") as mock:
            mock.IsWindow.side_effect = calls
            wait_for_window_close(55555, timeout_s=1.0)

        assert mock.IsWindow.call_count == 3

    def test_returns_on_timeout(self):
        with patch("fun_time.windows_bridge_win32._user32") as mock:
            mock.IsWindow.return_value = True  # window never closes
            wait_for_window_close(55555, timeout_s=0.05)


class TestConstants:
    def test_hwnd_topmost_value(self):
        assert HWND_TOPMOST == -1

    def test_hwnd_notopmost_value(self):
        assert HWND_NOTOPMOST == -2

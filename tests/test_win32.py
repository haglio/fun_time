from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.win32 import (
    StackedWindow,
    windows_obscuring,
    close_window,
    get_process_creation_time,
    get_process_image_name,
    is_process_alive,
    move_window,
    set_always_on_top,
    is_window_topmost,
    activate_window,
    find_window_by_pid,
    minimize_window,
    window_rect,
    HWND_TOPMOST,
    HWND_NOTOPMOST,
    GWL_EXSTYLE,
    WS_EX_TOPMOST,
    SW_MINIMIZE,
    SW_SHOWMINNOACTIVE,
    SW_RESTORE,
    SWP_NOZORDER,
    SWP_NOACTIVATE,
)


class TestFindWindowByPid:
    """The EnumWindows visibility filter, and the include_hidden override the
    startup sequencer needs to resolve the dashboard while it is hidden behind
    the loading overlay (a hidden window has WS_VISIBLE cleared)."""

    @staticmethod
    def _mock_user32(mock, *, hwnd: int, pid: int, visible: bool, title_len: int = 8):
        def gwtpid(_hwnd, pid_byref):
            pid_byref._obj.value = pid  # write the out-param DWORD
            return 1

        def enum(proc, _lparam):
            proc(hwnd, 0)  # invoke the enumeration callback with our window
            return True

        mock.GetWindowThreadProcessId.side_effect = gwtpid
        mock.EnumWindows.side_effect = enum
        mock.IsWindowVisible.return_value = 1 if visible else 0
        mock.GetWindowTextLengthW.return_value = title_len

    def test_skips_hidden_window_by_default(self):
        with patch("fun_time.win32._user32") as mock:
            self._mock_user32(mock, hwnd=555, pid=42, visible=False)
            assert find_window_by_pid(42) == 0

    def test_include_hidden_finds_hidden_window(self):
        with patch("fun_time.win32._user32") as mock:
            self._mock_user32(mock, hwnd=555, pid=42, visible=False)
            assert find_window_by_pid(42, include_hidden=True) == 555

    def test_visible_window_found_regardless_of_flag(self):
        with patch("fun_time.win32._user32") as mock:
            self._mock_user32(mock, hwnd=777, pid=42, visible=True)
            assert find_window_by_pid(42) == 777

    def test_untitled_window_skipped_even_when_hidden_included(self):
        with patch("fun_time.win32._user32") as mock:
            self._mock_user32(mock, hwnd=555, pid=42, visible=False, title_len=0)
            assert find_window_by_pid(42, include_hidden=True) == 0


class TestMoveWindow:
    def test_restores_then_repositions(self):
        calls: list[tuple[str, tuple]] = []

        def fake_show(hwnd, cmd):
            calls.append(("ShowWindow", (hwnd, cmd)))

        def fake_setpos(hwnd, insert_after, x, y, w, h, flags):
            calls.append(("SetWindowPos", (hwnd, insert_after, x, y, w, h, flags)))

        with patch("fun_time.win32._user32") as mock:
            mock.ShowWindow.side_effect = fake_show
            mock.SetWindowPos.side_effect = fake_setpos
            move_window(111, 10, 20, 800, 600)

        assert calls[0] == ("ShowWindow", (111, SW_RESTORE))
        assert calls[1] == ("SetWindowPos", (111, 0, 10, 20, 800, 600, SWP_NOZORDER | SWP_NOACTIVATE))


class TestSetAlwaysOnTop:
    def test_sets_topmost(self):
        with patch("fun_time.win32._user32") as mock:
            set_always_on_top(111, True)

        args = mock.SetWindowPos.call_args[0]
        assert args[0] == 111
        assert args[1] == HWND_TOPMOST

    def test_clears_topmost(self):
        with patch("fun_time.win32._user32") as mock:
            set_always_on_top(111, False)

        args = mock.SetWindowPos.call_args[0]
        assert args[1] == HWND_NOTOPMOST


class TestActivateWindow:
    def test_calls_set_foreground(self):
        with patch("fun_time.win32._user32") as mock:
            activate_window(111)

        mock.SetForegroundWindow.assert_called_once_with(111)


class TestMinimizeWindow:
    def test_calls_show_window_with_sw_minimize(self):
        with patch("fun_time.win32._user32") as mock_user32:
            minimize_window(99999)
        mock_user32.ShowWindow.assert_called_once_with(99999, SW_MINIMIZE)

    def test_no_activate_uses_sw_showminnoactive(self):
        with patch("fun_time.win32._user32") as mock_user32:
            minimize_window(99999, activate=False)
        mock_user32.ShowWindow.assert_called_once_with(99999, SW_SHOWMINNOACTIVE)


class TestCloseWindow:
    def test_sends_wm_close(self):
        with patch("fun_time.win32._user32") as mock:
            close_window(12345)

        mock.PostMessageW.assert_called_once_with(12345, 0x0010, 0, 0)

    def test_noop_for_zero_hwnd(self):
        with patch("fun_time.win32._user32") as mock:
            close_window(0)

        mock.PostMessageW.assert_not_called()


class TestIsWindowTopmost:
    def test_returns_true_when_topmost_bit_set(self):
        with patch("fun_time.win32._user32") as mock:
            mock.GetWindowLongW.return_value = WS_EX_TOPMOST | 0x100
            assert is_window_topmost(111) is True
        mock.GetWindowLongW.assert_called_once_with(111, GWL_EXSTYLE)

    def test_returns_false_when_topmost_bit_clear(self):
        with patch("fun_time.win32._user32") as mock:
            mock.GetWindowLongW.return_value = 0x100
            assert is_window_topmost(111) is False


class TestGetProcessImageName:
    def test_returns_own_executable_path(self):
        path = get_process_image_name(os.getpid())

        assert path is not None
        assert Path(path).name.lower() in {"python.exe", "pythonw.exe"}
        assert Path(path).is_file()

    def test_returns_none_when_process_cannot_be_opened(self):
        with patch("fun_time.win32._kernel32") as mock:
            mock.OpenProcess.return_value = None
            assert get_process_image_name(4242) is None

    def test_returns_none_when_image_query_fails(self):
        with patch("fun_time.win32._kernel32") as mock:
            mock.OpenProcess.return_value = 42
            mock.QueryFullProcessImageNameW.return_value = 0
            assert get_process_image_name(4242) is None
        mock.CloseHandle.assert_called_once_with(42)


class TestGetProcessCreationTime:
    def test_returns_a_stable_creation_time_for_our_own_process(self):
        first = get_process_creation_time(os.getpid())

        assert first is not None
        assert first > 0
        assert get_process_creation_time(os.getpid()) == first


class TestIsProcessAlive:
    def test_false_when_process_cannot_be_opened(self):
        with patch("fun_time.win32._kernel32") as mock:
            mock.OpenProcess.return_value = None
            assert is_process_alive(4242) is False

    def test_true_for_own_process(self):
        assert is_process_alive(os.getpid()) is True

    def test_false_for_pid_zero(self):
        # Callers pass 0 for children that were never launched.
        assert is_process_alive(0) is False

    def test_false_for_exited_process_whose_handle_is_still_open(self):
        # Popen holds the child's process handle, keeping the kernel object
        # (and the PID) alive after exit — OpenProcess still succeeds on such
        # a zombie, so liveness must come from GetExitCodeProcess.
        proc = subprocess.Popen([sys.executable, "-c", ""])
        proc.wait()

        assert is_process_alive(proc.pid) is False


class TestWindowsObscuring:
    """The pure z-order analysis behind the startup 'what's covering Nau' log.

    Given the visible windows front-to-back and a target hwnd, report which
    windows sit ABOVE the target AND overlap its rect — the ones actually
    hiding it.  A topmost flag alone can't answer this (a window can carry
    WS_EX_TOPMOST yet be buried under another overlapping topmost window), so
    the diagnostic walks the real stacking order instead.
    """

    @staticmethod
    def _w(hwnd, rect, *, topmost=False, title="w"):
        return StackedWindow(hwnd=hwnd, title=title, topmost=topmost, rect=rect)

    def test_frontmost_target_is_unobscured(self):
        nau = self._w(1, (0, 0, 100, 100))
        below = self._w(2, (0, 0, 100, 100))
        assert windows_obscuring(1, [nau, below]) == []

    def test_overlapping_window_above_is_reported(self):
        cover = self._w(9, (50, 50, 100, 100))
        nau = self._w(1, (0, 0, 100, 100))
        assert windows_obscuring(1, [cover, nau]) == [cover]

    def test_non_overlapping_window_above_is_ignored(self):
        # A window on another monitor sits above Nau in the topmost band but
        # never covers it — dashboard/logs/rfb are exactly this case.
        elsewhere = self._w(9, (5000, 0, 100, 100))
        nau = self._w(1, (0, 0, 100, 100))
        assert windows_obscuring(1, [elsewhere, nau]) == []

    def test_overlapping_window_below_is_ignored(self):
        nau = self._w(1, (0, 0, 100, 100))
        under = self._w(2, (0, 0, 100, 100))
        assert windows_obscuring(1, [nau, under]) == []

    def test_target_absent_reports_nothing(self):
        assert windows_obscuring(1, [self._w(2, (0, 0, 100, 100))]) == []

    def test_edge_touching_rects_do_not_count_as_overlap(self):
        # Nau's rect starts exactly where the portrait satellite's ends; a
        # shared edge is not coverage.
        adjacent = self._w(9, (0, 0, 100, 100))
        nau = self._w(1, (100, 0, 100, 100))
        assert windows_obscuring(1, [adjacent, nau]) == []


class TestConstants:
    def test_hwnd_topmost_is_64bit_pointer(self):
        import ctypes
        assert isinstance(HWND_TOPMOST, ctypes.c_void_p)
        # Must be 0xFFFFFFFFFFFFFFFF on 64-bit, not truncated 0xFFFFFFFF
        assert HWND_TOPMOST.value == (2**64 - 1)

    def test_hwnd_notopmost_is_64bit_pointer(self):
        import ctypes
        assert isinstance(HWND_NOTOPMOST, ctypes.c_void_p)
        assert HWND_NOTOPMOST.value == (2**64 - 2)


class TestLiveWindowMutationGuard:
    """The autouse guard in tests/conftest.py must keep a unit test from moving,
    topmosting, activating or closing a REAL window — the test bleed that surfaced for
    months as "Nau pops on top during OmniPause" (a concurrent agent's unit run
    resolving the live 'Nau'/'Genau' window by title and forcing it topmost)."""

    def test_mutating_user32_calls_are_inert(self):
        from fun_time import win32

        for name in ("SetWindowPos", "SetForegroundWindow", "ShowWindow", "PostMessageW"):
            # Stubbed to an inert no-op for the whole unit suite: callable, returns the
            # sentinel, and can never reach the real Win32 API on any hwnd.
            assert getattr(win32._user32, name)(0xDEAD, 0, 0, 0, 0, 0, 0) == 0
        # so a full wrapper call is a harmless no-op, even on a would-be live hwnd
        assert set_always_on_top(0xDEAD, True) is None

    def test_reader_user32_calls_stay_real(self):
        # Only the mutators are neutralised; is_window_topmost reads GWL_EXSTYLE through
        # the real GetWindowLongW, which returns 0 for a bogus hwnd -> False.  (A blanket
        # _user32 stub would have made this a truthy Mock instead.)
        assert is_window_topmost(0xDEAD) is False




class TestWindowRect:
    """Where a window sits, for a second window that must stand exactly on it."""

    def test_reports_the_windows_position_and_size(self):
        def fill(_hwnd, rect_ref):
            rect = rect_ref._obj
            rect.left, rect.top, rect.right, rect.bottom = 0, 400, 1080, 1920
            return 1

        with patch("fun_time.win32._user32") as mock:
            mock.GetWindowRect.side_effect = fill
            assert window_rect(123) == (0, 400, 1080, 1520)

    def test_reports_nothing_for_a_window_that_is_gone(self):
        with patch("fun_time.win32._user32") as mock:
            mock.GetWindowRect.return_value = 0
            assert window_rect(123) is None

from __future__ import annotations

from unittest.mock import patch

import pytest

from fun_time.win32 import (
    wait_for_window,
    move_window,
    set_always_on_top,
    activate_window,
    get_window_rect,
    find_window_by_pid,
    minimize_window,
    send_vk_to_window,
    show_open_file_dialog,
    HWND_TOPMOST,
    HWND_NOTOPMOST,
    OFN_FILEMUSTEXIST,
    OFN_NOCHANGEDIR,
    OFN_PATHMUSTEXIST,
    SW_MINIMIZE,
    SW_RESTORE,
    SWP_NOZORDER,
    SWP_NOACTIVATE,
    SWP_NOMOVE,
    SWP_NOSIZE,
)


class TestWaitForWindow:
    def test_returns_hwnd_immediately_if_found(self):
        with patch("fun_time.win32.find_window_by_pid", return_value=12345):
            assert wait_for_window(42, timeout_s=5.0) == 12345

    def test_returns_zero_on_timeout(self):
        with patch("fun_time.win32.find_window_by_pid", return_value=0):
            assert wait_for_window(42, timeout_s=0.05) == 0

    def test_retries_until_found(self):
        attempts = [0, 0, 99999]

        with patch("fun_time.win32.find_window_by_pid", side_effect=attempts):
            assert wait_for_window(42, timeout_s=5.0) == 99999


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


class TestSendVkToWindow:
    def test_posts_keydown_and_keyup(self):
        with patch("fun_time.win32._user32") as mock:
            send_vk_to_window(12345, 0x25)  # VK_LEFT

        calls = mock.PostMessageW.call_args_list
        assert len(calls) == 2
        # First call: WM_KEYDOWN (0x0100)
        assert calls[0][0][0] == 12345
        assert calls[0][0][1] == 0x0100
        assert calls[0][0][2] == 0x25
        # Second call: WM_KEYUP (0x0101)
        assert calls[1][0][0] == 12345
        assert calls[1][0][1] == 0x0101
        assert calls[1][0][2] == 0x25


class TestMinimizeWindow:
    def test_calls_show_window_with_sw_minimize(self):
        with patch("fun_time.win32._user32") as mock_user32:
            minimize_window(99999)
        mock_user32.ShowWindow.assert_called_once_with(99999, SW_MINIMIZE)


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


class TestShowOpenFileDialog:
    def _get_ofn(self, mock_comdlg):
        """Extract the OPENFILENAMEW struct from the mocked call."""
        return mock_comdlg.GetOpenFileNameW.call_args[0][0]._obj

    def test_sets_initial_directory(self):
        """lpstrInitialDir is set to the provided directory."""
        with patch("fun_time.win32._comdlg32") as mock_comdlg, \
             patch("fun_time.win32._ole32"):
            mock_comdlg.GetOpenFileNameW.return_value = 0
            show_open_file_dialog(r"C:\videos\clips")

        ofn = self._get_ofn(mock_comdlg)
        assert ofn.lpstrInitialDir == r"C:\videos\clips"

    def test_returns_none_on_cancel(self):
        """Returns None when the user cancels the dialog."""
        with patch("fun_time.win32._comdlg32") as mock_comdlg, \
             patch("fun_time.win32._ole32"):
            mock_comdlg.GetOpenFileNameW.return_value = 0
            result = show_open_file_dialog(r"C:\videos")

        assert result is None

    def test_returns_selected_file_path(self):
        """Returns the file path when the user selects a file."""
        import ctypes as ct
        from fun_time.win32 import OPENFILENAMEW

        def fake_get_open(ofn_ref):
            ofn = ofn_ref._obj
            # Read the raw pointer address from the lpstrFile field
            ptr_addr = ct.addressof(ofn) + OPENFILENAMEW.lpstrFile.offset
            buf_addr = ct.c_void_p.from_address(ptr_addr).value
            path_bytes = r"C:\videos\movie.mp4".encode("utf-16-le") + b"\x00\x00"
            ct.memmove(buf_addr, path_bytes, len(path_bytes))
            return 1

        with patch("fun_time.win32._comdlg32") as mock_comdlg, \
             patch("fun_time.win32._ole32"):
            mock_comdlg.GetOpenFileNameW.side_effect = fake_get_open
            result = show_open_file_dialog(r"C:\videos")

        assert result == r"C:\videos\movie.mp4"

    def test_sets_correct_flags(self):
        """Must set FILEMUSTEXIST, PATHMUSTEXIST, NOCHANGEDIR."""
        with patch("fun_time.win32._comdlg32") as mock_comdlg, \
             patch("fun_time.win32._ole32"):
            mock_comdlg.GetOpenFileNameW.return_value = 0
            show_open_file_dialog(r"C:\videos")

        ofn = self._get_ofn(mock_comdlg)
        assert ofn.Flags & OFN_FILEMUSTEXIST
        assert ofn.Flags & OFN_PATHMUSTEXIST
        assert ofn.Flags & OFN_NOCHANGEDIR

    def test_has_video_file_filter(self):
        """Filter string starts with the video file filter label."""
        import ctypes as ct
        from fun_time.win32 import OPENFILENAMEW, _VIDEO_FILTER

        with patch("fun_time.win32._comdlg32") as mock_comdlg, \
             patch("fun_time.win32._ole32"):
            mock_comdlg.GetOpenFileNameW.return_value = 0
            show_open_file_dialog(r"C:\videos")

        ofn = self._get_ofn(mock_comdlg)
        # lpstrFilter is c_wchar_p which truncates at first null; read raw memory
        ptr_addr = ct.addressof(ofn) + OPENFILENAMEW.lpstrFilter.offset
        str_addr = ct.c_void_p.from_address(ptr_addr).value
        raw = ct.wstring_at(str_addr, len(_VIDEO_FILTER))
        assert "*.mp4" in raw
        assert "*.mkv" in raw

    def test_initializes_and_uninitializes_com(self):
        """COM is initialized (STA) before the dialog and cleaned up after."""
        with patch("fun_time.win32._comdlg32") as mock_comdlg, \
             patch("fun_time.win32._ole32") as mock_ole:
            mock_comdlg.GetOpenFileNameW.return_value = 0
            show_open_file_dialog(r"C:\videos")

        mock_ole.CoInitializeEx.assert_called_once()
        mock_ole.CoUninitialize.assert_called_once()

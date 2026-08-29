from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import call, patch

import pytest

from fun_time import win32
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
    force_foreground_window,
    minimize_window,
    restore_window,
    window_exists,
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
    def test_a_move_restores_a_minimized_window_before_placing_it(self):
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
    def test_true_pins_the_window_into_the_topmost_band(self):
        with patch("fun_time.win32._user32") as mock:
            set_always_on_top(111, True)

        args = mock.SetWindowPos.call_args[0]
        assert args[0] == 111
        assert args[1] == HWND_TOPMOST

    def test_false_drops_the_window_out_of_the_topmost_band(self):
        with patch("fun_time.win32._user32") as mock:
            set_always_on_top(111, False)

        args = mock.SetWindowPos.call_args[0]
        assert args[1] == HWND_NOTOPMOST


class TestAWindowThatHasStoppedAnswering:
    """SetWindowPos and ShowWindow SEND messages to the thread owning the window
    and wait for it to handle them, with no timeout — so a player whose own loop
    has stalled froze the session that called them.  Startup's topmost pass hung
    on Genau's window: no main player, no hotkey script, and no way to quit.
    """

    @staticmethod
    @contextlib.contextmanager
    def _hung(monkeypatch):
        """A user32 whose calls never return, freed again when the block ends.

        A scoped ``with patch(...)`` rather than ``.start()`` + a finally's
        ``patch.stopall()`` — the stopall stopped EVERY active patch in the
        process, and a raise between start() and the finally leaked the stub
        into the rest of the session."""
        monkeypatch.setattr(win32, "HUNG_WINDOW_TIMEOUT_S", 0.05)
        monkeypatch.setattr(win32, "_owned_by_this_process", lambda _hwnd: False)
        released = threading.Event()

        def block(*_args):
            released.wait(10)

        try:
            with patch("fun_time.win32._user32") as mock:
                mock.SetWindowPos.side_effect = block
                mock.ShowWindow.side_effect = block
                yield
        finally:
            released.set()  # free the workers still parked on the block

    def test_our_own_windows_are_called_straight(self, monkeypatch):
        """The send would go to this process's UI thread — the very thread waiting
        on the worker — so waiting on it deadlocks against a pump that cannot
        happen.  It cost the dashboard the band on its own reference popup."""
        monkeypatch.setattr(win32, "HUNG_WINDOW_TIMEOUT_S", 0.05)
        monkeypatch.setattr(win32, "_owned_by_this_process", lambda _hwnd: True)
        threads: list[str] = []

        with patch("fun_time.win32._user32") as mock:
            mock.SetWindowPos.side_effect = (
                lambda *_a: threads.append(threading.current_thread().name))
            set_always_on_top(111, True)

        assert threads == [threading.current_thread().name]

    def test_the_caller_gives_up_instead_of_waiting_for_ever(self, monkeypatch):
        with self._hung(monkeypatch):
            started = time.monotonic()
            set_always_on_top(111, True)
            minimize_window(111, activate=False)
            restore_window(111, activate=False)
            move_window(111, 0, 0, 10, 10)

            # Generous next to the 0.05s timeout, tight next to the 10s each
            # blocked call would cost if the give-up stopped working.
            assert time.monotonic() - started < 5

    def test_it_says_which_window_stopped_answering(self, monkeypatch, caplog):
        """The session gave no clue which of six windows had wedged it."""
        with self._hung(monkeypatch):
            with caplog.at_level(logging.WARNING, logger="fun_time.win32"):
                set_always_on_top(4242, True)

        assert "4242" in caplog.text
        assert "stopped answering" in caplog.text

    def test_a_window_that_answers_is_not_slowed_down(self, monkeypatch):
        """The wait is only ever spent on a window that has stalled: a healthy one
        returns in microseconds, and the call order the caller made — which is what
        stacks Genau's HUD above Nau's video — is unchanged."""
        monkeypatch.setattr(win32, "HUNG_WINDOW_TIMEOUT_S", 30)
        monkeypatch.setattr(win32, "_owned_by_this_process", lambda _hwnd: False)
        order: list[int] = []

        with patch("fun_time.win32._user32") as mock:
            mock.SetWindowPos.side_effect = lambda hwnd, *_a: order.append(hwnd)
            started = time.monotonic()
            for hwnd in (1, 2, 3):
                set_always_on_top(hwnd, True)

        assert order == [1, 2, 3]
        assert time.monotonic() - started < 5


class TestActivateWindow:
    def test_activating_asks_windows_to_bring_it_to_the_foreground(self):
        with patch("fun_time.win32._user32") as mock:
            activate_window(111)

        mock.SetForegroundWindow.assert_called_once_with(111)


class TestWindowExists:
    """A handle outlives the window it named, so anything that must reach THAT
    window and no other has to ask first."""

    def test_zero_is_never_a_window(self):
        with patch("fun_time.win32._user32") as mock:
            mock.IsWindow.return_value = 1
            assert window_exists(0) is False
        mock.IsWindow.assert_not_called()

    def test_follows_is_window(self):
        with patch("fun_time.win32._user32") as mock:
            mock.IsWindow.return_value = 0
            assert window_exists(4321) is False
            mock.IsWindow.return_value = 1
            assert window_exists(4321) is True


class TestForceForegroundWindow:
    """Chrome gives a forwarded URL to the most recently activated window of the
    profile, so the RFB tab handoff has to activate Fun Time's own Chrome window
    first.  The bridge owns neither the foreground window nor the last input when
    a lock hotkey lands, and Windows refuses SetForegroundWindow from there —
    silently, with no WM_ACTIVATE — unless the input queues are attached."""

    @staticmethod
    def _mock(user32, kernel32, *, ends_up_foreground: int, was_foreground: int = 999):
        user32.IsWindow.return_value = 1
        user32.GetForegroundWindow.side_effect = [was_foreground, ends_up_foreground]
        user32.GetWindowThreadProcessId.return_value = 7001
        user32.AttachThreadInput.return_value = 1
        kernel32.GetCurrentThreadId.return_value = 7002

    def test_attaches_the_foreground_queue_activates_then_detaches(self):
        with patch("fun_time.win32._user32") as user32, \
             patch("fun_time.win32._kernel32") as kernel32:
            self._mock(user32, kernel32, ends_up_foreground=111)

            assert force_foreground_window(111) is True

        assert user32.AttachThreadInput.call_args_list == [
            call(7001, 7002, True),
            call(7001, 7002, False),
        ]
        user32.SetForegroundWindow.assert_called_once_with(111)
        user32.BringWindowToTop.assert_called_once_with(111)

    def test_reports_false_when_the_window_did_not_take_the_foreground(self):
        with patch("fun_time.win32._user32") as user32, \
             patch("fun_time.win32._kernel32") as kernel32:
            self._mock(user32, kernel32, ends_up_foreground=999)

            assert force_foreground_window(111) is False

    def test_dead_handle_activates_nothing(self):
        with patch("fun_time.win32._user32") as user32, \
             patch("fun_time.win32._kernel32") as kernel32:
            self._mock(user32, kernel32, ends_up_foreground=111)
            user32.IsWindow.return_value = 0

            assert force_foreground_window(111) is False

        user32.SetForegroundWindow.assert_not_called()
        user32.AttachThreadInput.assert_not_called()

    def test_no_foreground_window_means_nothing_to_attach_to(self):
        """The hidden desktop the integration suite runs on has no foreground
        window: the activation still lands, and this still reads False."""
        with patch("fun_time.win32._user32") as user32, \
             patch("fun_time.win32._kernel32") as kernel32:
            self._mock(user32, kernel32, ends_up_foreground=0, was_foreground=0)

            assert force_foreground_window(111) is False

        user32.AttachThreadInput.assert_not_called()
        user32.SetForegroundWindow.assert_called_once_with(111)

    def test_detaches_even_when_the_activation_raises(self):
        with patch("fun_time.win32._user32") as user32, \
             patch("fun_time.win32._kernel32") as kernel32:
            self._mock(user32, kernel32, ends_up_foreground=111)
            user32.SetForegroundWindow.side_effect = OSError("denied")

            with pytest.raises(OSError):
                force_foreground_window(111)

        assert user32.AttachThreadInput.call_args_list[-1] == call(7001, 7002, False)


class TestMinimizeWindow:
    def test_minimizing_animates_and_may_take_focus_by_default(self):
        with patch("fun_time.win32._user32") as mock_user32:
            minimize_window(99999)
        mock_user32.ShowWindow.assert_called_once_with(99999, SW_MINIMIZE)

    def test_no_activate_uses_sw_showminnoactive(self):
        with patch("fun_time.win32._user32") as mock_user32:
            minimize_window(99999, activate=False)
        mock_user32.ShowWindow.assert_called_once_with(99999, SW_SHOWMINNOACTIVE)


class TestCloseWindow:
    def test_closing_asks_the_window_to_close_itself_not_the_process(self):
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

    def test_an_unanswerable_image_query_reads_as_no_name(self):
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

    def test_a_ghost_frame_sliver_does_not_count_as_overlap(self):
        # A maximized window's GetWindowRect includes its INVISIBLE resize
        # frame, hanging ~8px onto the neighboring monitor — the startup log
        # warned that a maximized Chrome on one monitor "covered" the player
        # on the monitor next to it, over pixels nobody can see.
        chrome = self._w(9, (-8, -8, 2576, 1426))     # maximized on 2560x1410
        portrait = self._w(1, (2560, 0, 1440, 2560))  # the next monitor over
        assert windows_obscuring(1, [chrome, portrait]) == []

    def test_real_coverage_past_the_ghost_frame_still_counts(self):
        chrome = self._w(9, (-8, -8, 2576, 1426))
        landscape = self._w(1, (854, 0, 1706, 1410))  # same monitor: buried
        assert windows_obscuring(1, [chrome, landscape]) == [chrome]

    def test_the_ghost_frame_rule_holds_on_the_vertical_axis_too(self):
        """Vertically stacked windows are the NORMAL case on the portrait
        monitor — the portrait satellite sits directly above the main player —
        so this is the axis the startup diagnostic actually runs on.  Every
        other fixture here stands side by side, which left the vertical half
        of the tolerance deletable with the class green."""
        # A maximized window above, its invisible frame hanging ~8px down
        # onto the window below it: a sliver, not coverage.
        upper = self._w(9, (0, -8, 1440, 2516))       # bottom edge at y=2508
        lower = self._w(1, (0, 2500, 1440, 940))      # the player beneath
        assert windows_obscuring(1, [upper, lower]) == []

        # Past the frame it is real coverage again.
        deeper = self._w(9, (0, -8, 1440, 2560))      # bottom edge at y=2552
        assert windows_obscuring(1, [deeper, lower]) == [deeper]


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

        for name in ("SetWindowPos", "SetForegroundWindow", "ShowWindow", "PostMessageW",
                     "BringWindowToTop"):
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

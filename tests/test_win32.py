from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import call, patch

import pytest

from fun_time import win32, win32_process
from fun_time.win32 import (
    GWL_EXSTYLE,
    HWND_NOTOPMOST,
    HWND_TOPMOST,
    SW_MINIMIZE,
    SW_RESTORE,
    SW_SHOWMINNOACTIVE,
    SWP_NOACTIVATE,
    SWP_NOZORDER,
    WS_EX_TOPMOST,
    StackedWindow,
    activate_window,
    close_window,
    find_window_by_pid,
    force_foreground_window,
    is_window_topmost,
    minimize_window,
    move_window,
    restore_window,
    set_always_on_top,
    window_exists,
    window_rect,
    windows_obscuring,
)
from fun_time.win32_process import (
    get_process_creation_time,
    get_process_image_name,
    is_process_alive,
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
        with patch("fun_time.win32_process._kernel32") as mock:
            mock.OpenProcess.return_value = None
            assert get_process_image_name(4242) is None

    def test_an_unanswerable_image_query_reads_as_no_name(self):
        with patch("fun_time.win32_process._kernel32") as mock:
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
        with patch("fun_time.win32_process._kernel32") as mock:
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


class TestFindWindowByTitle:
    """The lookup every role resolution and both covers go through.

    Only the integration suite has ever driven it against real windows, so its
    two switches — substring versus exact, and whether a hidden window counts —
    are pinned here against a faked enumeration instead.
    """

    @staticmethod
    def _enumerating(mock, windows, *, visible=True):
        """Answer EnumWindows with *windows*, a list of (hwnd, title)."""
        titles = dict(windows)

        def enum(proc, _lparam):
            for hwnd, _title in windows:
                if not proc(hwnd, 0):
                    break
            return True

        def text(hwnd, buf, _cap):
            buf.value = titles[hwnd]
            return len(titles[hwnd])

        mock.EnumWindows.side_effect = enum
        mock.GetWindowTextW.side_effect = text
        mock.IsWindowVisible.return_value = 1 if visible else 0

    def test_a_substring_matches_by_default(self):
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, "Genau — Example Studio")])
            assert win32.find_window_by_title("Genau") == 11

    def test_exact_refuses_the_window_that_merely_contains_the_name(self):
        """The session opens three windows whose titles start "Fun Time" — the
        dashboard, the loading cover and the library browser — so only the whole
        caption tells the dashboard from the cover it hides behind."""
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, "Fun Time Loading"), (12, "Fun Time")])
            assert win32.find_window_by_title("Fun Time", exact=True) == 12

    def test_a_substring_would_have_taken_the_wrong_one_of_those(self):
        """The other half of the case above: without ``exact`` the cover
        answers a lookup meant for the dashboard."""
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, "Fun Time Loading"), (12, "Fun Time")])
            assert win32.find_window_by_title("Fun Time") == 11

    def test_a_hidden_window_is_skipped_unless_it_is_asked_for(self):
        """The dashboard is SW_HIDE behind the loading cover when startup has
        to resolve it."""
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, "Fun Time")], visible=False)
            assert win32.find_window_by_title("Fun Time") == 0
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, "Fun Time")], visible=False)
            assert win32.find_window_by_title("Fun Time", include_hidden=True) == 11

    def test_the_first_match_stops_the_walk(self):
        seen: list[int] = []

        def enum(proc, _lparam):
            for hwnd in (11, 12):
                seen.append(hwnd)
                if not proc(hwnd, 0):
                    break
            return True

        with patch("fun_time.win32._user32") as mock:
            mock.EnumWindows.side_effect = enum
            mock.GetWindowTextW.side_effect = lambda _h, buf, _c: setattr(buf, "value", "Nau")
            mock.IsWindowVisible.return_value = 1
            assert win32.find_window_by_title("Nau") == 11
        assert seen == [11]

    def test_every_title_is_read_into_ONE_buffer_of_256(self):
        """One buffer, allocated once outside the callback and reused for every
        window — so a read is capped at 256 characters however long the real
        title is, and `find_window_for_process` next door does the opposite on
        purpose.  Two windows, so a per-window allocation cannot pass."""
        buffers: list[int] = []
        caps: list[int] = []

        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, "Genau"), (12, "Nau")])
            mock.GetWindowTextW.side_effect = lambda hwnd, buf, cap: (
                buffers.append(id(buf)), caps.append(cap),
                setattr(buf, "value", "Nau" if hwnd == 12 else "Genau"))[0]
            assert win32.find_window_by_title("Nau", exact=True) == 12

        assert caps == [256, 256]
        assert len(set(buffers)) == 1, "a buffer per window, not one for the walk"

    def test_nothing_matching_is_no_window(self):
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, "Genau")])
            assert win32.find_window_by_title("Origenerator") == 0


class TestWaitForWindowByTitle:
    """Polling the lookup above until a window arrives, or the budget is spent."""

    def test_the_first_hit_returns_without_spending_the_rest(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(win32.time, "sleep", slept.append)
        monkeypatch.setattr(win32, "find_window_by_title", lambda *_a, **_k: 77)

        assert win32.wait_for_window_by_title("Nau", timeout_s=5.0) == 77
        assert slept == []

    def test_a_window_that_never_arrives_is_no_window(self, monkeypatch):
        monkeypatch.setattr(win32.time, "sleep", lambda _s: None)
        monkeypatch.setattr(win32, "find_window_by_title", lambda *_a, **_k: 0)

        assert win32.wait_for_window_by_title("Nau", timeout_s=0.0) == 0

    def test_it_keeps_asking_until_the_window_opens(self, monkeypatch):
        answers = iter([0, 0, 42])
        monkeypatch.setattr(win32.time, "sleep", lambda _s: None)
        monkeypatch.setattr(win32, "find_window_by_title", lambda *_a, **_k: next(answers))

        assert win32.wait_for_window_by_title("Nau", timeout_s=5.0) == 42

    def test_both_switches_reach_the_lookup(self, monkeypatch):
        asked: list[tuple] = []
        monkeypatch.setattr(win32.time, "sleep", lambda _s: None)
        monkeypatch.setattr(
            win32, "find_window_by_title",
            lambda title, **kwargs: (asked.append((title, kwargs)), 9)[1])

        win32.wait_for_window_by_title("Fun Time", exact=True, include_hidden=True)

        assert asked == [("Fun Time", {"exact": True, "include_hidden": True})]


class TestFindWindowForProcess:
    """Pid AND title, because neither alone names one window.

    A process can own several titled windows (the hosted Origenerator opens one
    per satellite region) and a title can land on another process's window (a
    standalone Origenerator carries the same captions).
    """

    @staticmethod
    def _enumerating(mock, windows):
        """*windows* is a list of (hwnd, pid, title)."""
        by_hwnd = {hwnd: (pid, title) for hwnd, pid, title in windows}

        def enum(proc, _lparam):
            for hwnd, _pid, _title in windows:
                if not proc(hwnd, 0):
                    break
            return True

        def gwtpid(hwnd, pid_ref):
            pid_ref._obj.value = by_hwnd[hwnd][0]
            return 1

        mock.EnumWindows.side_effect = enum
        mock.GetWindowThreadProcessId.side_effect = gwtpid
        mock.GetWindowTextLengthW.side_effect = lambda hwnd: len(by_hwnd[hwnd][1])
        mock.GetWindowTextW.side_effect = lambda hwnd, buf, _cap: setattr(
            buf, "value", by_hwnd[hwnd][1])

    def test_the_window_of_that_pid_with_that_exact_title(self, monkeypatch):
        monkeypatch.setattr(win32, "list_child_pids", lambda _pid: [])
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, 500, "Portrait AI Player"),
                                     (12, 500, "Landscape AI Player")])
            assert win32.find_window_for_process(500, "Landscape AI Player") == 12

    def test_a_title_that_merely_contains_the_name_is_not_it(self, monkeypatch):
        """The match is the whole caption, not a substring of it — this lookup
        has no ``exact`` switch because it is always exact."""
        monkeypatch.setattr(win32, "list_child_pids", lambda _pid: [])
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, 500, "Origenerator — portrait")])
            assert win32.find_window_for_process(500, "Origenerator") == 0

    def test_the_same_title_on_another_process_is_not_it(self, monkeypatch):
        monkeypatch.setattr(win32, "list_child_pids", lambda _pid: [])
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, 999, "Origenerator")])
            assert win32.find_window_for_process(500, "Origenerator") == 0

    def test_a_launchers_child_owns_the_window_and_still_resolves(self, monkeypatch):
        """A venv's ``Scripts\\python.exe`` spawns the interpreter that owns the
        windows, so the recorded pid is one generation short."""
        monkeypatch.setattr(win32, "list_child_pids", lambda pid: [pid + 1])
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, 501, "Origenerator")])
            assert win32.find_window_for_process(500, "Origenerator") == 11

    def test_only_one_generation_of_children_counts(self, monkeypatch):
        """One hop is the launcher pattern; nothing spawns windows two shims
        deep, and the walk is asked for exactly once."""
        asked: list[int] = []
        monkeypatch.setattr(
            win32, "list_child_pids", lambda pid: (asked.append(pid), [501])[1])
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, 502, "Origenerator")])
            assert win32.find_window_for_process(500, "Origenerator") == 0
        assert asked == [500]

    def test_an_untitled_window_is_never_the_match(self, monkeypatch):
        monkeypatch.setattr(win32, "list_child_pids", lambda _pid: [])
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, 500, "")])
            assert win32.find_window_for_process(500, "") == 0

    def test_each_title_is_read_into_a_buffer_of_its_own_exact_length(self, monkeypatch):
        """Unlike :func:`find_window_by_title`, which reuses one 256-character
        buffer for every window it visits, this one asks each window how long
        its caption is and allocates for exactly that — so a caption past 256
        characters still compares whole.  The two are not interchangeable."""
        caps: list[int] = []
        monkeypatch.setattr(win32, "list_child_pids", lambda _pid: [])
        long_title = "Origenerator " * 30  # 390 characters
        with patch("fun_time.win32._user32") as mock:
            self._enumerating(mock, [(11, 500, long_title)])
            mock.GetWindowTextW.side_effect = lambda _h, buf, cap: (
                caps.append(cap), setattr(buf, "value", long_title))[0]
            assert win32.find_window_for_process(500, long_title) == 11

        assert caps == [len(long_title) + 1]

    def test_no_pid_is_no_window_and_no_enumeration(self, monkeypatch):
        monkeypatch.setattr(win32, "list_child_pids", lambda _pid: [])
        with patch("fun_time.win32._user32") as mock:
            assert win32.find_window_for_process(0, "Nau") == 0
            mock.EnumWindows.assert_not_called()


class TestListChildPids:
    """The one hop from a recorded pid to the process that owns the windows."""

    @staticmethod
    def _snapshot(mock, rows, *, handle=4321):
        """Answer the Toolhelp walk with *rows*, a list of (pid, parent_pid)."""
        remaining = list(rows)

        def fill(_snapshot, entry_ref):
            if not remaining:
                return 0
            pid, parent = remaining.pop(0)
            entry_ref._obj.th32ProcessID = pid
            entry_ref._obj.th32ParentProcessID = parent
            return 1

        mock.CreateToolhelp32Snapshot.return_value = handle
        mock.Process32FirstW.side_effect = fill
        mock.Process32NextW.side_effect = fill

    def test_only_the_pids_whose_parent_is_the_one_asked_about(self):
        with patch("fun_time.win32_process._kernel32") as mock:
            self._snapshot(mock, [(501, 500), (502, 999), (503, 500)])
            assert win32_process.list_child_pids(500) == [501, 503]

    def test_the_snapshot_is_always_closed(self):
        with patch("fun_time.win32_process._kernel32") as mock:
            self._snapshot(mock, [(501, 500)], handle=4321)
            win32_process.list_child_pids(500)
            mock.CloseHandle.assert_called_once_with(4321)

    def test_a_snapshot_that_could_not_be_taken_is_no_children(self):
        """INVALID_HANDLE_VALUE, which is what the declared restype makes the
        documented failure come back as.  Undeclared it answered -1, the guard
        never fired, and the walk went on to close an invalid handle."""
        with patch("fun_time.win32_process._kernel32") as mock:
            mock.CreateToolhelp32Snapshot.return_value = ctypes.wintypes.HANDLE(-1).value
            assert win32_process.list_child_pids(500) == []
            mock.CloseHandle.assert_not_called()

    def test_the_snapshot_answers_as_a_handle_not_a_32_bit_int(self):
        """Its failure value only equals INVALID_HANDLE_VALUE when it does."""
        assert win32_process._kernel32.CreateToolhelp32Snapshot.restype is (
            ctypes.wintypes.HANDLE)
        assert win32_process._kernel32.CreateToolhelp32Snapshot.argtypes == [
            ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]

    def test_a_walk_that_cannot_even_start_is_no_children(self):
        with patch("fun_time.win32_process._kernel32") as mock:
            mock.CreateToolhelp32Snapshot.return_value = 4321
            mock.Process32FirstW.return_value = 0
            assert win32_process.list_child_pids(500) == []
            mock.CloseHandle.assert_called_once_with(4321)


class TestIterZorder:
    """The real stacking order, which EnumWindows does not give.

    This is what the startup diagnostic's "what is covering Nau" reads, and the
    only walk in the module that uses GetTopWindow + GW_HWNDNEXT.
    """

    @staticmethod
    def _stack(mock, windows):
        """*windows* is a list of (hwnd, title, visible, iconic, rect)."""
        by_hwnd = {hwnd: rest for hwnd, *rest in windows}
        order = [hwnd for hwnd, *_ in windows]

        mock.GetTopWindow.return_value = order[0] if order else 0
        mock.GetWindow.side_effect = lambda hwnd, _rel: (
            order[order.index(hwnd) + 1] if order.index(hwnd) + 1 < len(order) else 0)
        mock.IsWindowVisible.side_effect = lambda hwnd: 1 if by_hwnd[hwnd][1] else 0
        mock.IsIconic.side_effect = lambda hwnd: 1 if by_hwnd[hwnd][2] else 0
        mock.GetWindowTextLengthW.side_effect = lambda hwnd: len(by_hwnd[hwnd][0])
        mock.GetWindowTextW.side_effect = lambda hwnd, buf, _cap: setattr(
            buf, "value", by_hwnd[hwnd][0])
        mock.GetWindowLongW.return_value = 0

        def rect_of(hwnd, rect_ref):
            left, top, width, height = by_hwnd[hwnd][3]
            rect = rect_ref._obj
            rect.left, rect.top = left, top
            rect.right, rect.bottom = left + width, top + height
            return 1

        mock.GetWindowRect.side_effect = rect_of

    def test_the_walk_reports_front_to_back(self):
        with patch("fun_time.win32._user32") as mock:
            self._stack(mock, [
                (11, "Nau", True, False, (0, 0, 100, 200)),
                (12, "Fun Time", True, False, (100, 0, 300, 400)),
            ])
            stacked = win32.iter_zorder()

        assert [w.hwnd for w in stacked] == [11, 12]
        assert [w.title for w in stacked] == ["Nau", "Fun Time"]
        assert stacked[1].rect == (100, 0, 300, 400)

    def test_hidden_minimized_and_untitled_windows_are_left_out(self):
        """None of the three can visibly cover anything, and the title filter is
        what keeps the log line legible among the system's own surfaces."""
        with patch("fun_time.win32._user32") as mock:
            self._stack(mock, [
                (11, "Hidden", False, False, (0, 0, 10, 10)),
                (12, "Minimized", True, True, (0, 0, 10, 10)),
                (13, "", True, False, (0, 0, 10, 10)),
                (14, "Nau", True, False, (0, 0, 10, 10)),
            ])
            assert [w.hwnd for w in win32.iter_zorder()] == [14]

    def test_each_window_carries_whether_it_rides_the_topmost_band(self):
        with patch("fun_time.win32._user32") as mock:
            self._stack(mock, [(11, "Nau", True, False, (0, 0, 10, 10))])
            mock.GetWindowLongW.return_value = win32.WS_EX_TOPMOST
            assert win32.iter_zorder()[0].topmost is True

    def test_an_empty_desktop_is_an_empty_stack(self):
        with patch("fun_time.win32._user32") as mock:
            mock.GetTopWindow.return_value = 0
            assert win32.iter_zorder() == []


class TestDisableWindowTransitions:
    """The main slot swaps by minimizing one player and restoring the other, so
    both keep a taskbar button — and the animation that would show has to go."""

    def test_the_window_is_told_to_force_its_transitions_off(self):
        with patch("fun_time.win32._dwmapi") as mock:
            win32.disable_window_transitions(4242)

        hwnd, attribute, value_ref, size = mock.DwmSetWindowAttribute.call_args.args
        assert (hwnd, attribute) == (4242, 3)  # DWMWA_TRANSITIONS_FORCEDISABLED
        assert value_ref._obj.value == 1  # TRUE
        assert size == ctypes.sizeof(ctypes.wintypes.BOOL)


class TestIsWindowMinimized:
    def test_an_iconic_window_reads_as_minimized(self):
        with patch("fun_time.win32._user32") as mock:
            mock.IsIconic.return_value = 1
            assert win32.is_window_minimized(4242) is True

    def test_anything_else_does_not(self):
        with patch("fun_time.win32._user32") as mock:
            mock.IsIconic.return_value = 0
            assert win32.is_window_minimized(4242) is False


class TestTheWindowChromeThisProcessGivesItsOwn:
    """Three calls the dashboard used to make raw, on its OWN window.

    None goes through :func:`_without_hanging`, and must not: the send would go
    to this process's UI thread, which is the thread that would be waiting.
    """

    def test_the_taskbar_pass_keeps_minimize_and_close_and_drops_maximize(self):
        with patch("fun_time.win32._user32") as mock:
            mock.GetWindowLongW.return_value = 0
            win32.set_taskbar_window_styles(4242)

        style = next(c for c in mock.SetWindowLongW.call_args_list
                     if c.args[1] == win32.GWL_STYLE).args[2]
        assert style & win32.WS_SYSMENU
        assert style & win32.WS_MINIMIZEBOX
        assert not style & win32.WS_MAXIMIZEBOX

    def test_the_taskbar_pass_claims_a_taskbar_button(self):
        """WS_EX_APPWINDOW on and WS_EX_TOOLWINDOW off is what puts the panel
        in the taskbar at all."""
        with patch("fun_time.win32._user32") as mock:
            mock.GetWindowLongW.return_value = win32.WS_EX_TOOLWINDOW
            win32.set_taskbar_window_styles(4242)

        ex_style = next(c for c in mock.SetWindowLongW.call_args_list
                        if c.args[1] == win32.GWL_EXSTYLE).args[2]
        assert ex_style & win32.WS_EX_APPWINDOW
        assert not ex_style & win32.WS_EX_TOOLWINDOW

    def test_the_taskbar_pass_keeps_the_bits_already_on_the_window(self):
        """Read-modify-write: the pass adds and removes its own bits and leaves
        every other one the window came with."""
        other = 0x00000100
        with patch("fun_time.win32._user32") as mock:
            mock.GetWindowLongW.return_value = other
            win32.set_taskbar_window_styles(4242)

        assert all(c.args[2] & other for c in mock.SetWindowLongW.call_args_list)

    def test_the_taskbar_pass_asks_the_frame_to_be_recalculated(self):
        """A style set with no SWP_FRAMECHANGED is a style Windows has not
        drawn yet."""
        with patch("fun_time.win32._user32") as mock:
            mock.GetWindowLongW.return_value = 0
            win32.set_taskbar_window_styles(4242)

        flags = mock.SetWindowPos.call_args.args[6]
        assert flags & win32.SWP_FRAMECHANGED
        assert flags & win32.SWP_NOSIZE
        assert flags & win32.SWP_NOMOVE
        assert flags & win32.SWP_NOZORDER

    def test_showing_and_hiding_an_own_window(self):
        with patch("fun_time.win32._user32") as mock:
            win32.hide_own_window(4242)
            win32.show_own_window(4242)

        assert mock.ShowWindow.call_args_list == [
            call(4242, win32.SW_HIDE), call(4242, win32.SW_SHOW)]

    def test_inserting_below_another_window_names_it_as_a_pointer(self):
        """A bare int is marshalled as a 32-bit c_int, which truncates a 64-bit
        handle — the window would land somewhere else in the band, or nowhere."""
        with patch("fun_time.win32._user32") as mock:
            win32.insert_below(4242, 99)

        hwnd, insert_after, _x, _y, _cx, _cy, flags = mock.SetWindowPos.call_args.args
        assert hwnd == 4242
        assert isinstance(insert_after, ctypes.c_void_p)
        assert insert_after.value == 99
        assert flags & win32.SWP_NOACTIVATE
        assert not flags & win32.SWP_NOZORDER

    def test_inserting_below_nothing_leaves_the_z_order_alone(self):
        """With no window to sit under there is nothing to place against, so
        the call keeps its other work and asks for no move in the band."""
        with patch("fun_time.win32._user32") as mock:
            win32.insert_below(4242, 0)

        flags = mock.SetWindowPos.call_args.args[6]
        assert flags & win32.SWP_NOZORDER
        assert not flags & win32.SWP_NOACTIVATE

    def test_an_insert_moves_and_resizes_nothing(self):
        with patch("fun_time.win32._user32") as mock:
            win32.insert_below(4242, 99)

        flags = mock.SetWindowPos.call_args.args[6]
        assert flags & win32.SWP_NOSIZE
        assert flags & win32.SWP_NOMOVE
        assert flags & win32.SWP_FRAMECHANGED

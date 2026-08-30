"""The live windows behind the managed roles.

These pin :class:`fun_time.role_windows.WindowRoles` directly, which is the
point of it having been lifted out of the dispatch loop: the object needs the
child pids, the browser's hwnd and the startup seed, and nothing else — no
config, no session state, no runner.
"""
from __future__ import annotations

from unittest.mock import patch

from fun_time.role_windows import ChildPids, WindowRoles
from fun_time.windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)
from tests.role_window_fakes import (
    DASHBOARD_PID,
    HOSTED_HWND,
    HOSTED_PID,
    LANDSCAPE_HWND,
    LANDSCAPE_PID,
    NAU_HWND,
    NAU_PID,
    PORTRAIT_HWND,
    PORTRAIT_PID,
    lookup_hosted,
)


def make_windows(**overrides) -> WindowRoles:
    pids = dict(nau=NAU_PID, portrait=PORTRAIT_PID,
                landscape=LANDSCAPE_PID, dashboard=DASHBOARD_PID)
    pids.update(overrides.pop("pids", {}))
    return WindowRoles(pids=ChildPids(**pids), **overrides)


def test_the_windows_object_needs_only_the_pids_and_the_startup_seed():
    """Startup resolves every window while it is still visible and hands the
    map over; hidden windows are invisible to the pid/title lookups, so that
    seed is the only way back to one.  Nothing else is needed to answer for a
    role, which is what lets these windows be reasoned about on their own."""
    windows = WindowRoles(pids=ChildPids(nau=NAU_PID), role_hwnds={"nau": NAU_HWND})

    assert windows.hwnd("nau") == NAU_HWND


class TestResolveRole:
    def test_nau_falls_back_to_exact_title_when_pid_fails(self):
        """The venv pythonw launcher's PID differs from the interpreter that
        owns the SDL window, so resolution must fall back to an exact-title
        lookup — exact because 'Nau' is a substring of 'Genau'."""
        windows = make_windows()

        title_calls: list[tuple[str, bool]] = []

        def title_lookup(title, exact=False):
            title_calls.append((title, exact))
            return 2002 if (title == "Nau" and exact) else 0

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=title_lookup):
            hwnd = windows.hwnd("nau")

        assert ("Nau", True) in title_calls, "must try the exact-title fallback"
        assert hwnd == 2002

    def test_dashboard_falls_back_to_title_when_pid_fails(self):
        """When find_window_by_pid cannot find the Dashboard (PID mismatch
        from the venv launcher), resolution falls back to its title."""
        windows = make_windows()

        def title_lookup(title, exact=False):
            return 9999 if title == "Fun Time" else 0

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=title_lookup):
            assert windows.hwnd("dashboard") == 9999

    def test_each_satellite_falls_back_to_its_own_exact_caption(self):
        """The recorded satellite pids are the venv launcher's, not the
        interpreter that owns the SDL window, so on a cold cache the by-pid
        lookup finds nothing and resolution falls back to the caption — each
        side's own, exactly.  The captions differ only in their first word,
        so a swapped or substring lookup here assigns one side's window to
        the other, which is the portrait/landscape visual swap."""
        windows = make_windows()

        def title_lookup(title, exact=False):
            if not exact:
                return 0
            return {SATELLITE_PORTRAIT_TITLE: PORTRAIT_HWND,
                    SATELLITE_LANDSCAPE_TITLE: LANDSCAPE_HWND}.get(title, 0)

        with patch("fun_time.role_windows.find_window_by_pid", return_value=0), \
             patch("fun_time.role_windows.find_window_by_title", side_effect=title_lookup):
            assert windows.hwnd("portrait") == PORTRAIT_HWND
            assert windows.hwnd("landscape") == LANDSCAPE_HWND

    def test_a_dead_hosted_window_handle_is_dropped_and_re_resolved(self):
        """The hosted app's boot can put a short-lived twin of its caption up
        first (its splash); caching that handle would aim every later restore
        at a dead window — the mode switch that visibly does nothing.  Only
        this role heals its cache, so a dead handle must be re-resolved."""
        windows = make_windows(pids={"origenerator": HOSTED_PID})

        with patch("fun_time.role_windows.find_window_for_process",
                   return_value=8888):
            assert windows.hwnd("origenerator") == 8888  # the splash, cached

        with patch("fun_time.role_windows.window_exists", return_value=False), \
             patch("fun_time.role_windows.find_window_for_process",
                   side_effect=lookup_hosted):
            assert windows.hwnd("origenerator") == HOSTED_HWND

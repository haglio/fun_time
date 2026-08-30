"""The live windows behind the managed roles.

:mod:`fun_time.window_roles` says which band a role belongs in — pure policy,
no window in sight.  This module holds the other half: the actual HWNDs the
session's children put on screen, how they are found, and the bookkeeping that
remembers which ones have been put away.

One object per session, shared by every thread that touches a window: the
dispatch loop's tick and the library browser's own thread both resolve roles
through the same instance, so the cache below is the one they share.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .window_roles import ORIGENERATOR_ROLE_TITLES
from .win32 import (
    find_window_by_pid,
    find_window_by_title,
    find_window_for_process,
    window_exists,
)
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChildPids:
    """The launched children whose windows the session manages.

    Every one of them is a process id the startup sequencer recorded, and each
    is 0 for a session that did not launch that child — an origenerator-less
    session, a dashboard-less one.
    """

    nau: int = 0
    portrait: int = 0
    landscape: int = 0
    dashboard: int = 0
    origenerator: int = 0


class WindowRoles:
    """Resolves a managed role to the window that is playing it."""

    def __init__(
        self,
        *,
        pids: ChildPids,
        rfb_hwnd: int = 0,
        role_hwnds: dict[str, int] | None = None,
    ) -> None:
        self.pids = pids
        self.rfb_hwnd = rfb_hwnd
        # Seeded from the startup sequencer, which resolved every window
        # while it was still visible — startup then hides the inactive
        # main-slot windows, and hidden windows are invisible to the
        # pid/title lookups.
        self._role_hwnds: dict[str, int] = dict(role_hwnds or {})

    def hwnd(self, role: str) -> int:
        """HWND for a managed window role, cached on first sight.

        Hidden windows are invisible to the pid/title lookups, so a
        window's HWND must be captured while it is visible (startup shows
        everything) and reused to show it again later.
        """
        if role in ("origenerator_portrait", "origenerator_landscape"):
            # The region shows come and go with the slideshows, so a cached
            # handle would name a destroyed window — resolved fresh every time.
            # find_window_for_process: the recorded pid can be a launcher's,
            # with the interpreter that owns the windows one child down.
            return find_window_for_process(
                self.pids.origenerator, ORIGENERATOR_ROLE_TITLES[role])
        hwnd = self._role_hwnds.get(role, 0)
        if hwnd and role == "origenerator" and not window_exists(hwnd):
            # The hosted app's boot can put a short-lived twin of this caption
            # up first (its splash), and caching that leaves every later
            # restore aimed at a dead handle — the switch that visibly did
            # nothing.  Only this role heals its cache: the other windows live
            # as long as the session, and their hidden phases (SW_HIDE behind
            # the overlay) are exactly when a re-resolve would come up empty.
            self._role_hwnds.pop(role, None)
            hwnd = 0
        if hwnd:
            return hwnd
        if role == "genau":
            hwnd = find_window_by_title("Genau")
        elif role == "nau":
            # The venv pythonw launcher's PID differs from the interpreter
            # that owns the SDL window, so fall back to the exact window
            # title (exact: "Nau" is a substring of "Genau").
            hwnd = find_window_by_pid(self.pids.nau) or find_window_by_title("Nau", exact=True)
        elif role == "portrait":
            # By title as well as pid, like Nau: the recorded pid is the venv
            # launcher's, not the interpreter that owns the SDL window, so on a
            # cold cache the by-pid lookup alone finds nothing and every band
            # operation silently skips the player.
            hwnd = (find_window_by_pid(self.pids.portrait)
                    or find_window_by_title(SATELLITE_PORTRAIT_TITLE, exact=True))
        elif role == "landscape":
            hwnd = (find_window_by_pid(self.pids.landscape)
                    or find_window_by_title(SATELLITE_LANDSCAPE_TITLE, exact=True))
        elif role == "dashboard":
            hwnd = self._dashboard_hwnd()
        elif role == "rfb":
            hwnd = self.rfb_hwnd
        elif role == "origenerator":
            # Pid AND title: the process owns three titled windows, and a
            # standalone Origenerator of his owns windows with the same titles.
            # Children included, for a recorded pid that is a launcher's.
            hwnd = find_window_for_process(
                self.pids.origenerator, ORIGENERATOR_ROLE_TITLES[role])
        if hwnd:
            self._role_hwnds[role] = hwnd
        return hwnd

    def _dashboard_hwnd(self) -> int:
        """Find the Dashboard window, falling back to title search.

        The PID-based lookup can fail if the venv launcher's PID differs
        from the actual Python interpreter process that owns the Qt window.
        """
        hwnd = find_window_by_pid(self.pids.dashboard) if self.pids.dashboard else 0
        if not hwnd:
            hwnd = find_window_by_title("Fun Time", exact=True)
            if hwnd:
                logger.info(
                    "Dashboard found by title (hwnd=%d) but NOT by pid %d",
                    hwnd, self.pids.dashboard,
                )
        return hwnd

"""The live windows behind the managed roles.

:mod:`fun_time.window_roles` says which band a role belongs in — pure policy,
no window in sight.  This module holds the other half: the HWNDs the session's
children put on screen, how they are found, and which ones have been put away.
One instance per session, shared by the dispatch tick and the library browser's
own thread, so the cache below is the one cache both of them read.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .window_roles import ORIGENERATOR_ROLE_TITLES
from .win32 import (
    activate_window,
    find_window_by_pid,
    find_window_by_title,
    find_window_for_process,
    minimize_window,
    restore_window,
    window_exists,
)
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)

logger = logging.getLogger(__name__)


# How long the outgoing main-slot player keeps its window before it is
# minimized, so the DISPLAY_OFF it was sent in the same breath is on screen
# first (see :meth:`WindowRoles.hide_after_settle`).  Generous next to the two
# frames the player needs to read the verb and present the black — time nobody
# can see, and being early is the failure it exists to avoid.
PRIMARY_BLANK_SETTLE_S = 0.25


@dataclass(frozen=True)
class ChildPids:
    """The launched children whose windows the session manages: a pid the
    startup sequencer recorded, or 0 for a child this session never launched."""

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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.pids = pids
        self.rfb_hwnd = rfb_hwnd
        # Seeded from the startup sequencer, which resolved every window
        # while it was still visible — startup then hides the inactive
        # main-slot windows, and hidden windows are invisible to the
        # pid/title lookups.
        self._role_hwnds: dict[str, int] = dict(role_hwnds or {})
        # The settle a mode switch's outgoing player waits out is the one thing
        # here that is about elapsed time.  Injectable so a test can let it run
        # out rather than reach in and back-date the deadline.
        self._clock = clock
        # Main-slot windows waiting out PRIMARY_BLANK_SETTLE_S before they are
        # minimized, by role -> the time they are due.
        self._pending_hides: dict[str, float] = {}
        # Windows a player's own minimize button parked, kept apart from the two
        # other things that minimize around here: the mode switch parks the idle
        # main-slot player (undone by the switch that brings it back) and
        # omniminimize parks the room (undone by omnirestore).  These are undone
        # by the room resuming — a player parked from its own HUD took that HUD
        # down with it and has no button left to press.
        self._parked_hwnds: list[int] = []
        self._minimized_hwnds: list[int] = []

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
        # The three SDL players are looked up by pid AND by caption: the pid on
        # record is the venv pythonw launcher's, not the interpreter that owns
        # the window, so on a cold cache by-pid alone finds nothing and every
        # band operation silently skips the player.  The captions are matched
        # exactly — "Nau" is a substring of "Genau", and the two satellites'
        # differ only in their first word, so a substring match hands one side's
        # window to the other (the portrait/landscape visual swap).
        elif role == "nau":
            hwnd = find_window_by_pid(self.pids.nau) or find_window_by_title("Nau", exact=True)
        elif role == "portrait":
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
        """Find the Dashboard window, falling back to its caption — the venv
        launcher's pid differs from the interpreter that owns the Qt window the
        same way it does for the SDL players above."""
        hwnd = find_window_by_pid(self.pids.dashboard) if self.pids.dashboard else 0
        if not hwnd:
            hwnd = find_window_by_title("Fun Time", exact=True)
            if hwnd:
                logger.info(
                    "Dashboard found by title (hwnd=%d) but NOT by pid %d",
                    hwnd, self.pids.dashboard,
                )
        return hwnd

    # -- putting windows down and bringing them back ------------------------

    def show(self, role: str) -> None:
        """Bring a role's window back — the mode switch's incoming player.

        Restore rather than SW_SHOW: the idle main-slot player is parked by
        minimizing it (which keeps its taskbar button), so bringing it back is a
        restore.  No-activate — focus is :meth:`activate`'s business — and DWM
        transitions are disabled, so it is instant.  A switch straight back
        cancels the settle: the window being restored must not be minimized a
        moment later.
        """
        self._pending_hides.pop(role, None)
        hwnd = self.hwnd(role)
        if hwnd:
            restore_window(hwnd, activate=False)

    def activate(self, role: str) -> None:
        """Hand a role's window the foreground."""
        hwnd = self.hwnd(role)
        if hwnd:
            activate_window(hwnd)

    def hide_after_settle(self, role: str) -> None:
        """Park the main-slot player a mode switch is leaving — after a beat.

        Only that pair is ever hidden (see ``_main_slot_ops``), and only they
        need the beat.  Minimizing is what FREEZES a window's Alt-Tab thumbnail:
        Windows stops compositing a minimized window, so whatever it last drew is
        what the thumbnail keeps showing until it is restored.  The same switch
        has just told this player to go dark (DISPLAY_OFF), and reading that verb
        and presenting the black costs it a frame or two — minimize inside that
        gap and the thumbnail keeps the video frame the player was sitting on,
        which is the exact thing the blanking exists to prevent.

        Nothing shows during the wait: the incoming player has already been
        restored, activated and promoted over the same rect, and this one has
        been demoted out of the topmost band (see :meth:`restack_main_slot`).
        """
        self._pending_hides[role] = self._clock() + PRIMARY_BLANK_SETTLE_S

    def flush_pending_hides(self) -> None:
        """Park each main-slot window whose settle time has run out."""
        if not self._pending_hides:
            return
        now = self._clock()
        for role in [r for r, due in self._pending_hides.items() if now >= due]:
            del self._pending_hides[role]
            self.minimize(role)

    def minimize(self, role: str) -> None:
        # Minimize instead of SW_HIDE so the window keeps its taskbar button
        # (running indicator) the whole session — and, for a satellite parked
        # from its own HUD, so there is something left to click: that panel goes
        # down with the window, so the taskbar button is the way back.
        # No-activate, so parking one player never yanks focus to the next.
        hwnd = self.hwnd(role)
        if hwnd:
            minimize_window(hwnd, activate=False)

    def park(self, role: str) -> None:
        """Minimize a window the user asked to have out of the way, and remember it.

        Remembered here rather than inside :meth:`minimize`, which the mode
        switch also calls: the slot-mate it parks is the mode's business and comes
        back when the mode brings it back, so putting it on this list would have
        the next resume drag a hidden player onto a rect another one is using.
        """
        hwnd = self.hwnd(role)
        if not hwnd:
            return
        self.minimize(role)
        if hwnd not in self._parked_hwnds:
            self._parked_hwnds.append(hwnd)

    def restore_parked(self) -> None:
        """Bring back every window a minimize button parked — leaving OmniPause.

        Each returns to the rect and size it had, which is its slot: Windows keeps
        a minimized window's restored placement, and nothing moved it meanwhile.
        The topmost bands are re-applied right after by the ``restore_all_topmost``
        op that follows this one, so a window comes back into the same band it
        left as well as the same place.
        """
        for hwnd in self._parked_hwnds:
            restore_window(hwnd, activate=False)
        self._parked_hwnds = []

    def minimize_all(self, roles: Iterable[str]) -> None:
        """Minimize the windows the current mode shows — the "omniminimize" command.

        Only mode-visible windows are minimized (SW_MINIMIZE would drag a
        hidden slot-mate back into view), each with ``activate=False`` so
        minimizing one never yanks focus to the next.  The minimized set is
        remembered so :meth:`restore_minimized` brings back exactly these.
        """
        self._minimized_hwnds = []
        for role in roles:
            hwnd = self.hwnd(role)
            if hwnd:
                minimize_window(hwnd, activate=False)
                self._minimized_hwnds.append(hwnd)

    def restore_minimized(self) -> None:
        """Un-minimize exactly the windows :meth:`minimize_all` minimized.

        That set is every window the mode had on screen, so it covers any that a
        HUD button had already parked — they are up again, and the parked list is
        dropped so a later resume does not "restore" windows already restored.
        """
        for hwnd in self._minimized_hwnds:
            restore_window(hwnd, activate=False)
        self._minimized_hwnds = []
        self._parked_hwnds = []

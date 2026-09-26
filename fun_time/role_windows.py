"""The live windows under the managed roles.

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

from .mode_plan import main_player_displays
from .satellites_mode import origenerator_shows
from .win32 import (
    activate_window,
    find_window_by_pid,
    find_window_by_title,
    find_window_for_process,
    is_window_minimized,
    is_window_topmost,
    minimize_window,
    place_beneath,
    place_window,
    restore_window,
    set_always_on_top,
    window_exists,
    window_rect,
)
from .window_layout import SecondaryMonitorRects, WindowRect
from .window_roles import (
    FIXED_TOPMOST_ROLES,
    GENAU_TITLES,
    MANAGED_ROLES,
    ORIGENERATOR_ROLE,
    ORIGENERATOR_TITLE,
    role_topmost,
)
from .windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)

logger = logging.getLogger(__name__)


# How long a mode switch gives a main-slot player to act on its display verb: the
# outgoing one keeps its window that long before it is minimized (see
# :meth:`WindowRoles.hide_after_settle`), and Genau stays opaque that long over the
# incoming main player (see ``MainSlotHandover``).  Generous next to the two
# frames a player needs; being early is the failure it exists to avoid.
MAIN_BLANK_SETTLE_S = 0.25


@dataclass(frozen=True)
class ChildPids:
    """The launched children whose windows the session manages: a pid the
    startup sequencer recorded, or 0 for a child this session never launched."""

    main_player: int = 0
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
        self.clock = clock
        # Main-slot windows waiting out MAIN_BLANK_SETTLE_S before they are
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
        hwnd = self._role_hwnds.get(role, 0)
        if hwnd and role == ORIGENERATOR_ROLE and not window_exists(hwnd):
            # The hosted app's boot can put a short-lived twin of this caption
            # up first (its splash), and caching that leaves every later
            # restore aimed at a dead handle — the switch that visibly did
            # nothing.  Only this role heals its cache: the other windows live
            # as long as the session, and their hidden phases (SW_HIDE under
            # the overlay) are exactly when a re-resolve would come up empty.
            self._role_hwnds.pop(role, None)
            hwnd = 0
        if hwnd:
            return hwnd
        if role == "genau":
            # Either caption, each matched exactly: this window renames
            # itself when its HUD goes over the main player's video.
            hwnd = next((found for title in GENAU_TITLES
                         if (found := find_window_by_title(title, exact=True))), 0)
        # The three SDL players are looked up by pid AND by caption: the pid on
        # record is the venv pythonw launcher's, not the interpreter that owns
        # the window, so on a cold cache by-pid alone finds nothing and every
        # band operation silently skips the player.  The captions are matched
        # exactly, the way every caption this session resolves is: a substring
        # lookup answers with whatever window it reaches first whose title
        # merely CONTAINS the name (see find_window_by_title), and handing one
        # side's window to the other is the portrait/landscape visual swap.
        elif role == "main_player":
            hwnd = find_window_by_pid(self.pids.main_player) or find_window_by_title("Main Player", exact=True)
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
        elif role == ORIGENERATOR_ROLE:
            # Pid AND title: a standalone Origenerator of his owns a window
            # with the same caption.  Children included, for a recorded pid
            # that is a launcher's.
            hwnd = find_window_for_process(
                self.pids.origenerator, ORIGENERATOR_TITLE, include_hidden=True)
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

        Only that pair is ever hidden (see ``MainSlotHandover``), and only they
        need the beat.  Minimizing is what FREEZES a window's Alt-Tab thumbnail:
        Windows stops compositing a minimized window, so whatever it last drew is
        what the thumbnail keeps showing until it is restored.  The handover
        has just told this player to go dark (DISPLAY_OFF), and reading that verb
        and presenting the black costs it a frame or two — minimize inside that
        gap and the thumbnail keeps the video frame the player was sitting on,
        which is the exact thing the blanking exists to prevent.

        Nothing shows during the wait: the incoming player already covers the
        same rect, and this one has been demoted out of the topmost band (see
        :meth:`restack_main_slot`).
        """
        self._pending_hides[role] = self.clock() + MAIN_BLANK_SETTLE_S

    def flush_pending_hides(self) -> None:
        """Park each main-slot window whose settle time has run out."""
        if not self._pending_hides:
            return
        now = self.clock()
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

    def seat(self, rects: SecondaryMonitorRects) -> None:
        self.place([("portrait", rects.portrait), ("main_player", rects.main), ("genau", rects.main)])

    def place(self, placements: Iterable[tuple[str, WindowRect]]) -> None:
        moves = []
        for role, rect in placements:
            hwnd = self.hwnd(role)
            if not hwnd or is_window_minimized(hwnd):
                continue
            now = window_rect(hwnd)
            if now != (rect.x, rect.y, rect.width, rect.height):
                growth = rect.width * rect.height - (now[2] * now[3] if now else 0)
                moves.append((growth, hwnd, rect))
        for _growth, hwnd, rect in sorted(moves, key=lambda move: move[0]):
            place_window(hwnd, rect.x, rect.y, rect.width, rect.height)

    # -- the topmost bands --------------------------------------------------

    def remove_all_topmost(self) -> None:
        """Drop EVERY managed window out of the TOPMOST band (omnipause frees
        the desktop).  Dropping unconditionally — not just the normally-topmost
        roles — is what stops the main player from being stranded on top in video mode, where
        it does carry the topmost flag."""
        for role in MANAGED_ROLES:
            hwnd = self.hwnd(role)
            if hwnd:
                set_always_on_top(hwnd, False)

    def restore_all_topmost(self, main_mode: str, satellites_mode: str) -> None:
        """Re-apply the topmost bands for these modes after omnipause.

        Every role is asked the shared ``role_topmost`` policy, the fixed ones
        included.  Promoting those without asking is what flashed the Random
        Favs Browser over Origenerator on every resume: the browser shares its
        rect with the hosted app's main window and the policy already answers
        "not topmost" for it in origenerator mode, but this path put it in the
        band anyway — and ``HWND_TOPMOST`` inserts at the TOP of the band, so
        it sat above Origenerator until :meth:`restack_origenerator`, a few
        SetWindowPos calls later, promoted the host back over it.

        The hosted window then goes up (:meth:`restack_origenerator`), and the
        overlapping main player/Genau pair last (:meth:`restack_main_slot`), so Genau's
        HUD sits above the main player's video in video mode.
        """
        for role in FIXED_TOPMOST_ROLES:
            if not role_topmost(role, main_mode, satellites_mode):
                continue
            hwnd = self.hwnd(role)
            if hwnd:
                set_always_on_top(hwnd, True)
        self.restack_origenerator(main_mode, satellites_mode)
        self.restack_main_slot(main_mode)

    def restack_origenerator(self, main_mode: str, satellites_mode: str, *,
                             paused: bool = False) -> None:
        """Promote the hosted Origenerator's window above the RFB it covers.

        Only in origenerator mode — the two share one rect, and
        ``HWND_TOPMOST`` inserts at the top of the band, so promoting this one
        after the fixed roles is what stacks it on top.  In video mode it is
        parked and stays out of the band.
        """
        if paused or not role_topmost(ORIGENERATOR_ROLE, main_mode, satellites_mode):
            return
        hwnd = self.hwnd(ORIGENERATOR_ROLE)
        if hwnd:
            set_always_on_top(hwnd, True)

    def restack_main_slot(self, main_mode: str, *, paused: bool = False) -> None:
        """Re-establish the main player/Genau z-order for this mode.

        The main player and Genau share one screen rect — in video mode Genau's transparent HUD
        overlays the main player's video — so unlike every other window they OVERLAP and need
        explicit stacking.  Demote both, then promote low-to-high so the last
        promotion lands highest:

          * video mode — promote the main player, then Genau ABOVE it, so the HUD overlays
                         the video and both float above the desktop.
          * genau mode — promote Genau (the main player hidden).

        Promoting the main player before Genau is what keeps the HUD over the video.
        """
        main_player = self.hwnd("main_player")
        genau = self.hwnd("genau")
        if paused:
            if main_player and genau and main_player_displays(main_mode):
                place_beneath(main_player, genau)
            return
        for hwnd in (main_player, genau):
            if hwnd:
                set_always_on_top(hwnd, False)
        if main_player and role_topmost("main_player", main_mode):
            set_always_on_top(main_player, True)
        if genau and role_topmost("genau", main_mode):
            set_always_on_top(genau, True)

    def converge_origenerator_window(self, main_mode: str, satellites_mode: str) -> None:
        """Keep the hosted app's main window where the satellites' mode says.

        The mode-switch ops restore or park it when a command fires, but the
        window arrives mid-session — the room opens without waiting the app
        out — so this is what meets it: parked while the room is in video mode,
        and up in the other.

        Judged from the WINDOW, not from a memory of what was asked: the app's
        main thread blocks for long stretches while it boots, so a restore sent
        to it can time out through the stalled-window guard and do nothing — and a
        converger that then remembered "shown" never tried again, which left the
        window parked until the user dug it out of the taskbar.  Reading the
        minimized state each pass makes every miss retry.
        """
        if not self.pids.origenerator:
            return
        hwnd = self.hwnd(ORIGENERATOR_ROLE)
        if not hwnd:
            return  # still booting — try again next sync
        minimized = is_window_minimized(hwnd)
        if origenerator_shows(satellites_mode):
            if minimized:
                restore_window(hwnd, activate=False)
            if minimized or not is_window_topmost(hwnd):
                self.restack_origenerator(main_mode, satellites_mode)
        elif not minimized:
            minimize_window(hwnd, activate=False)

    def topmost_report(self) -> str:
        """Every managed window's resolved hwnd and topmost state, one line.

        Entering omnipause should leave EVERY window non-topmost; one still
        topmost at "post-enter" is one the drop didn't reach, and this is the
        diagnostic that names it.
        """
        parts = []
        for role in MANAGED_ROLES:
            hwnd = self.hwnd(role)
            state = is_window_topmost(hwnd) if hwnd else "n/a"
            parts.append(f"{role}={hwnd}:{state}")
        return "  ".join(parts)

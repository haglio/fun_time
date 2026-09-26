"""What drives the on-player flash: a second tail of the event log, faster than
the panel's 500ms refresh so a "Clip saved" lands promptly, drawn over the
window it concerns — from the same layout functions startup and the crown place
that window with.

The overlay is a widget, so it arrives as something to call and every rule here
runs headless.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fun_time.config import LayoutConfig
from fun_time.crown import Crown
from fun_time.event_log import EVENT_LOG_FILENAME, is_announcement, read_events
from fun_time.notice_placement import PlayerRects, notice_target_rect
from fun_time.overlay_progress import loading_cover_is_up
from fun_time.shared_state import read_shared_state
from fun_time.window_layout import ScreenLayout, screen_layout, secondary_monitor_rects


def player_rects(screens: ScreenLayout, majority: Crown = Crown.PORTRAIT) -> PlayerRects:
    """Where each notice-bearing window sits, in real screen coordinates."""
    seats = secondary_monitor_rects(screens.secondary_monitor, screens.config, majority=majority)
    return PlayerRects(
        main=seats.main,
        portrait=seats.portrait,
        landscape=screens.plan.landscape,
        dash=screens.plan.dashboard,
    )


def _screens(layout: LayoutConfig) -> ScreenLayout | None:
    """None on a headless run, where notices simply do not flash."""
    try:
        return screen_layout(layout)
    except (ValueError, OSError):
        return None


class NoticeFeed:
    """One session's notices: where they go, when they may go, how far read.

    Two directories, because the two files are in two.  *held* waits for the
    COVER, which the panel's own reveal precedes.
    """

    def __init__(
        self,
        *,
        layout: LayoutConfig,
        event_log_dir: Path,
        cover_dir: Path,
        make_overlay: Callable[[], object],
        held: bool,
        shared_state_file: Path | None = None,
    ) -> None:
        self._event_log_dir = event_log_dir
        self._cover_dir = cover_dir
        self._offset = 0
        self._held = held
        self._shared_state_file = shared_state_file
        self._screens = _screens(layout)
        self.overlay = make_overlay() if self._screens is not None else None

    @property
    def offset(self) -> int:
        """How far into the event log this tail has read."""
        return self._offset

    @property
    def player_rects(self) -> PlayerRects | None:
        if self._screens is None:
            return None
        return player_rects(self._screens, self._majority())

    def _majority(self) -> Crown:
        state = read_shared_state(self._shared_state_file) if self._shared_state_file else None
        return Crown.PORTRAIT if state is None else state.majority

    def poll(self) -> None:
        """Flash every announcement written since the last poll."""
        if self.overlay is None or self._screens is None:
            return
        if self._held:
            if loading_cover_is_up(self._cover_dir):
                return
            # Latched, so the steady state costs no file check at all.
            self._held = False
        records, self._offset = read_events(
            self._event_log_dir / EVENT_LOG_FILENAME, self._offset)
        announcements = [record for record in records if is_announcement(record)]
        if not announcements:
            return
        rects = self.player_rects
        for record in announcements:
            self.overlay.flash(record, notice_target_rect(record.source, rects))

    def shutdown(self) -> None:
        """Put the overlay down; nothing flashes after this."""
        if self.overlay is not None:
            self.overlay.shutdown()
            self.overlay = None

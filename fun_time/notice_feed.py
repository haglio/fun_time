"""What drives the on-player flash: a second tail of the event log, faster than
the panel's 500ms refresh so a "Clip saved" lands promptly, drawn over the
window it concerns — from the same two layout functions startup positioned that
window with.

The overlay is a widget, so it arrives as something to call and every rule here
runs headless.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fun_time.config import LayoutConfig
from fun_time.event_log import EVENT_LOG_FILENAME, is_announcement, read_events
from fun_time.notice_placement import PlayerRects, notice_target_rect
from fun_time.overlay_progress import loading_cover_is_up
from fun_time.window_layout import compute_main_media_rect, screen_layout


def player_rects(layout: LayoutConfig) -> PlayerRects | None:
    """Where each notice-bearing window sits, in real screen coordinates.

    From the layout functions startup positioned them with, so a notice lands ON
    its window.  None on a headless run, where notices simply do not flash.
    """
    try:
        screens = screen_layout(layout)
    except (ValueError, OSError):
        return None
    return PlayerRects(
        main=compute_main_media_rect(
            secondary_monitor=screens.secondary_monitor, layout_config=layout),
        portrait=screens.plan.portrait,
        landscape=screens.plan.landscape,
        dash=screens.plan.dashboard,
    )


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
    ) -> None:
        self._event_log_dir = event_log_dir
        self._cover_dir = cover_dir
        self._offset = 0
        self._held = held
        self.player_rects = player_rects(layout)
        self.overlay = make_overlay() if self.player_rects is not None else None

    @property
    def offset(self) -> int:
        """How far into the event log this tail has read."""
        return self._offset

    def poll(self) -> None:
        """Flash every announcement written since the last poll."""
        if self.overlay is None or self.player_rects is None:
            return
        if self._held:
            if loading_cover_is_up(self._cover_dir):
                return
            # Latched, so the steady state costs no file check at all.
            self._held = False
        records, self._offset = read_events(
            self._event_log_dir / EVENT_LOG_FILENAME, self._offset)
        for record in records:
            if is_announcement(record):
                self.overlay.flash(
                    record, notice_target_rect(record.source, self.player_rects))

    def shutdown(self) -> None:
        """Put the overlay down; nothing flashes after this."""
        if self.overlay is not None:
            self.overlay.shutdown()
            self.overlay = None

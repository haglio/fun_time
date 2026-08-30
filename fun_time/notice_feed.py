"""The announcements that flash over the player they are about.

A second tail of the session's event log, faster than the panel's 500ms refresh
so a "Clip saved" lands promptly, and with a geometry of its own: a toast is
drawn over the window it concerns, so this reads the same two layout functions
startup positioned that window with rather than a description of where it went.

Held while the loading cover is up — a toast is topmost, and one that fired in
that gap appeared for a moment through the scrim the cover is there to be.
Nothing is dropped by waiting: the read offset does not advance while held.

No Qt.  The overlay is a widget, so it arrives as something to call.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fun_time.config import LayoutConfig
from fun_time.dashboard_layout import Rect
from fun_time.event_log import EVENT_LOG_FILENAME, read_events
from fun_time.monitors import enumerate_monitors, get_logical_monitor_rects
from fun_time.notice_overlay import PlayerRects, is_announcement, notice_target_rect
from fun_time.overlay_progress import loading_cover_is_up
from fun_time.window_layout import compute_main_media_rect, compute_window_layout


def player_rects(layout: LayoutConfig) -> PlayerRects | None:
    """Where each notice-bearing window sits, in real screen coordinates.

    From the same layout functions startup positions the windows with, so the
    overlay lands ON the window rather than near it.  None when the monitors
    cannot be read (a headless run), so notices simply do not flash.
    """
    try:
        monitors = enumerate_monitors()
        primary_rect, secondary_rect = get_logical_monitor_rects(
            monitors,
            primary_index=layout.primary_monitor,
            secondary_index=layout.secondary_monitor,
        )
    except (ValueError, OSError):
        return None
    plan = compute_window_layout(
        primary_monitor=primary_rect,
        secondary_monitor=secondary_rect,
        layout_config=layout,
    )
    main = compute_main_media_rect(
        secondary_monitor=secondary_rect, layout_config=layout,
    )
    as_rect = lambda w: Rect(w.x, w.y, w.width, w.height)  # noqa: E731
    return PlayerRects(
        main=as_rect(main),
        portrait=as_rect(plan.portrait),
        landscape=as_rect(plan.landscape),
        dash=as_rect(plan.dashboard),
    )


class NoticeFeed:
    """One session's toasts: where they go, when they may go, and how far read.

    *event_log_dir* and *cover_dir* are given separately because the two files
    are not in the same place today.

    *held* starts the feed waiting for the cover.  The panel shows itself one
    phase BEFORE the cover goes, so it cannot use its own reveal as the cue.
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

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fun_time.config import LayoutConfig
from fun_time.crown import Crown
from fun_time.event_log import EVENT_LOG_FILENAME, EventRecord, is_announcement, read_events
from fun_time.message_placement import FunestraRects, message_target_rect
from fun_time.overlay_progress import loading_screen_is_up
from fun_time.shared_state import read_shared_state
from fun_time.unlogged_messages import UnloggedMessages
from fun_time.window_layout import ScreenLayout, screen_layout, secondary_monitor_rects


def funestra_rects(screens: ScreenLayout, majority: Crown = Crown.PORTRAIT) -> FunestraRects:
    seats = secondary_monitor_rects(screens.secondary_monitor, screens.config, majority=majority)
    return FunestraRects(
        main=seats.main,
        portrait=seats.portrait,
        landscape=screens.plan.landscape,
        dash=screens.plan.dashboard,
    )


def _screens(layout: LayoutConfig, rfb_mode_selector: bool) -> ScreenLayout | None:
    try:
        return screen_layout(layout, rfb_mode_selector=rfb_mode_selector)
    except (ValueError, OSError):
        return None


class MessageFeed:
    def __init__(
        self,
        *,
        layout: LayoutConfig,
        rfb_mode_selector: bool = False,
        event_log_dir: Path,
        loading_screen_dir: Path,
        make_overlay: Callable[[], object],
        held: bool,
        list_what_was_heard: Callable[[list[EventRecord]], None],
        shared_state_file: Path | None = None,
    ) -> None:
        self._event_log_dir = event_log_dir
        self._loading_screen_dir = loading_screen_dir
        self._offset = 0
        self._held = held
        self._list_what_was_heard = list_what_was_heard
        self._shared_state_file = shared_state_file
        self._screens = _screens(layout, rfb_mode_selector)
        self.overlay = make_overlay() if self._screens is not None else None
        self._unlogged = UnloggedMessages(event_log_dir) if self.overlay is not None else None

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def funestra_rects(self) -> FunestraRects | None:
        if self._screens is None:
            return None
        return funestra_rects(self._screens, self._majority())

    def _majority(self) -> Crown:
        state = read_shared_state(self._shared_state_file) if self._shared_state_file else None
        return Crown.PORTRAIT if state is None else state.majority

    def poll(self) -> None:
        if self.overlay is None or self._screens is None:
            return
        if self._held:
            if loading_screen_is_up(self._loading_screen_dir):
                return
            self._held = False
        records, self._offset = read_events(
            self._event_log_dir / EVENT_LOG_FILENAME, self._offset)
        unlogged = self._unlogged.take_all()
        if unlogged:
            self._list_what_was_heard(unlogged)
        announcements = sorted([*filter(is_announcement, records), *unlogged],
                               key=lambda record: record.ts)
        if not announcements:
            return
        rects = self.funestra_rects
        for record in announcements:
            self.overlay.flash(record, message_target_rect(record.source, rects))

    def shutdown(self) -> None:
        if self._unlogged is not None:
            self._unlogged.stop()
        if self.overlay is not None:
            self.overlay.shutdown()
            self.overlay = None

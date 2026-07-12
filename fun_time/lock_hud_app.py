"""Per-VLC lock-status HUD overlays.

Runs as its own process: ``python -m fun_time.lock_hud_app <manifest_path>``.

Draws a small always-on-top, click-through overlay in the top-left corner of
each satellite VLC (portrait and landscape) showing whether that player is
locked and thumbnails of the other clips reachable in the current video's seed
family and action group. All of *what* it shows and *where* is decided by the
framework-free helpers in :mod:`fun_time.lock_hud`; this module is only the Qt
shell that draws them and keeps each overlay's topmost band in step with
OmniPause (topmost normally, non-topmost while paused so the desktop is free).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QRect
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from shared_ui.colors import BG_PRIMARY, BORDER_PANEL, GREEN, TEXT_MUTED, TEXT_PRIMARY
from shared_ui.fonts import FONT_UI, SIZE_BODY, SIZE_TINY, make_font

from fun_time.command_dispatch import BridgeState
from fun_time.lock_hud import (
    HudAppConfig,
    HudPanel,
    build_panels,
    hud_display_state,
    load_hud_app_config,
    overlay_rect,
    panel_thumbnails,
)
from fun_time.monitors import enumerate_monitors, get_logical_monitor_rects
from fun_time.startup_progress import loading_screen_active
from fun_time.thumbnail_cache import thumbnail_for
from fun_time.vlc_actions import get_current_file_path
from fun_time.win32 import is_window_topmost, set_always_on_top
from fun_time.window_layout import compute_window_layout
from fun_time.windows_bridge_dispatch_loop import read_shared_state

OVERLAY_WIDTH = 320
OVERLAY_HEIGHT = 300
REFRESH_MS = 600
SEED_LIMIT = 6
ACTION_LIMIT = 4

_PAD = 10
_MAP_THUMB_H = 54
_MAP_GAP = 5
_BORDER_W = 2
_LOCK_BAND_H = 24
_STATUS_LINE_H = 15
_BORDER_COLOR = QColor(255, 255, 255)


def _draw_status_line(painter: QPainter, x: int, y: int, text: str, color) -> int:
    painter.setFont(make_font(FONT_UI, SIZE_TINY, bold=True))
    painter.setPen(color)
    painter.drawText(x, y + 10, text)
    return y + _STATUS_LINE_H


def _scaled(pixmap: QPixmap, height: int) -> QPixmap:
    return pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)


def paint_hud(
    painter: QPainter,
    rect: QRect,
    panel: HudPanel,
    current_thumb: QPixmap | None,
    seed_thumbs: list[QPixmap],
    action_thumbs: list[QPixmap],
) -> None:
    """Render one satellite's HUD: lock band, optional filter, then the map.

    The current clip anchors the map with a white border; its seed family runs
    right along the row and its distinct other actions run down the column, so
    stepping an action moves down and the row reloads with that action's seeds.
    """
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    background = QColor(BG_PRIMARY)
    background.setAlpha(224)
    painter.setPen(QPen(BORDER_PANEL, 1))
    painter.setBrush(background)
    painter.drawRoundedRect(QRect(rect.left(), rect.top(), rect.width() - 1, rect.height() - 1), 8, 8)

    x = rect.left() + _PAD
    y = rect.top() + _PAD

    lock_color = GREEN if panel.locked else TEXT_MUTED
    painter.setBrush(lock_color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(x, y + 2, 10, 10)
    painter.setFont(make_font(FONT_UI, SIZE_BODY, bold=True))
    painter.setPen(TEXT_PRIMARY if panel.locked else TEXT_MUTED)
    painter.drawText(x + 18, y + 11, panel.lock_label)
    y += _LOCK_BAND_H

    if panel.filter_query:
        y = _draw_status_line(painter, x, y, f"FILTER · {panel.filter_query}", TEXT_PRIMARY)

    if current_thumb is None:
        return

    right = rect.right() - _PAD
    bottom = rect.bottom() - _PAD

    corner = _scaled(current_thumb, _MAP_THUMB_H)
    painter.drawPixmap(x, y, corner)
    painter.setPen(QPen(_BORDER_COLOR, _BORDER_W))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(x, y, corner.width(), corner.height())

    # Seeds run right from the corner: the same act under other seeds.
    seed_x = x + corner.width() + _MAP_GAP
    for pixmap in seed_thumbs:
        scaled = _scaled(pixmap, _MAP_THUMB_H)
        if seed_x + scaled.width() > right:
            break
        painter.drawPixmap(seed_x, y, scaled)
        seed_x += scaled.width() + _MAP_GAP

    # Distinct other actions run down from the corner.
    action_y = y + corner.height() + _MAP_GAP
    for pixmap in action_thumbs:
        scaled = _scaled(pixmap, _MAP_THUMB_H)
        if action_y + scaled.height() > bottom:
            break
        painter.drawPixmap(x, action_y, scaled)
        action_y += scaled.height() + _MAP_GAP


def _load_pixmaps(pairs: list[tuple[str, Path]]) -> list[QPixmap]:
    pixmaps: list[QPixmap] = []
    for _path, thumb in pairs:
        pixmap = QPixmap(str(thumb))
        if not pixmap.isNull():
            pixmaps.append(pixmap)
    return pixmaps


def _load_pixmap(thumb: Path | None) -> QPixmap | None:
    if thumb is None:
        return None
    pixmap = QPixmap(str(thumb))
    return pixmap if not pixmap.isNull() else None


class HudOverlay(QWidget):
    """A frameless, click-through, always-on-top overlay for one satellite."""

    def __init__(self, side: str) -> None:
        super().__init__()
        self._panel: HudPanel | None = None
        self._current_thumb: QPixmap | None = None
        self._seed_thumbs: list[QPixmap] = []
        self._action_thumbs: list[QPixmap] = []

        self.setWindowTitle(f"Fun Time HUD ({side})")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_content(
        self,
        panel: HudPanel,
        current_thumb: QPixmap | None,
        seed_thumbs: list[QPixmap],
        action_thumbs: list[QPixmap],
    ) -> None:
        self._panel = panel
        self._current_thumb = current_thumb
        self._seed_thumbs = seed_thumbs
        self._action_thumbs = action_thumbs
        self.update()

    def sync_topmost(self, desired_topmost: bool) -> None:
        """Drive this overlay's z-order band to match, drift-corrected.

        Created WindowStaysOnTop, the overlay floats above its satellite — but
        OmniPause must free the desktop, so while paused it has to LEAVE the
        topmost band, not merely stop re-asserting it (the WS_EX_TOPMOST style
        persists otherwise and it stays glued on top). SetWindowPos runs only
        when the actual band differs from the desired one, so there is no
        flicker in the steady state and a stray Qt re-assert of the hint is
        corrected on the next refresh. Mirrors the dashboard's own
        ``_sync_own_topmost``.
        """
        if sys.platform != "win32":
            return
        hwnd = int(self.winId())
        if is_window_topmost(hwnd) != desired_topmost:
            set_always_on_top(hwnd, desired_topmost)

    def paintEvent(self, event: object) -> None:  # noqa: N802
        if self._panel is None:
            return
        painter = QPainter(self)
        try:
            paint_hud(
                painter, self.rect(), self._panel,
                self._current_thumb, self._seed_thumbs, self._action_thumbs,
            )
        finally:
            painter.end()


class LockHud:
    """Owns both overlays, positions them over the satellites, and refreshes them."""

    def __init__(self, config: HudAppConfig) -> None:
        self._config = config
        self._overlays = {"portrait": HudOverlay("portrait"), "landscape": HudOverlay("landscape")}

        monitors = enumerate_monitors()
        main_rect, secondary_rect = get_logical_monitor_rects(
            monitors,
            main_index=config.layout.main_monitor,
            secondary_index=config.layout.secondary_monitor,
        )
        plan = compute_window_layout(
            main_monitor=main_rect, secondary_monitor=secondary_rect, layout_config=config.layout
        )
        for side, vlc_rect in (("portrait", plan.portrait), ("landscape", plan.landscape)):
            rect = overlay_rect(vlc_rect, width=OVERLAY_WIDTH, height=OVERLAY_HEIGHT)
            self._overlays[side].setGeometry(rect.x, rect.y, rect.width, rect.height)

        self._timer = QTimer()
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        self.refresh()

    def refresh(self) -> None:
        # One file carries it: the locks, each satellite's filter, and whether
        # OmniPause has the floor.
        state = read_shared_state(self._config.shared_state_file) or BridgeState()
        loading = loading_screen_active(self._config.shared_state_file.parent)
        visible, desired_topmost = hud_display_state(loading, state.omni_paused)
        if not visible:
            for overlay in self._overlays.values():
                overlay.hide()
            return

        portrait_current = get_current_file_path(self._config.portrait_port, self._config.vlc_password)
        landscape_current = get_current_file_path(self._config.landscape_port, self._config.vlc_password)
        portrait_panel, landscape_panel = build_panels(
            self._config,
            portrait_current=portrait_current,
            landscape_current=landscape_current,
            portrait_locked=state.locked2,
            landscape_locked=state.locked3,
            portrait_filter=state.portrait_filter,
            landscape_filter=state.landscape_filter,
        )
        self._apply("portrait", portrait_panel, desired_topmost)
        self._apply("landscape", landscape_panel, desired_topmost)

    def _apply(self, side: str, panel: HudPanel, desired_topmost: bool) -> None:
        cache_dir = self._config.thumbnail_cache_dir
        current_thumb = _load_pixmap(thumbnail_for(panel.current, cache_dir)) if panel.current else None
        seed = _load_pixmaps(panel_thumbnails(panel.seed_siblings, cache_dir, limit=SEED_LIMIT))
        action = _load_pixmaps(panel_thumbnails(panel.action_siblings, cache_dir, limit=ACTION_LIMIT))
        overlay = self._overlays[side]
        overlay.set_content(panel, current_thumb, seed, action)
        if not overlay.isVisible():
            overlay.show()
        overlay.sync_topmost(desired_topmost)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2:
        print("Usage: python -m fun_time.lock_hud_app <manifest_path>", file=sys.stderr)
        return 2

    config = load_hud_app_config(argv[1])
    app = QApplication(argv[:1])
    hud = LockHud(config)  # noqa: F841 — kept alive for the app's lifetime
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

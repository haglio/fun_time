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
# The satellites play ~5 s clips, so the map has to track the current clip
# almost the instant it changes.  Polling this fast is cheap: get_current_file_path
# reuses a keep-alive socket per port, and _apply reloads thumbnails / repaints
# only when the panel actually changed, so an unchanged tick costs a state read.
REFRESH_MS = 150
SEED_LIMIT = 6
ACTION_LIMIT = 4

_PAD = 10
_MAP_THUMB_H = 54
_MAP_GAP = 5
_BORDER_W = 2
_LOCK_BAND_H = 24
_STATUS_LINE_H = 15
_BORDER_COLOR = QColor(255, 255, 255)
_COL_LABEL_H = 13  # header strip above the map for the "Seed N" column labels
_ROW_LABEL_W = 46  # left gutter for the action-name row labels


def _draw_status_line(painter: QPainter, x: int, y: int, text: str, color) -> int:
    painter.setFont(make_font(FONT_UI, SIZE_TINY, bold=True))
    painter.setPen(color)
    painter.drawText(x, y + 10, text)
    return y + _STATUS_LINE_H


def _scaled(pixmap: QPixmap, height: int) -> QPixmap:
    return pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)


def _draw_lock_icon(painter: QPainter, x: int, y: int, size: int) -> None:
    """A small green padlock, drawn on the current clip while its satellite is
    locked, so the lock reads at a glance in addition to the "Locked" band."""
    painter.save()
    body_h = int(size * 0.58)
    body_y = y + size - body_h
    shackle_w = max(4, int(size * 0.5))
    shackle_x = x + (size - shackle_w) // 2
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(GREEN, 2))
    # Top half of an ellipse = the shackle, sitting just above the body.
    painter.drawArc(shackle_x, y, shackle_w, (size - body_h) * 2, 0, 180 * 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(GREEN)
    painter.drawRoundedRect(x, body_y, size, body_h, 3, 3)
    painter.restore()


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

    # The map sits below a header strip (the seed-column labels) and right of a
    # gutter (the action-row labels).
    map_x = x + _ROW_LABEL_W
    map_y = y + _COL_LABEL_H
    painter.setFont(make_font(FONT_UI, SIZE_TINY, bold=True))

    def _col_label(cx: int, width: int, text: str) -> None:
        painter.setPen(TEXT_MUTED)
        painter.drawText(
            QRect(cx, y, width, _COL_LABEL_H),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, text,
        )

    def _row_label(row_y: int, height: int, text: str) -> None:
        if not text:
            return
        painter.setPen(TEXT_MUTED)
        painter.drawText(
            QRect(x, row_y, _ROW_LABEL_W - _MAP_GAP, height),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text,
        )

    corner = _scaled(current_thumb, _MAP_THUMB_H)
    painter.drawPixmap(map_x, map_y, corner)
    painter.setPen(QPen(_BORDER_COLOR, _BORDER_W))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(map_x, map_y, corner.width(), corner.height())
    _col_label(map_x, corner.width(), "Seed 1")
    _row_label(map_y, corner.height(), panel.current_action)
    if panel.locked:
        _draw_lock_icon(painter, map_x + 3, map_y + 3, 15)

    # Seeds run right from the corner: the same act under other seeds.
    seed_x = map_x + corner.width() + _MAP_GAP
    for i, pixmap in enumerate(seed_thumbs):
        scaled = _scaled(pixmap, _MAP_THUMB_H)
        if seed_x + scaled.width() > right:
            break
        painter.drawPixmap(seed_x, map_y, scaled)
        _col_label(seed_x, scaled.width(), f"Seed {i + 2}")
        seed_x += scaled.width() + _MAP_GAP

    # Distinct other actions run down from the corner, each named by its action.
    action_y = map_y + corner.height() + _MAP_GAP
    for i, pixmap in enumerate(action_thumbs):
        scaled = _scaled(pixmap, _MAP_THUMB_H)
        if action_y + scaled.height() > bottom:
            break
        painter.drawPixmap(map_x, action_y, scaled)
        label = panel.action_labels[i] if i < len(panel.action_labels) else ""
        _row_label(action_y, scaled.height(), label)
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
        """Drive this overlay's z-order band to match OmniPause.

        Created WindowStaysOnTop, the overlay floats above its satellite. Two
        directions, and they are NOT symmetric:

        - **Topmost** (normal / resumed): re-staked on *every* refresh, even
          when the overlay already carries the topmost bit. The satellite VLC
          it sits over is itself topmost and gets re-promoted to the top of the
          band on mode switches and on resume from OmniPause, which buries the
          HUD *within* the band. A drift-corrected bit check can't see "topmost
          but buried", so only an unconditional re-assert climbs it back over.
          (The dashboard can drift-correct its own topmost because nothing
          re-promotes over it; the HUD cannot.)
        - **Non-topmost** (OmniPause): the desktop must be freed, so the overlay
          leaves the band — but only once. Nothing re-buries a non-topmost
          window, so this direction is drift-corrected to avoid churning
          SetWindowPos every tick while paused.
        """
        if sys.platform != "win32":
            return
        hwnd = int(self.winId())
        if desired_topmost:
            set_always_on_top(hwnd, True)
        elif is_window_topmost(hwnd):
            set_always_on_top(hwnd, False)

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
        # The last panel drawn per side, so a fast refresh skips reloading
        # thumbnails and repainting when nothing about the map has changed.
        self._last_panels: dict[str, HudPanel | None] = {"portrait": None, "landscape": None}

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
        overlay = self._overlays[side]
        # Reload thumbnails and repaint only when the map actually changed; the
        # panel captures everything drawn, so an equal one would render
        # identically.  Topmost is re-staked every tick regardless — that is
        # what keeps the overlay above a re-promoted VLC.
        if panel != self._last_panels[side]:
            cache_dir = self._config.thumbnail_cache_dir
            current_thumb = _load_pixmap(thumbnail_for(panel.current, cache_dir)) if panel.current else None
            seed = _load_pixmaps(panel_thumbnails(panel.seed_siblings, cache_dir, limit=SEED_LIMIT))
            action = _load_pixmaps(panel_thumbnails(panel.action_siblings, cache_dir, limit=ACTION_LIMIT))
            overlay.set_content(panel, current_thumb, seed, action)
            self._last_panels[side] = panel
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

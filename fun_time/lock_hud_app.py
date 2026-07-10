"""Per-VLC lock-status HUD overlays.

Runs as its own process: ``python -m fun_time.lock_hud_app <manifest_path>``.

Draws a small always-on-top, click-through overlay in the top-left corner of
each satellite VLC (portrait and landscape) showing whether that player is
locked and thumbnails of the other clips reachable in the current video's seed
family and action group. All of *what* it shows and *where* is decided by the
framework-free helpers in :mod:`fun_time.lock_hud`; this module is only the Qt
shell that draws them and keeps itself on top.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QRect
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from shared_ui.colors import BG_PRIMARY, BORDER_PANEL, GREEN, TEXT_MUTED, TEXT_PRIMARY
from shared_ui.fonts import FONT_UI, SIZE_BODY, SIZE_TINY, make_font

from fun_time.dashboard_runtime import DashboardSnapshot, load_dashboard_snapshot
from fun_time.lock_hud import (
    HudAppConfig,
    HudPanel,
    build_panels,
    load_hud_app_config,
    overlay_rect,
    panel_thumbnails,
)
from fun_time.monitors import enumerate_monitors, get_logical_monitor_rects
from fun_time.vlc_actions import get_current_file_path
from fun_time.window_layout import compute_window_layout

OVERLAY_WIDTH = 300
OVERLAY_HEIGHT = 214
REFRESH_MS = 600
THUMBS_PER_AXIS = 4

_PAD = 10
_THUMB_H = 60
_THUMB_GAP = 6
_LOCK_BAND_H = 24
_ROW_LABEL_H = 16

_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def _draw_axis_row(
    painter: QPainter, x: int, y: int, width: int, label: str, count: int, thumbs: list[QPixmap]
) -> int:
    """Draw a "LABEL · N" header and a row of thumbnails; return the next y."""
    painter.setFont(make_font(FONT_UI, SIZE_TINY, bold=True))
    painter.setPen(TEXT_MUTED)
    painter.drawText(x, y + 10, f"{label} · {count}")

    thumb_y = y + _ROW_LABEL_H
    thumb_x = x
    for pixmap in thumbs:
        scaled = pixmap.scaledToHeight(_THUMB_H, Qt.TransformationMode.SmoothTransformation)
        if thumb_x + scaled.width() > x + width:
            break
        painter.drawPixmap(thumb_x, thumb_y, scaled)
        thumb_x += scaled.width() + _THUMB_GAP
    return thumb_y + _THUMB_H + 8


def paint_hud(
    painter: QPainter,
    rect: QRect,
    panel: HudPanel,
    seed_thumbs: list[QPixmap],
    action_thumbs: list[QPixmap],
) -> None:
    """Render one satellite's HUD — lock band over seed and action thumbnail rows."""
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

    width = rect.width() - 2 * _PAD
    y = _draw_axis_row(painter, x, y, width, "SEED", len(panel.seed_siblings), seed_thumbs)
    _draw_axis_row(painter, x, y, width, "ACTION", len(panel.action_siblings), action_thumbs)


def _load_pixmaps(pairs: list[tuple[str, Path]]) -> list[QPixmap]:
    pixmaps: list[QPixmap] = []
    for _path, thumb in pairs:
        pixmap = QPixmap(str(thumb))
        if not pixmap.isNull():
            pixmaps.append(pixmap)
    return pixmaps


class HudOverlay(QWidget):
    """A frameless, click-through, always-on-top overlay for one satellite."""

    def __init__(self, side: str) -> None:
        super().__init__()
        self._panel: HudPanel | None = None
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

    def set_content(self, panel: HudPanel, seed_thumbs: list[QPixmap], action_thumbs: list[QPixmap]) -> None:
        self._panel = panel
        self._seed_thumbs = seed_thumbs
        self._action_thumbs = action_thumbs
        self.update()

    def reassert_topmost(self) -> None:
        """Re-stake the TOPMOST band so mode-switch promotions don't bury us."""
        if sys.platform != "win32":
            return
        import ctypes

        ctypes.windll.user32.SetWindowPos(
            int(self.winId()), _HWND_TOPMOST, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
        )

    def paintEvent(self, event: object) -> None:  # noqa: N802
        if self._panel is None:
            return
        painter = QPainter(self)
        try:
            paint_hud(painter, self.rect(), self._panel, self._seed_thumbs, self._action_thumbs)
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
        snapshot = load_dashboard_snapshot(self._config.dashboard_state_file)
        if snapshot is not None and snapshot.omni_paused:
            for overlay in self._overlays.values():
                overlay.hide()
            return

        portrait_current = get_current_file_path(self._config.portrait_port, self._config.vlc_password)
        landscape_current = get_current_file_path(self._config.landscape_port, self._config.vlc_password)
        portrait_panel, landscape_panel = build_panels(
            self._config,
            portrait_current=portrait_current,
            landscape_current=landscape_current,
            portrait_locked=_side_locked(snapshot, "portrait"),
            landscape_locked=_side_locked(snapshot, "landscape"),
        )
        self._apply("portrait", portrait_panel)
        self._apply("landscape", landscape_panel)

    def _apply(self, side: str, panel: HudPanel) -> None:
        cache_dir = self._config.thumbnail_cache_dir
        seed = _load_pixmaps(panel_thumbnails(panel.seed_siblings, cache_dir, limit=THUMBS_PER_AXIS))
        action = _load_pixmaps(panel_thumbnails(panel.action_siblings, cache_dir, limit=THUMBS_PER_AXIS))
        overlay = self._overlays[side]
        overlay.set_content(panel, seed, action)
        if not overlay.isVisible():
            overlay.show()
        overlay.reassert_topmost()


def _side_locked(snapshot: DashboardSnapshot | None, side: str) -> bool:
    if snapshot is None:
        return False
    return getattr(snapshot, side).locked


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

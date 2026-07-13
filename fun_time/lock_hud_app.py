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
from collections.abc import Callable
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


_ThumbRect = tuple[int, int, int, int]  # (x, y, w, h)


def hud_thumbnail_rects(
    *,
    map_x: int,
    map_y: int,
    right: int,
    bottom: int,
    corner_size: tuple[int, int],
    seed_sizes: list[tuple[int, int]],
    action_sizes: list[tuple[int, int]],
) -> tuple[_ThumbRect, list[_ThumbRect], list[_ThumbRect]]:
    """Positioned ``(x, y, w, h)`` rects for the map's thumbnails.

    The corner sits at the origin, seeds walk right until one would cross
    *right*, actions walk down until one would cross *bottom* — each dropped
    rather than clipped, exactly as the map is drawn.  Sizes are the thumbnails'
    already-scaled dimensions.  This is the single source of the map geometry,
    so painting and (next) click hit-testing cannot drift apart.
    """
    cw, ch = corner_size
    corner = (map_x, map_y, cw, ch)
    seeds: list[_ThumbRect] = []
    seed_x = map_x + cw + _MAP_GAP
    for w, h in seed_sizes:
        if seed_x + w > right:
            break
        seeds.append((seed_x, map_y, w, h))
        seed_x += w + _MAP_GAP
    actions: list[_ThumbRect] = []
    action_y = map_y + ch + _MAP_GAP
    for w, h in action_sizes:
        if action_y + h > bottom:
            break
        actions.append((map_x, action_y, w, h))
        action_y += h + _MAP_GAP
    return corner, seeds, actions


def paint_hud(
    painter: QPainter,
    rect: QRect,
    panel: HudPanel,
    current_thumb: QPixmap | None,
    seed_thumbs: list[QPixmap],
    action_thumbs: list[QPixmap],
) -> tuple[_ThumbRect | None, list[_ThumbRect], list[_ThumbRect]]:
    """Render one satellite's HUD: lock band, optional filter, then the map.

    The current clip anchors the map with a white border; its seed family runs
    right along the row and its distinct other actions run down the column, so
    stepping an action moves down and the row reloads with that action's seeds.

    Returns the map thumbnails' positioned rects — the corner (current clip),
    then each drawn seed and action — so the caller can hit-test clicks against
    exactly what was drawn.  The corner is None when there is no map to draw.
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
        return None, [], []

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
    seeds_scaled = [_scaled(pixmap, _MAP_THUMB_H) for pixmap in seed_thumbs]
    actions_scaled = [_scaled(pixmap, _MAP_THUMB_H) for pixmap in action_thumbs]
    corner_rect, seed_rects, action_rects = hud_thumbnail_rects(
        map_x=map_x, map_y=map_y, right=right, bottom=bottom,
        corner_size=(corner.width(), corner.height()),
        seed_sizes=[(p.width(), p.height()) for p in seeds_scaled],
        action_sizes=[(p.width(), p.height()) for p in actions_scaled],
    )

    # Corner = the current clip: bordered, "Seed 1", named by its action, and
    # padlocked when locked.
    cx, cy, cw, ch = corner_rect
    painter.drawPixmap(cx, cy, corner)
    painter.setPen(QPen(_BORDER_COLOR, _BORDER_W))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(cx, cy, cw, ch)
    _col_label(cx, cw, "Seed 1")
    _row_label(cy, ch, panel.current_action)
    if panel.locked:
        _draw_lock_icon(painter, cx + 3, cy + 3, 15)

    # Seeds run right from the corner: the same act under other seeds.
    for i, (sx, sy, sw, _sh) in enumerate(seed_rects):
        painter.drawPixmap(sx, sy, seeds_scaled[i])
        _col_label(sx, sw, f"Seed {i + 2}")

    # Distinct other actions run down from the corner, each named by its action.
    for i, (ax, ay, _aw, ah) in enumerate(action_rects):
        painter.drawPixmap(ax, ay, actions_scaled[i])
        label = panel.action_labels[i] if i < len(panel.action_labels) else ""
        _row_label(ay, ah, label)

    return corner_rect, seed_rects, action_rects


def _load_pixmaps(pairs: list[tuple[str, Path]]) -> list[tuple[str, QPixmap]]:
    """(video_path, pixmap) for each readable thumbnail, keeping the video path so
    a click on the drawn thumbnail knows which clip it is."""
    loaded: list[tuple[str, QPixmap]] = []
    for path, thumb in pairs:
        pixmap = QPixmap(str(thumb))
        if not pixmap.isNull():
            loaded.append((path, pixmap))
    return loaded


def build_click_targets(
    corner_rect: _ThumbRect | None,
    seed_rects: list[_ThumbRect],
    action_rects: list[_ThumbRect],
    current_path: str,
    seed_paths: list[str],
    action_paths: list[str],
) -> list[tuple[_ThumbRect, str]]:
    """(rect, video_path) for every clickable thumbnail: the corner is the
    current clip, then each drawn seed and action zipped to its path."""
    targets: list[tuple[_ThumbRect, str]] = []
    if corner_rect is not None and current_path:
        targets.append((corner_rect, current_path))
    targets.extend(zip(seed_rects, seed_paths))
    targets.extend(zip(action_rects, action_paths))
    return targets


def hit_test_targets(targets: list[tuple[_ThumbRect, str]], px: int, py: int) -> str:
    """The video path whose rect contains ``(px, py)``, or "" if none does."""
    for (x, y, w, h), path in targets:
        if x <= px < x + w and y <= py < y + h:
            return path
    return ""


def _load_pixmap(thumb: Path | None) -> QPixmap | None:
    if thumb is None:
        return None
    pixmap = QPixmap(str(thumb))
    return pixmap if not pixmap.isNull() else None


class HudOverlay(QWidget):
    """A frameless, always-on-top overlay for one satellite.

    Clicking a thumbnail posts a "play this clip" command through
    *command_writer* — the overlay takes mouse input but never activates
    (WS_EX_NOACTIVATE), so the VLC beneath it keeps focus.
    """

    def __init__(self, side: str, command_writer: Callable[[str], None]) -> None:
        super().__init__()
        self._side = side
        self._command_writer = command_writer
        self._panel: HudPanel | None = None
        self._current_thumb: QPixmap | None = None
        self._seed_thumbs: list[QPixmap] = []
        self._action_thumbs: list[QPixmap] = []
        self._seed_paths: list[str] = []
        self._action_paths: list[str] = []
        self._click_targets: list[tuple[_ThumbRect, str]] = []
        # A single click is deferred by the double-click interval so a
        # double-click can cancel it — one posts "switch", the other "lock".
        self._pending_click_path = ""
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._fire_pending_click)

        self.setWindowTitle(f"Fun Time HUD ({side})")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            # Take clicks (no WindowTransparentForInput), but never take focus:
            # WindowDoesNotAcceptFocus sets WS_EX_NOACTIVATE, so clicking the
            # overlay does not steal activation from the satellite VLC under it.
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def set_content(
        self,
        panel: HudPanel,
        current_thumb: QPixmap | None,
        seed_thumbs: list[QPixmap],
        action_thumbs: list[QPixmap],
        seed_paths: list[str],
        action_paths: list[str],
    ) -> None:
        self._panel = panel
        self._current_thumb = current_thumb
        self._seed_thumbs = seed_thumbs
        self._action_thumbs = action_thumbs
        self._seed_paths = seed_paths
        self._action_paths = action_paths
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
            corner_rect, seed_rects, action_rects = paint_hud(
                painter, self.rect(), self._panel,
                self._current_thumb, self._seed_thumbs, self._action_thumbs,
            )
        finally:
            painter.end()
        self._click_targets = build_click_targets(
            corner_rect, seed_rects, action_rects,
            self._panel.current, self._seed_paths, self._action_paths,
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """A single click switches the satellite to the thumbnail under the
        cursor; a double-click locks it.  The single-click action waits out the
        double-click interval so a double-click cancels it — one command, not two."""
        point = event.position().toPoint()
        self._pending_click_path = hit_test_targets(self._click_targets, point.x(), point.y())
        if self._pending_click_path:
            self._click_timer.start(QApplication.doubleClickInterval())

    def _fire_pending_click(self) -> None:
        if self._pending_click_path:
            self._command_writer(f"{self._side}_play_video|{self._pending_click_path}")
            self._pending_click_path = ""

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self._click_timer.stop()
        point = event.position().toPoint()
        path = hit_test_targets(self._click_targets, point.x(), point.y())
        self._pending_click_path = ""
        if path:
            self._command_writer(f"{self._side}_lock_video|{path}")


class LockHud:
    """Owns both overlays, positions them over the satellites, and refreshes them."""

    def __init__(self, config: HudAppConfig) -> None:
        self._config = config
        self._overlays = {
            "portrait": HudOverlay("portrait", self._write_command),
            "landscape": HudOverlay("landscape", self._write_command),
        }
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
            overlay.set_content(
                panel, current_thumb,
                [pixmap for _path, pixmap in seed], [pixmap for _path, pixmap in action],
                [path for path, _pixmap in seed], [path for path, _pixmap in action],
            )
            self._last_panels[side] = panel
        if not overlay.isVisible():
            overlay.show()
        overlay.sync_topmost(desired_topmost)

    def _write_command(self, command: str) -> None:
        """Post a command for the dispatch loop — the channel a thumbnail click
        rides.  Overwrites like the dashboard's own writer; clicks are user-paced,
        so a rare collision would at worst drop one."""
        try:
            self._config.dashboard_cmd_file.parent.mkdir(parents=True, exist_ok=True)
            self._config.dashboard_cmd_file.write_text(command, encoding="utf-8")
        except OSError:
            pass


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

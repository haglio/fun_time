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
import threading
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QRect
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QToolTip, QWidget

from shared_ui.colors import BG_PRIMARY, BORDER_PANEL, GREEN, TEXT_MUTED, TEXT_PRIMARY
from shared_ui.fonts import FONT_UI, SIZE_BODY, SIZE_TINY, make_font

from fun_time.command_dispatch import BridgeState
from fun_time.filter_vocab import set_command
from fun_time.media_metadata import normalize_path_key
from fun_time.lock_hud import (
    HudAppConfig,
    HudPanel,
    build_panels,
    hud_overlays_visible,
    load_hud_app_config,
    overlay_rect,
    panel_thumbnails,
    prewarm_thumbnails,
    prime_group_indexes,
    signal_hud_ready,
)
from fun_time.monitors import enumerate_monitors, get_logical_monitor_rects
from fun_time.startup_progress import loading_screen_active
from fun_time.thumbnail_cache import thumbnail_for
from fun_time.vlc_actions import get_current_file_path
from fun_time.win32 import set_always_on_top
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
_DIM_OPACITY = 0.5  # non-playing thumbnails; the currently-playing one stays full
_COL_LABEL_H = 13  # header strip above the map for the "Seed N" column labels
_COL_LABEL_GAP = 4  # breathing room between a column label and the thumbnail under it
_ROW_LABEL_W = 62  # left gutter for the action-name row labels (fits "delta")
_LOOP_BTN = 18  # loop-button thickness (px): below the action column, right of the seed row


def _draw_status_line(painter: QPainter, x: int, y: int, text: str, color) -> int:
    painter.setFont(make_font(FONT_UI, SIZE_TINY, bold=True))
    painter.setPen(color)
    painter.drawText(x, y + 10, text)
    return y + _STATUS_LINE_H


def _scaled(pixmap: QPixmap, height: int) -> QPixmap:
    return pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)


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


def hud_loop_button_rects(
    corner_rect: _ThumbRect | None,
    seed_rects: list[_ThumbRect],
    action_rects: list[_ThumbRect],
    right: int,
    bottom: int,
) -> tuple[_ThumbRect | None, _ThumbRect | None]:
    """``(loop_action_rect, loop_seed_rect)``: a button below the action column
    and one right of the seed row — or None for either that would overflow the
    panel.  The action button loops the column, the seed button the row."""
    if corner_rect is None:
        return None, None
    cx, cy, cw, ch = corner_rect
    col_bottom = max([cy + ch] + [ay + ah for _ax, ay, _aw, ah in action_rects])
    loop_action_y = col_bottom + _MAP_GAP
    loop_action = (cx, loop_action_y, cw, _LOOP_BTN) if loop_action_y + _LOOP_BTN <= bottom else None
    row_right = max([cx + cw] + [sx + sw for sx, _sy, sw, _sh in seed_rects])
    loop_seed_x = row_right + _MAP_GAP
    loop_seed = (loop_seed_x, cy, _LOOP_BTN, ch) if loop_seed_x + _LOOP_BTN <= right else None
    return loop_action, loop_seed


def hud_expand_button_rect(
    loop_seed_rect: _ThumbRect | None, bottom: int
) -> _ThumbRect | None:
    """The "more seeds" expand button, directly under the seed-loop button — both
    act on the seed row, so it reads as "one more of these".  None when there is
    no seed-loop button or it would overflow the panel's bottom."""
    if loop_seed_rect is None:
        return None
    sx, sy, sw, sh = loop_seed_rect
    ey = sy + sh + _MAP_GAP
    if ey + _LOOP_BTN > bottom:
        return None
    return (sx, ey, sw, _LOOP_BTN)


def _draw_loop_controls(
    painter: QPainter,
    corner_rect: _ThumbRect,
    loop_action_rect: _ThumbRect | None,
    loop_seed_rect: _ThumbRect | None,
    seed_rects: list[_ThumbRect],
    action_rects: list[_ThumbRect],
    active_loop: str,
    hover_loop: str,
) -> None:
    """Draw the two loop buttons, and — while a button is hovered or its loop is
    on — a border around the videos it loops (dashed for a hover preview, solid
    green once on)."""
    cx, cy, cw, ch = corner_rect
    col_bottom = max([cy + ch] + [ay + ah for _ax, ay, _aw, ah in action_rects])
    row_right = max([cx + cw] + [sx + sw for sx, _sy, sw, _sh in seed_rects])
    boxes = {
        "action": (loop_action_rect, (cx, cy, cw, col_bottom - cy)),
        "seed": (loop_seed_rect, (cx, cy, row_right - cx, ch)),
    }
    for kind, (button, group_box) in boxes.items():
        if button is None:
            continue
        on = active_loop == kind
        bx, by, bw, bh = button
        painter.setPen(QPen(GREEN if on else TEXT_MUTED, 1))
        painter.setBrush(GREEN if on else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(bx, by, bw, bh, 3, 3)
        painter.setFont(make_font(FONT_UI, SIZE_TINY, bold=True))
        painter.setPen(QColor(BG_PRIMARY) if on else TEXT_MUTED)
        painter.drawText(QRect(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, "↻")
        if on or hover_loop == kind:
            gx, gy, gw, gh = group_box
            style = Qt.PenStyle.SolidLine if on else Qt.PenStyle.DashLine
            painter.setPen(QPen(_BORDER_COLOR, 2 if on else 1, style))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(gx, gy, gw, gh)


def _playing_cell(
    playing: str, current: str, seed_paths: list[str], action_paths: list[str]
) -> tuple[str, int]:
    """Which drawn map cell — ``("corner", 0)``, ``("seed", i)`` or
    ``("action", i)`` — holds the clip that is actually on screen, so paint can
    light it up.  Defaults to the corner (the not-looping case, where the corner
    itself is playing, and the fallback when *playing* was not thumbnailed)."""
    key = normalize_path_key
    if playing and key(playing) != key(current):
        for i, path in enumerate(seed_paths):
            if key(path) == key(playing):
                return ("seed", i)
        for i, path in enumerate(action_paths):
            if key(path) == key(playing):
                return ("action", i)
    return ("corner", 0)


# Action words that read wrong in plain title case — kept upper.
_ACTION_ACRONYMS = {"pov": "POV"}


def _friendly_action_label(name: str) -> str:
    """A row's action drawn nicely: title-cased with known acronyms upper
    ("pov gamma" → "POV Gamma"), or "(unknown)" when the clip has no action
    metadata, so the row is never a blank, invisible gutter."""
    if not name.strip():
        return "(unknown)"
    return " ".join(
        _ACTION_ACRONYMS.get(word.lower(), word[:1].upper() + word[1:].lower())
        for word in name.split()
    )


def paint_hud(
    painter: QPainter,
    rect: QRect,
    panel: HudPanel,
    current_thumb: QPixmap | None,
    seed_thumbs: list[QPixmap],
    action_thumbs: list[QPixmap],
    playing: tuple[str, int] = ("corner", 0),
) -> tuple[_ThumbRect | None, list[_ThumbRect], list[_ThumbRect]]:
    """Render one satellite's HUD: lock band, optional filter, then the map.

    The current clip anchors the map with a white border; its seed family runs
    right along the row and its distinct other actions run down the column, so
    stepping an action moves down and the row reloads with that action's seeds.

    *playing* names the cell to draw at full opacity (the rest dim) — the corner
    normally, or another cell while a loop plays a non-anchor member of the group.

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

    # The map sits below a header strip (the seed-column labels, with a little
    # gap under them) and right of a gutter (the action-row labels).
    map_x = x + _ROW_LABEL_W
    map_y = y + _COL_LABEL_H + _COL_LABEL_GAP
    painter.setFont(make_font(FONT_UI, SIZE_TINY, bold=True))

    def _col_label(cx: int, width: int, text: str) -> None:
        painter.setPen(TEXT_MUTED)
        painter.drawText(
            QRect(cx, y, width, _COL_LABEL_H),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, text,
        )

    def _row_label(row_y: int, height: int, text: str) -> None:
        # Long / two-word actions ("POV Gamma") wrap within the gutter rather
        # than clipping on the left; a missing action shows "(unknown)".
        painter.setPen(TEXT_MUTED)
        painter.drawText(
            QRect(x, row_y, _ROW_LABEL_W - _MAP_GAP, height),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
            _friendly_action_label(text),
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

    # The clip that is actually on screen is drawn at full opacity; the rest dim
    # to half, so the bright one reads as "this is what's on".  Usually that is
    # the corner, but while a loop plays a non-anchor member the bright cell moves
    # to it (the map itself stays put).  A locked single clip is ringed in white
    # (the padlock is gone); the looping borders are drawn by the overlay on top.
    play_bucket, play_index = playing
    cx, cy, cw, ch = corner_rect
    painter.setOpacity(1.0 if play_bucket == "corner" else _DIM_OPACITY)
    painter.drawPixmap(cx, cy, corner)

    for i, (sx, sy, _sw, _sh) in enumerate(seed_rects):
        painter.setOpacity(1.0 if play_bucket == "seed" and play_index == i else _DIM_OPACITY)
        painter.drawPixmap(sx, sy, seeds_scaled[i])
    for i, (ax, ay, _aw, _ah) in enumerate(action_rects):
        painter.setOpacity(1.0 if play_bucket == "action" and play_index == i else _DIM_OPACITY)
        painter.drawPixmap(ax, ay, actions_scaled[i])
    painter.setOpacity(1.0)

    if panel.locked:
        painter.setPen(QPen(_BORDER_COLOR, _BORDER_W))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(cx, cy, cw, ch)

    # Labels stay crisp (full opacity) over the dimmed thumbnails.
    _col_label(cx, cw, "Seed 1")
    _row_label(cy, ch, panel.current_action)
    for i, (sx, _sy, sw, _sh) in enumerate(seed_rects):
        _col_label(sx, sw, f"Seed {i + 2}")
    for i, (_ax, ay, _aw, ah) in enumerate(action_rects):
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
    """The value whose rect contains ``(px, py)``, or "" if none does — used for
    the thumbnail (path), loop-button (axis) and action-label (action) targets."""
    for (x, y, w, h), value in targets:
        if x <= px < x + w and y <= py < y + h:
            return value
    return ""


_LOOP_TOOLTIPS = {"action": "Loop this action column", "seed": "Loop this seed row"}
_EXPAND_TOOLTIP = "More seeds — widen the net"


def hud_button_tooltip(
    loop_targets: list[tuple[_ThumbRect, str]],
    expand_rect: _ThumbRect | None,
    px: int,
    py: int,
) -> str:
    """The tooltip for whichever HUD button is under ``(px, py)`` — the loop
    buttons or the expand button — or "" when the cursor is over neither, so the
    user can tell what each cryptic glyph does."""
    loop = hit_test_targets(loop_targets, px, py)
    if loop:
        return _LOOP_TOOLTIPS.get(loop, "")
    if expand_rect is not None:
        ex, ey, ew, eh = expand_rect
        if ex <= px < ex + ew and ey <= py < ey + eh:
            return _EXPAND_TOOLTIP
    return ""


def build_label_targets(
    corner_rect: _ThumbRect | None,
    action_rects: list[_ThumbRect],
    gutter_x: int,
    gutter_w: int,
    current_action: str,
    action_labels: list[str],
) -> list[tuple[_ThumbRect, str]]:
    """(rect, action_name) for each clickable action-name label in the left
    gutter — the corner's row is the current action, the rows below are the
    sibling actions.  Clicking one filters the satellite to that action."""
    targets: list[tuple[_ThumbRect, str]] = []
    if corner_rect is not None and current_action:
        _cx, cy, _cw, ch = corner_rect
        targets.append(((gutter_x, cy, gutter_w, ch), current_action))
    for (_ax, ay, _aw, ah), name in zip(action_rects, action_labels):
        if name:
            targets.append(((gutter_x, ay, gutter_w, ah), name))
    return targets


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
        # Loop buttons: which axis is looping ("", "action", "seed" — mutually
        # exclusive, mirrored from the shared state each refresh) and which is
        # hovered (for the preview border), plus their hit rects.
        self._active_loop = ""
        self._hover_loop = ""
        self._loop_targets: list[tuple[_ThumbRect, str]] = []
        self._label_targets: list[tuple[_ThumbRect, str]] = []
        self._expand_rect: _ThumbRect | None = None

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
        self.setMouseTracking(True)  # hover the loop buttons without a pressed button

    def set_content(
        self,
        panel: HudPanel,
        current_thumb: QPixmap | None,
        seed_thumbs: list[QPixmap],
        action_thumbs: list[QPixmap],
        seed_paths: list[str],
        action_paths: list[str],
    ) -> None:
        # The loop's on/off is authoritative from the shared state (the dispatch
        # sets it on apply and clears it on any rebuild), so mirror it here rather
        # than guessing from clip changes — auto-advance inside a loop changes the
        # clip without ending the loop, and the button must stay lit through it.
        self._active_loop = panel.active_loop
        self._panel = panel
        self._current_thumb = current_thumb
        self._seed_thumbs = seed_thumbs
        self._action_thumbs = action_thumbs
        self._seed_paths = seed_paths
        self._action_paths = action_paths
        self.update()

    def restake_topmost(self) -> None:
        """Re-assert the topmost band on every refresh — including OmniPause.

        The overlay floats above its satellite via WindowStaysOnTop, but the
        satellite VLC is itself topmost and gets re-promoted to the top of the
        band on mode switches and on resume from OmniPause, burying the HUD
        *within* the band.  A drift-corrected bit check can't see "topmost but
        buried", so we re-stake unconditionally, so the map stays legible the
        whole time it is shown.
        """
        if sys.platform != "win32":
            return
        set_always_on_top(int(self.winId()), True)

    def paintEvent(self, event: object) -> None:  # noqa: N802
        if self._panel is None:
            return
        rect = self.rect()
        painter = QPainter(self)
        try:
            playing = _playing_cell(
                self._panel.playing, self._panel.current, self._seed_paths, self._action_paths,
            )
            corner_rect, seed_rects, action_rects = paint_hud(
                painter, rect, self._panel,
                self._current_thumb, self._seed_thumbs, self._action_thumbs, playing,
            )
            loop_action_rect, loop_seed_rect = hud_loop_button_rects(
                corner_rect, seed_rects, action_rects, rect.right() - _PAD, rect.bottom() - _PAD,
            )
            expand_rect = hud_expand_button_rect(loop_seed_rect, rect.bottom() - _PAD)
            if corner_rect is not None:
                _draw_loop_controls(
                    painter, corner_rect, loop_action_rect, loop_seed_rect,
                    seed_rects, action_rects, self._active_loop, self._hover_loop,
                )
                if expand_rect is not None:
                    ex, ey, ew, eh = expand_rect
                    painter.setPen(QPen(TEXT_MUTED, 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(ex, ey, ew, eh, 3, 3)
                    painter.setFont(make_font(FONT_UI, SIZE_BODY, bold=True))
                    painter.setPen(TEXT_MUTED)
                    # A plain "+" reads clearly at 18 px where the old ⤢ glyph did
                    # not; it means "one more seed" — widen the net.
                    painter.drawText(QRect(ex, ey, ew, eh), Qt.AlignmentFlag.AlignCenter, "+")
        finally:
            painter.end()
        self._click_targets = build_click_targets(
            corner_rect, seed_rects, action_rects,
            self._panel.current, self._seed_paths, self._action_paths,
        )
        self._loop_targets = [
            (button, kind)
            for kind, button in (("action", loop_action_rect), ("seed", loop_seed_rect))
            if button is not None
        ]
        self._expand_rect = expand_rect
        self._label_targets = build_label_targets(
            corner_rect, action_rects, _PAD, _ROW_LABEL_W - _MAP_GAP,
            self._panel.current_action, self._panel.action_labels,
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """A click on a loop button toggles that loop; a click on a thumbnail
        switches to it, and a double-click locks it.  The single-click switch
        waits out the double-click interval so a double-click cancels it — one
        command, not two."""
        point = event.position().toPoint()
        loop = hit_test_targets(self._loop_targets, point.x(), point.y())
        if loop:
            self._toggle_loop(loop)
            return
        if self._expand_rect is not None:
            ex, ey, ew, eh = self._expand_rect
            if ex <= point.x() < ex + ew and ey <= point.y() < ey + eh:
                self._command_writer(f"{self._side}_more_seeds")
                return
        action = hit_test_targets(self._label_targets, point.x(), point.y())
        if action:
            self._command_writer(set_command(self._side, action.lower()))
            return
        self._pending_click_path = hit_test_targets(self._click_targets, point.x(), point.y())
        if self._pending_click_path:
            self._click_timer.start(QApplication.doubleClickInterval())

    def _toggle_loop(self, kind: str) -> None:
        """Turn *kind*'s loop on (posting action_loop/seed_loop) or, if it is
        already on, off (no_loop).  Turning one on turns the other off — the two
        loops cannot coexist — matching the command the dispatch loop runs."""
        if self._active_loop == kind:
            self._command_writer(f"{self._side}_no_loop")
            self._active_loop = ""
        else:
            self._command_writer(f"{self._side}_{kind}_loop")
            self._active_loop = kind
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        point = event.position().toPoint()
        hover = hit_test_targets(self._loop_targets, point.x(), point.y())
        if hover != self._hover_loop:
            self._hover_loop = hover
            self.update()
        tip = hud_button_tooltip(self._loop_targets, self._expand_rect, point.x(), point.y())
        if tip:
            QToolTip.showText(self.mapToGlobal(point), tip, self)
        else:
            QToolTip.hideText()

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
        # Build the group indexes up front, while the loading screen is still up,
        # so the very first map is instant instead of paying for a scan, then
        # signal ready so startup can drop the loading screen knowing the maps
        # will paint immediately rather than blank.
        prime_group_indexes(config)
        signal_hud_ready(config.ready_file)
        # Fill the thumbnail cache off the UI thread so a clip change paints from
        # cache instead of blocking on a first-use frame grab.  Daemon: it must
        # never hold the process open, and losing a half-done warm is harmless.
        threading.Thread(
            target=prewarm_thumbnails, args=(config,), daemon=True, name="hud-thumb-prewarm",
        ).start()
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
        if not hud_overlays_visible(loading):
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
            portrait_loop=state.portrait_loop,
            landscape_loop=state.landscape_loop,
        )
        self._apply("portrait", portrait_panel)
        self._apply("landscape", landscape_panel)

    def _apply(self, side: str, panel: HudPanel) -> None:
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
        overlay.restake_topmost()

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

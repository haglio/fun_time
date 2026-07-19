"""Draw the satellite's lock HUD as a bitmap mpv composites into the video.

A straight port of the HUD fun_time used to paint into its own always-on-top Qt
window.  Drawing it into the frame instead is the whole point: an mpv overlay has
no z-order, so it can neither fall behind the video nor float above the desktop —
the two failure modes the separate window kept oscillating between.

Pillow does the drawing (the same library Nau's overlays use) and the result is
handed to mpv as a BGRA array.  The layout and hit-test rects come from
:mod:`satellite.hud`, so what is drawn and what is clickable cannot drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .hud import (
    ACT_GAP,
    COL_LABEL_GAP,
    COL_LABEL_H,
    ELLIPSIS_ROOM,
    LOCK_BAND_H,
    LOOP_BTN,
    MAP_GAP,
    MAP_THUMB_H,
    MAX_GUTTER,
    MIN_GUTTER,
    PAD,
    PANEL_SIZE,
    ROW_GAP,
    STATUS_LINE_H,
    HudCell,
    HudModel,
    HudTargets,
    MapWindow,
    Rect,
    action_label_blocks,
    build_click_targets,
    build_label_targets,
    ellipsis_rects,
    expand_button_rect,
    friendly_action_label,
    loop_button_rects,
    looped_group_box,
    map_window,
    seed_column_label,
    thumbnail_rects,
)

# Palette, matching the shared_ui tokens the Qt HUD drew with (RGB).
_BG_PRIMARY = (24, 24, 24)
_BORDER_PANEL = (112, 119, 128)
_GREEN = (48, 160, 48)
_TEXT_MUTED = (120, 120, 120)
_TEXT_PRIMARY = (240, 240, 240)
_WHITE = (255, 255, 255)
_PLACEHOLDER = (48, 48, 60)

_PANEL_ALPHA = 224
_TOOLTIP_ALPHA = 240
_DIM = 0.5      # non-playing thumbnails; the one on screen stays full
_BORDER_W = 2   # the lock ring around the corner
_DOT = 1        # radius of one dot in a "…" mark — small, so three read as three
_DOT_GAP = 4    # centre-to-centre spacing of those dots along the axis

# Qt sized these fonts in points; Pillow sizes in pixels, so convert at the
# standard 96 dpi (points * 96/72) to keep the panel looking as it did.
_UI_FONT = "segoeuib.ttf"  # Segoe UI Bold — every label in the HUD is bold
# The loop (U+21BB) and expand (U+2194) glyphs on the buttons: Segoe UI has no
# U+21BB, and Pillow — unlike Qt, which fell back silently — would draw a tofu
# box.  Segoe UI Symbol covers both, so the buttons keep their icons.
_SYMBOL_FONT = "seguisym.ttf"
_SIZE_BODY = 11
_SIZE_TINY = 8
_ROW_LABEL_PT = 7
_LOOP_GLYPH = "↻"
_EXPAND_GLYPH = "↔"


def _px(points: int) -> int:
    return round(points * 4 / 3)


def _font(points: int, family: str = _UI_FONT) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(family, _px(points))
    except OSError:  # pragma: no cover — the Segoe faces ship with Windows
        return ImageFont.load_default(_px(points))


def _text_width(font: ImageFont.FreeTypeFont, text: str) -> int:
    return int(font.getlength(text))


def gutter_width_for(font: ImageFont.FreeTypeFont, current_action: str,
                     action_labels: tuple[str, ...]) -> int:
    """Size the row-label gutter to the actions actually present — wide enough for
    the widest word, no wider — so a map of short acts doesn't carry a big empty
    gutter, and a long one ("Delta") still fits without splitting."""
    words = [
        word
        for label in (current_action, *action_labels)
        for word in friendly_action_label(label).split("\n")
    ]
    widest = max((_text_width(font, word) for word in words), default=0)
    return min(max(widest + 2 * MAP_GAP, MIN_GUTTER), MAX_GUTTER)


@dataclass(frozen=True)
class RenderedHud:
    """The HUD as mpv wants it, plus what the pixels under the cursor mean."""

    bgra: np.ndarray
    targets: HudTargets


def _rgba_to_bgra(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image, dtype=np.uint8)
    return np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]], dtype=np.uint8)


def _dashed_rect(draw: ImageDraw.ImageDraw, box: Rect, color, dash: int = 4) -> None:
    """A 1px dashed outline — Pillow draws only solid lines, and the hover preview
    has to read as provisional next to the solid border a running loop gets."""
    x, y, w, h = box
    for start in range(x, x + w, dash * 2):
        end = min(start + dash, x + w)
        draw.line([(start, y), (end, y)], fill=color)
        draw.line([(start, y + h - 1), (end, y + h - 1)], fill=color)
    for start in range(y, y + h, dash * 2):
        end = min(start + dash, y + h)
        draw.line([(x, start), (x, end)], fill=color)
        draw.line([(x + w - 1, start), (x + w - 1, end)], fill=color)


class HudRenderer:
    """Paints one satellite's HUD, reusing its fonts and decoded thumbnails.

    A render happens whenever the published panel changes (every few seconds, as
    clips advance) or the cursor moves onto or off a button, so the thumbnails are
    cached by path: fun_time's cache filenames fold in the clip's mtime, so a path
    that is still valid is still the right image.
    """

    def __init__(self, side: str) -> None:
        self._side = side
        self._body = _font(_SIZE_BODY)
        self._tiny = _font(_SIZE_TINY)
        self._row = _font(_ROW_LABEL_PT)
        self._glyph = _font(_SIZE_BODY, _SYMBOL_FONT)
        self._thumbs: dict[str, Image.Image] = {}

    def _thumbnail(self, cell: HudCell) -> Image.Image:
        """*cell*'s thumbnail scaled to the map's row height, or a neutral
        placeholder shaped like this side's clips while it is still being made."""
        if cell.thumb:
            cached = self._thumbs.get(cell.thumb)
            if cached is None:
                try:
                    image = Image.open(cell.thumb).convert("RGBA")
                except OSError:
                    image = None
                if image is not None:
                    width = max(1, round(image.width * MAP_THUMB_H / max(1, image.height)))
                    cached = image.resize((width, MAP_THUMB_H))
                    self._thumbs[cell.thumb] = cached
            if cached is not None:
                return cached
        width = 30 if self._side == "portrait" else 96
        return Image.new("RGBA", (width, MAP_THUMB_H), (*_PLACEHOLDER, 255))

    def render(
        self,
        model: HudModel,
        *,
        hover_loop: str = "",
        hover_tip: str = "",
        hover_pos: tuple[int, int] = (0, 0),
    ) -> RenderedHud:
        """The panel as a BGRA bitmap plus the rects its controls occupy.

        The current clip anchors the map with a white border when locked; its seed
        family runs right along the row and its distinct other actions run down the
        column, so stepping an action moves down and the row reloads with that
        action's seeds.
        """
        width, height = PANEL_SIZE.get(model.side, PANEL_SIZE["portrait"])
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            [0, 0, width - 1, height - 1], radius=8,
            fill=(*_BG_PRIMARY, _PANEL_ALPHA), outline=(*_BORDER_PANEL, 255), width=1,
        )

        x, y = PAD, PAD
        lock_color = _GREEN if model.locked else _TEXT_MUTED
        draw.ellipse([x, y + 2, x + 10, y + 12], fill=(*lock_color, 255))
        draw.text((x + 18, y + 11), model.lock_label, font=self._body, anchor="ls",
                  fill=(*(_TEXT_PRIMARY if model.locked else _TEXT_MUTED), 255))
        y += LOCK_BAND_H

        if model.filter_query:
            draw.text((x, y + 10), f"FILTER · {model.filter_query}", font=self._tiny,
                      anchor="ls", fill=(*_TEXT_PRIMARY, 255))
            y += STATUS_LINE_H

        if model.corner is None:
            return RenderedHud(_rgba_to_bgra(image),
                               HudTargets(click=[], loop=[], label=[], expand=None))

        # Sized from the whole model, before any windowing, so the gutter does not
        # change width as a loop's window slides along.
        gutter_w = gutter_width_for(self._row, model.current_action,
                                    tuple(cell.label for cell in model.actions))
        right, bottom = width - PAD, height - PAD
        # Both axes are drawn through a window that keeps the clip on screen in view,
        # and both keep room at each end for the "…" that says the map runs on past
        # what is drawn.  That room is kept unconditionally — looping or not, more to
        # show or not — so nothing on the map ever moves: not as a window slides, not
        # when a mark appears, and not when a loop is switched on or off.
        map_x = x + gutter_w + ELLIPSIS_ROOM
        map_y = y + COL_LABEL_H + COL_LABEL_GAP + ELLIPSIS_ROOM
        # Reserve room past the map for its buttons — the seed-loop + expand
        # buttons sit right of the seed row, the action-loop button below the
        # column — so a widened row can never push them off the panel.
        map_right = right - (2 * LOOP_BTN + 2 * MAP_GAP) - ELLIPSIS_ROOM
        map_bottom = bottom - (LOOP_BTN + MAP_GAP) - ELLIPSIS_ROOM
        model, seed_win, action_win = self._window(
            model, room_x=map_right - map_x, room_y=map_bottom - map_y)

        corner_thumb = self._thumbnail(model.corner)
        seed_thumbs = [self._thumbnail(cell) for cell in model.seeds]
        action_thumbs = [self._thumbnail(cell) for cell in model.actions]
        corner_rect, seed_rects, action_rects = thumbnail_rects(
            map_x=map_x, map_y=map_y, right=map_right, bottom=map_bottom,
            corner_size=corner_thumb.size,
            seed_sizes=[thumb.size for thumb in seed_thumbs],
            action_sizes=[thumb.size for thumb in action_thumbs],
        )

        self._draw_thumbnails(image, model, corner_rect, seed_rects, action_rects,
                              corner_thumb, seed_thumbs, action_thumbs)
        if model.locked:
            cx, cy, cw, ch = corner_rect
            draw.rectangle([cx, cy, cx + cw - 1, cy + ch - 1],
                           outline=(*_WHITE, 255), width=_BORDER_W)
        self._draw_labels(image, draw, model, x, y, gutter_w,
                          corner_rect, seed_rects, action_rects,
                          seed_offset=seed_win.start if seed_win else 0)

        loop_action_rect, loop_seed_rect = loop_button_rects(
            corner_rect, seed_rects, action_rects, right, bottom,
            reserve_row=ELLIPSIS_ROOM, reserve_col=ELLIPSIS_ROOM)
        expand_rect = expand_button_rect(loop_seed_rect, right)
        self._draw_loop_controls(draw, corner_rect, loop_action_rect, loop_seed_rect,
                                 seed_rects, action_rects, model.active_loop, hover_loop)
        for axis, window in (("seed", seed_win), ("action", action_win)):
            if window is not None:
                self._draw_ellipses(draw, corner_rect, seed_rects, action_rects, axis, window)
        if expand_rect is not None:
            ex, ey, ew, eh = expand_rect
            draw.rounded_rectangle([ex, ey, ex + ew - 1, ey + eh - 1], radius=3,
                                   outline=(*_TEXT_MUTED, 255), width=1)
            # "↔" reads as expanding — the seed row widening.
            draw.text((ex + ew / 2, ey + eh / 2), _EXPAND_GLYPH, font=self._glyph,
                      anchor="mm", fill=(*_TEXT_MUTED, 255))
        if hover_tip:
            self._draw_tooltip(draw, width, height, hover_tip, hover_pos)

        targets = HudTargets(
            click=build_click_targets(corner_rect, seed_rects, action_rects,
                                      model.corner, model.seeds, model.actions),
            loop=[(button, kind)
                  for kind, button in (("action", loop_action_rect), ("seed", loop_seed_rect))
                  if button is not None],
            label=build_label_targets(corner_rect, action_rects, PAD, gutter_w - MAP_GAP,
                                      model.current_action,
                                      [cell.label for cell in model.actions]),
            expand=expand_rect,
        )
        return RenderedHud(_rgba_to_bgra(image), targets)

    def _window(
        self, model: HudModel, *, room_x: int, room_y: int
    ) -> tuple[HudModel, MapWindow | None, MapWindow | None]:
        """*model* narrowed to the cells actually drawn, plus each axis's window.

        An axis can hold far more clips than the map has room for — a loop's group
        especially.  Rather than draw the first few and leave the clip on screen off
        the map once playback moves past them (which showed as the highlight
        vanishing onto an unrecognisable video), each axis is drawn through a window
        that keeps the playing cell near the middle.  Narrowing the model here means
        everything downstream — rects, labels, hit targets, the bright cell — works
        off the drawn cells alone.
        """
        if model.corner is None:
            return model, None, None
        bucket, index = model.playing
        seed_strip = [model.corner, *model.seeds]
        action_strip = [model.corner, *model.actions]
        seed_at = index + 1 if bucket == "seed" else 0
        action_at = index + 1 if bucket == "action" else 0
        # Along the row the cells differ in width, so they are measured; down the
        # column every thumbnail is scaled to one height, so none need decoding.
        seed_win = map_window([self._thumbnail(cell).width for cell in seed_strip],
                              seed_at, room_x, gap=MAP_GAP)
        action_win = map_window([MAP_THUMB_H] * len(action_strip), action_at, room_y, gap=ROW_GAP)
        # The corner slot belongs to whichever axis the clip on screen sits on: that
        # is the only axis whose window can have moved off the corner.
        window = action_win if bucket == "action" else seed_win
        strip = action_strip if bucket == "action" else seed_strip
        if not window.count:
            return model, None, None
        corner = strip[window.start]
        lit = (action_at if bucket == "action" else seed_at) - window.start
        narrowed = replace(
            model,
            corner=corner,
            seeds=tuple(seed_strip[seed_win.start + 1:seed_win.start + seed_win.count]),
            actions=tuple(action_strip[action_win.start + 1:action_win.start + action_win.count]),
            playing=("corner", 0) if lit <= 0 else (bucket, lit - 1),
        )
        if bucket == "action":
            # A column window can open on a sibling act, so the corner's row label
            # comes from the cell drawn there rather than the anchor's own action.
            narrowed = replace(narrowed, current_action=corner.label or model.current_action)
        return narrowed, seed_win, action_win

    def _draw_ellipses(self, draw, corner_rect, seed_rects, action_rects, axis, window) -> None:
        """Three dots in the slots kept at each end of *axis*, on whichever side it
        runs on past what is drawn — along the row, down the column.  They fall inside
        a running loop's rectangle, so they read as "more of these are in the loop"
        rather than as something outside it, and a gap in from its border, so they do
        not read as part of that border either.

        Drawn rather than typed: an "…" glyph hangs off the text baseline, which in a
        slot this small puts it against the bottom edge instead of in the middle.
        """
        before, after = ellipsis_rects(corner_rect, seed_rects, action_rects, axis)
        for rect, show in ((before, window.more_before), (after, window.more_after)):
            if not show:
                continue
            bx, by, bw, bh = rect
            mx, my = bx + bw / 2, by + bh / 2
            for step in (-1, 0, 1):
                dx, dy = (step * _DOT_GAP, 0) if axis == "seed" else (0, step * _DOT_GAP)
                draw.ellipse([mx + dx - _DOT, my + dy - _DOT, mx + dx + _DOT, my + dy + _DOT],
                             fill=(*_TEXT_PRIMARY, 255))

    def _draw_thumbnails(self, image, model, corner_rect, seed_rects, action_rects,
                         corner_thumb, seed_thumbs, action_thumbs) -> None:
        """Paste the map, with only the clip actually on screen at full opacity.

        Usually that is the corner, but while a loop plays a non-anchor member the
        bright cell moves to it (the map itself stays put), so the bright one always
        reads as "this is what's on".
        """
        bucket, index = model.playing
        drawn = [(corner_rect, corner_thumb, bucket == "corner")]
        drawn += [(rect, thumb, bucket == "seed" and index == i)
                  for i, (rect, thumb) in enumerate(zip(seed_rects, seed_thumbs))]
        drawn += [(rect, thumb, bucket == "action" and index == i)
                  for i, (rect, thumb) in enumerate(zip(action_rects, action_thumbs))]
        for (rx, ry, _rw, _rh), thumb, bright in drawn:
            if not bright:
                thumb = thumb.copy()
                thumb.putalpha(thumb.getchannel("A").point(lambda a: int(a * _DIM)))
            image.alpha_composite(thumb, (rx, ry))

    def _draw_labels(self, image, draw, model, x, y, gutter_w, corner_rect, seed_rects,
                     action_rects, *, seed_offset: int = 0) -> None:
        """Column labels ("Seed N") in the header strip and action names down the
        left gutter, drawn over the (possibly dimmed) thumbnails at full opacity."""
        def column(cx: int, cw: int, text: str) -> None:
            # Clipped to its own column: a portrait map's columns are barely wider
            # than the label, and neighbouring "Seed N"s running together is
            # illegible.  Drawn into a column-sized scratch, so the overflow is cut.
            strip = Image.new("RGBA", (cw, COL_LABEL_H), (0, 0, 0, 0))
            ImageDraw.Draw(strip).text((cw / 2, COL_LABEL_H / 2), text, font=self._tiny,
                                       anchor="mm", fill=(*_TEXT_MUTED, 255))
            image.alpha_composite(strip, (cx, y))

        def row(row_y: int, row_h: int, text: str) -> None:
            # One block of tight word-lines per act, with a bigger gap between
            # acts, so a two-word act ("Motion" / "Bounce") wraps close but two acts
            # ("Alpha" then "Theta Motion") are clearly separated.
            ascent, descent = self._row.getmetrics()
            line_h = ascent + descent - 4
            blocks = action_label_blocks(text)
            total = sum(len(block) for block in blocks) * line_h + (len(blocks) - 1) * ACT_GAP
            ty = row_y + (row_h - total) // 2
            for block in blocks:
                for line in block:
                    draw.text((x + gutter_w - MAP_GAP, ty + line_h / 2), line,
                              font=self._row, anchor="rm", fill=(*_TEXT_MUTED, 255))
                    ty += line_h
                ty += ACT_GAP

        cx, cy, cw, ch = corner_rect
        # Offset by where a windowed loop opens, so the headers carry each seed's
        # real place in the family instead of restarting at one every window.
        column(cx, cw, seed_column_label(seed_offset))
        row(cy, ch, model.current_action)
        for i, (sx, _sy, sw, _sh) in enumerate(seed_rects):
            column(sx, sw, seed_column_label(seed_offset + i + 1))
        for i, (_ax, ay, _aw, ah) in enumerate(action_rects):
            row(ay, ah, model.actions[i].label if i < len(model.actions) else "")

    def _draw_loop_controls(self, draw, corner_rect, loop_action_rect, loop_seed_rect,
                            seed_rects, action_rects, active_loop, hover_loop) -> None:
        """The two loop buttons, and — while one is hovered or its loop is on — a
        border around the videos it loops (dashed for a hover preview, solid once
        on).  The border wraps the room kept for that axis's "…" marks, so the clips
        they stand for read as part of the looped set."""
        boxes = {
            kind: (
                button,
                looped_group_box(corner_rect, seed_rects, action_rects, kind,
                                 reserve=ELLIPSIS_ROOM),
            )
            for kind, button in (("action", loop_action_rect), ("seed", loop_seed_rect))
        }
        for kind, (button, group_box) in boxes.items():
            if button is None:
                continue
            on = active_loop == kind
            bx, by, bw, bh = button
            draw.rounded_rectangle(
                [bx, by, bx + bw - 1, by + bh - 1], radius=3,
                fill=(*_GREEN, 255) if on else None,
                outline=(*(_GREEN if on else _TEXT_MUTED), 255), width=1,
            )
            draw.text((bx + bw / 2, by + bh / 2), _LOOP_GLYPH, font=self._glyph,
                      anchor="mm", fill=(*(_BG_PRIMARY if on else _TEXT_MUTED), 255))
            if on:
                gx, gy, gw, gh = group_box
                draw.rectangle([gx, gy, gx + gw - 1, gy + gh - 1],
                               outline=(*_WHITE, 255), width=2)
            elif hover_loop == kind:
                _dashed_rect(draw, group_box, (*_WHITE, 255))

    def _draw_tooltip(self, draw, width, height, text, pos) -> None:
        """A tooltip box drawn inside the panel near the cursor — the HUD lives in
        the video, so there is no native tooltip to fall back on."""
        pad = 5
        ascent, descent = self._tiny.getmetrics()
        w = _text_width(self._tiny, text) + 2 * pad
        h = ascent + descent + 2 * pad
        x = max(2, min(pos[0] + 14, width - w - 2))
        y = max(2, min(pos[1] + 16, height - h - 2))
        draw.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=4,
                               fill=(*_BG_PRIMARY, _TOOLTIP_ALPHA),
                               outline=(*_BORDER_PANEL, 255), width=1)
        draw.text((x + w / 2, y + h / 2), text, font=self._tiny, anchor="mm",
                  fill=(*_TEXT_PRIMARY, 255))

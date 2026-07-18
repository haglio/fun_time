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

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .hud import (
    ACT_GAP,
    COL_LABEL_GAP,
    COL_LABEL_H,
    LOCK_BAND_H,
    LOOP_BTN,
    MAP_GAP,
    MAP_THUMB_H,
    MAX_GUTTER,
    MIN_GUTTER,
    PAD,
    PANEL_SIZE,
    STATUS_LINE_H,
    HudCell,
    HudModel,
    HudTargets,
    Rect,
    action_label_blocks,
    build_click_targets,
    build_label_targets,
    expand_button_rect,
    friendly_action_label,
    loop_button_rects,
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

        gutter_w = gutter_width_for(self._row, model.current_action,
                                    tuple(cell.label for cell in model.actions))
        right, bottom = width - PAD, height - PAD
        corner_thumb = self._thumbnail(model.corner)
        seed_thumbs = [self._thumbnail(cell) for cell in model.seeds]
        action_thumbs = [self._thumbnail(cell) for cell in model.actions]
        # Reserve room past the map for its buttons — the seed-loop + expand
        # buttons sit right of the seed row, the action-loop button below the
        # column — so a widened row can never push them off the panel.
        corner_rect, seed_rects, action_rects = thumbnail_rects(
            map_x=x + gutter_w, map_y=y + COL_LABEL_H + COL_LABEL_GAP,
            right=right - (2 * LOOP_BTN + 2 * MAP_GAP),
            bottom=bottom - (LOOP_BTN + MAP_GAP),
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
                          corner_rect, seed_rects, action_rects)

        loop_action_rect, loop_seed_rect = loop_button_rects(
            corner_rect, seed_rects, action_rects, right, bottom)
        expand_rect = expand_button_rect(loop_seed_rect, right)
        self._draw_loop_controls(draw, corner_rect, loop_action_rect, loop_seed_rect,
                                 seed_rects, action_rects, model.active_loop, hover_loop)
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
                     action_rects) -> None:
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
        column(cx, cw, "Seed 1")
        row(cy, ch, model.current_action)
        for i, (sx, _sy, sw, _sh) in enumerate(seed_rects):
            column(sx, sw, f"Seed {i + 2}")
        for i, (_ax, ay, _aw, ah) in enumerate(action_rects):
            row(ay, ah, model.actions[i].label if i < len(model.actions) else "")

    def _draw_loop_controls(self, draw, corner_rect, loop_action_rect, loop_seed_rect,
                            seed_rects, action_rects, active_loop, hover_loop) -> None:
        """The two loop buttons, and — while one is hovered or its loop is on — a
        border around the videos it loops (dashed for a hover preview, solid once
        on)."""
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

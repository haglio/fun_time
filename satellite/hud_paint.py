"""Draw the satellite's lock HUD as a bitmap mpv composites into the video.

A straight port of the HUD fun_time used to paint into its own always-on-top Qt
window.  Drawing it into the frame instead is the whole point: an mpv overlay has
no z-order, so it can neither fall behind the video nor float above the desktop —
the two failure modes the separate window kept oscillating between.

The slab it is drawn on — the rounded translucent panel, the palette, the Segoe
face sized the way Qt sized it, the BGRA hand-off — comes from
:mod:`player_core.hud_panel`, which Nau's own HUD is drawn on too, so the two
players go on looking like one another.  The layout and hit-test rects come from
:mod:`satellite.hud`, so what is drawn and what is clickable cannot drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from player_core.hud_marks import SHARED_MARK, shared_mark, shared_mark_name
from player_core.hud_panel import (
    BG_PRIMARY,
    GREEN,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WHITE,
    HudPanel,
    draw_glyph,
    draw_icon,
    draw_mark,
    draw_tooltip,
    load_font,
    text_width,
)

from .hud import (
    ACT_GAP,
    COL_LABEL_GAP,
    COL_LABEL_H,
    CTRL_BAND_H,
    ELLIPSIS_ROOM,
    FILTER_ROOM,
    MAP_BOTTOM_RESERVE,
    MAP_CELLS,
    MAP_GAP,
    MAP_RIGHT_RESERVE,
    MAP_THUMB_H,
    MAX_GUTTER,
    MIN_GUTTER,
    PAD,
    STATUS_BAND_H,
    STATUS_BASELINE,
    STATUS_DOT,
    STATUS_TEXT_X,
    SUBTITLE_GAP,
    HudCell,
    HudModel,
    HudTargets,
    MapWindow,
    Rect,
    act_is_filtered,
    action_label_blocks,
    build_click_targets,
    cell_width,
    column_anchor_rect,
    control_button_rects,
    ellipsis_rects,
    expand_button_rect,
    favorite_mark_rect,
    filter_button_rects,
    friendly_action_label,
    label_is_filtered,
    loop_button_rects,
    looped_group_box,
    map_column_height,
    map_reach,
    map_window,
    panel_height,
    panel_width,
    playing_rect,
    seed_column_label,
    thumbnail_rects,
)

_PLACEHOLDER = (48, 48, 60)  # a thumbnail fun_time has not produced yet

_DIM = 0.5      # non-playing thumbnails; the one on screen stays full
_BORDER_W = 2   # the lock ring around the held clip
_DOT = 1        # radius of one dot in a "…" mark — small, so three read as three
_DOT_GAP = 4    # center-to-center spacing of those dots along the axis
_COUNT_LINE_H = 11  # line pitch of the axis counts in the map's top-left corner

# The loop (U+21BB) and expand (U+2194) glyphs on the buttons: Segoe UI has no
# U+21BB, and Pillow — unlike Qt, which fell back silently — would draw a tofu
# box.  Segoe UI Symbol covers both, so the buttons keep their icons.
_SYMBOL_FONT = "seguisym.ttf"
_SIZE_BODY = 11
_SIZE_TINY = 8
_ROW_LABEL_PT = 7
# The family's own drawing rather than U+21BB: two arrows chasing each other
# around a rounded rectangle, which says "around and around" where a single arc
# says "back one step" -- and a single arc is what undo and reset already are.
_LOOP_GLYPH = shared_mark("loop")
_EXPAND_GLYPH = "↔"
# The side's own controls.  Skip-track for the browse pair rather than bare
# arrows, so they cannot be read as "step along the map"; a padlock and a bin for
# the two that act on the clip on screen.  Reset wears the loop's own mark run
# backwards (U+21BA against the loop's U+21BB): counterclockwise is what "put it
# back" looks like everywhere, and the two never share a band — the loop buttons
# stand on the map's edges and this one in the control row above it.  They come
# from the same symbol face as the loop glyph — Segoe UI Bold has none of them.
# The bin and the reset are the family's own drawings: the bin is the very bin
# Origenerator's toolbar wears, and reset is a gear with a circular arrow at its
# corner -- a bare counterclockwise arrow read as an undo, which is a different
# act.  Skip-track and the padlock stay typed: the family has no drawing of
# either, and the symbol face carries both cleanly.
_CONTROL_GLYPHS = {
    "prev": "⏮", "next": "⏭", "lock": "🔒",
    "trash": shared_mark("trash"), "reset": shared_mark("reset"),
}
# F-mode wears its own mark rather than a glyph: no symbol says "favorites
# only", and the mode already has a face — the pink "F" of ``fmode_icon.ico``,
# the five-by-five letter every app in this family is marked with.  A letter set
# in the body face is a thin thing beside it, reading as a caption rather than a
# badge (:func:`player_core.hud_panel.draw_icon`).
_ICON_CONTROLS = {"fmode": "F"}
_FAVORITE_GLYPH = shared_mark("star")

# The filter mark, drawn rather than typed: Segoe UI Symbol — the face the other
# buttons take their icons from — carries no funnel at any codepoint, and this is
# the one button whose shape *is* its meaning, so a ".notdef" box would say
# nothing at all.  A mouth _FUNNEL_W wide narrowing to a stem, sized like the
# glyphs beside it.
_FUNNEL_W = 9
_FUNNEL_H = 9
_FUNNEL_NECK = 3  # width of the stem the mouth narrows to

# The minimize mark, drawn for the same reason: the bar Windows puts on a title
# bar is U+E921 of Segoe MDL2 Assets, which is not the face the buttons here take
# their glyphs from, and Pillow draws a ".notdef" box for a codepoint a face does
# not carry.  Drawing it costs one rectangle and needs no font at all — and the
# bar is the one mark on this panel nobody has to be taught, since it is exactly
# what every title bar in Windows uses for the same gesture.  As wide as the
# funnel's mouth, so the two drawn marks are built to one size.
_MINIMIZE_W = 9
_MINIMIZE_H = 2


def gutter_width_for(font: ImageFont.FreeTypeFont, current_action: str,
                     action_labels: tuple[str, ...], *, min_width: int = 0) -> int:
    """Size the row-label gutter to the actions actually present — wide enough for
    the widest word and the row's filter button, no wider — so a map of short acts
    doesn't carry a big empty gutter, and a long one ("Delta") still fits without
    splitting.

    *min_width* is a floor the caller needs regardless of the acts: the axis counts
    printed in the corner above the gutter have to fit in it too.
    """
    words = [
        word
        for label in (current_action, *action_labels)
        for word in friendly_action_label(label).split("\n")
    ]
    widest = max((text_width(font, word) for word in words), default=0)
    label_w = max(widest + 2 * MAP_GAP, MIN_GUTTER)
    return min(max(label_w + FILTER_ROOM, min_width), MAX_GUTTER)


@dataclass(frozen=True)
class RenderedHud:
    """The HUD as mpv wants it, plus what the pixels under the cursor mean."""

    bgra: np.ndarray
    targets: HudTargets


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
        self._body = load_font(_SIZE_BODY)
        self._tiny = load_font(_SIZE_TINY)
        self._row = load_font(_ROW_LABEL_PT)
        self._glyph = load_font(_SIZE_BODY, _SYMBOL_FONT)
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
        return Image.new("RGBA", (cell_width(self._side), MAP_THUMB_H),
                         (*_PLACEHOLDER, 255))

    def _map_thumbnails(
        self, model: HudModel
    ) -> tuple[Image.Image | None, list[Image.Image], list[Image.Image]]:
        """The images for every cell of the windowed map — corner, seed row, action
        column — decoded once and used both to measure the panel and to paste into
        it, so what the panel was sized for is what goes in it.

        The corner is None when there is no clip yet, which is also when there is no
        row and no column.
        """
        if model.corner is None:
            return None, [], []
        return (self._thumbnail(model.corner),
                [self._thumbnail(cell) for cell in model.seeds],
                [self._thumbnail(cell) for cell in model.actions])

    def render(
        self,
        model: HudModel,
        *,
        video: str = "",
        hover_loop: str = "",
        hover_tip: str = "",
        hover_pos: tuple[int, int] = (0, 0),
    ) -> RenderedHud:
        """The panel as a BGRA bitmap plus the rects its controls occupy.

        The current clip anchors the map — its seed family runs right along the row
        and distinct other actions run down the column, so stepping an action
        moves down and the row reloads with that action's seeds.  The column hangs
        under whichever row cell is playing (fun_time builds it from that seed's
        own acts), the corner when the corner is.  A lock rings the cell being
        held in white: the corner normally, or the member a loop had reached when
        the lock was taken.

        *video* is the file on screen, named under the status line.  It comes from
        the player rather than from *model*: the published panel is fun_time's answer
        to what this side is browsing, and what is actually decoding is the player's
        own — the same split the main player draws, which names its file from its own
        session and takes the rest of its console off the wire.
        """
        # The gutter is sized from the WHOLE model, before any windowing, so it does
        # not change width as a loop's window slides along — and never narrower than
        # the axis counts printed above it.
        counts = self._count_lines(model)
        gutter_w = gutter_width_for(
            self._row, model.current_action, tuple(cell.label for cell in model.actions),
            min_width=max((text_width(self._tiny, line) for line in counts), default=0) + MAP_GAP,
        )
        # Then the map is windowed, and the panel measured around the cells that won
        # — the width from the gutter and the row's real cells, or from the status
        # line where that is the longer of the two, and the height from how many rows
        # the column has.  Measuring first and windowing into what was left is what
        # made a row of wide clips two cells instead of three.
        model, seed_win, action_win = self._window(model)
        corner_thumb, seed_thumbs, action_thumbs = self._map_thumbnails(model)
        row = ([corner_thumb.width] + [thumb.width for thumb in seed_thumbs]
               if corner_thumb is not None
               else [cell_width(model.side)] * MAP_CELLS)
        subtitle_h = (SUBTITLE_GAP + sum(self._tiny.getmetrics())) if video else 0
        # The row's reach covers the action column too: it hangs under the cell
        # ``playing`` lights, which can be partway along the row.
        reach = map_reach(row, [thumb.width for thumb in action_thumbs], model.playing)
        width = panel_width(gutter_w, reach, text_width(self._body, model.lock_label),
                            text_width(self._tiny, video))
        height = panel_height(
            map_column_height(1 + len(action_thumbs)) if corner_thumb is not None else 0,
            subtitle_h)
        panel = HudPanel(width, height)
        image, draw = panel.image, panel.draw

        x, y = PAD, PAD
        # The dot at the head of the band: white while a bare "lock" or "next" would
        # land on this side, the palette's gray otherwise.  Always drawn, never
        # hidden — an absent dot and an idle dot look the same, and then only the
        # player that *has* the floor says anything, which is half an answer.
        draw.ellipse([x, y + 2, x + STATUS_DOT, y + 2 + STATUS_DOT],
                     fill=(*(WHITE if model.active else TEXT_MUTED), 255))
        # The status itself, composed by fun_time: it already holds the lock, what is
        # looping, the browse order, F-mode and the filter, so there is nothing else
        # to lay out up here.  Drawn full-strength whatever it says — dimming it when
        # the side happened to be unlocked hid it in the case where it carries most —
        # and on one line always, in the room the panel was widened above to leave
        # it.  All five at once outruns a row of portrait clips, and the two other
        # answers to that are both worse: Pillow clips the tail away in silence,
        # which reads as the states that ran out of room being *off*, and a second
        # line reads as two states rather than one side's.
        draw.text((STATUS_TEXT_X, y + STATUS_BASELINE), model.lock_label,
                  font=self._body, anchor="ls", fill=(*TEXT_PRIMARY, 255))
        # The file on screen, muted, hung off that line's own descender so the two
        # read as one block — what the main player's console leads with, in the same
        # corner, so a glance across the screens finds each player's answer to "what
        # is this?" in the same place.  Muted because the status is what the side is
        # *doing* and the name only says which clip it is doing it to.
        if video:
            _ascent, descent = self._body.getmetrics()
            draw.text((STATUS_TEXT_X, y + STATUS_BASELINE + descent + SUBTITLE_GAP), video,
                      font=self._tiny, anchor="la", fill=(*TEXT_MUTED, 255))
        y += STATUS_BAND_H + subtitle_h

        # The side's own controls, above the map: they act on the satellite and
        # the clip on screen, not on anything the map draws, so they are laid out
        # against the panel rather than against the map — and are there whether or
        # not there is a map to draw.
        controls = control_button_rects(x, y)
        favorite = favorite_mark_rect(width - PAD, y)
        self._draw_controls(image, draw, controls, favorite, model)
        y += CTRL_BAND_H

        if model.corner is None:
            return RenderedHud(panel.to_bgra(),
                               HudTargets(click=[], loop=[], filter=[], expand=None,
                                          control=controls, favorite=favorite))

        self._draw_counts(draw, x, y, counts)
        right, bottom = width - PAD, height - PAD
        # Both axes keep room at each end for the "…" that says the map runs on past
        # what is drawn.  That room is kept unconditionally — looping or not, more to
        # show or not — so nothing on the map ever moves: not as a window slides, not
        # when a mark appears, and not when a loop is switched on or off.
        map_x = x + gutter_w + ELLIPSIS_ROOM
        map_y = y + COL_LABEL_H + COL_LABEL_GAP + ELLIPSIS_ROOM
        # The panel was measured to leave exactly this map here, so these bounds are
        # where the map already ends; they are what keeps painting and hit-testing
        # from drifting apart, not a second answer to how many cells fit.
        map_right = right - MAP_RIGHT_RESERVE - ELLIPSIS_ROOM
        map_bottom = bottom - MAP_BOTTOM_RESERVE - ELLIPSIS_ROOM
        corner_rect, seed_rects, action_rects = thumbnail_rects(
            map_x=map_x, map_y=map_y, right=map_right, bottom=map_bottom,
            corner_size=corner_thumb.size,
            seed_sizes=[thumb.size for thumb in seed_thumbs],
            action_sizes=[thumb.size for thumb in action_thumbs],
            playing=model.playing,
        )
        # The row cell the action column hangs under — the playing cell while it
        # is out along the row.  The column's own chrome (loop button, box, "…")
        # follows it.
        column_rect = column_anchor_rect(model.playing, corner_rect, seed_rects)

        self._draw_thumbnails(image, model, corner_rect, seed_rects, action_rects,
                              corner_thumb, seed_thumbs, action_thumbs)
        held = playing_rect(model.playing, corner_rect, seed_rects, action_rects)
        if model.locked and held is not None:
            hx, hy, hw, hh = held
            draw.rectangle([hx, hy, hx + hw - 1, hy + hh - 1],
                           outline=(*WHITE, 255), width=_BORDER_W)
        self._draw_labels(image, draw, model, x, y, gutter_w,
                          corner_rect, seed_rects, action_rects,
                          seed_offset=seed_win.start if seed_win else 0)
        filter_rects = filter_button_rects(corner_rect, action_rects, x,
                                           model.current_action,
                                           [cell.label for cell in model.actions])
        self._draw_filter_buttons(draw, filter_rects, model.filter_query)

        loop_action_rect, loop_seed_rect = loop_button_rects(
            corner_rect, seed_rects, action_rects, right, bottom,
            reserve_row=ELLIPSIS_ROOM, reserve_col=ELLIPSIS_ROOM, column_rect=column_rect)
        expand_rect = expand_button_rect(loop_seed_rect, right)
        self._draw_loop_controls(image, draw, corner_rect, column_rect, loop_action_rect,
                                 loop_seed_rect, seed_rects, action_rects,
                                 model.active_loop, hover_loop)
        for axis, window in (("seed", seed_win), ("action", action_win)):
            if window is not None:
                self._draw_ellipses(draw, corner_rect, column_rect, seed_rects,
                                    action_rects, axis, window)
        if expand_rect is not None:
            # "↔" reads as expanding — the seed row widening.
            self._glyph_button(image, draw, expand_rect, _EXPAND_GLYPH)
        if hover_tip:
            draw_tooltip(draw, self._tiny, hover_tip, hover_pos, (width, height))

        targets = HudTargets(
            click=build_click_targets(corner_rect, seed_rects, action_rects,
                                      model.corner, model.seeds, model.actions),
            loop=[(button, kind)
                  for kind, button in (("action", loop_action_rect), ("seed", loop_seed_rect))
                  if button is not None],
            filter=filter_rects,
            expand=expand_rect,
            control=controls,
            favorite=favorite,
        )
        return RenderedHud(panel.to_bgra(), targets)

    def _window(
        self, model: HudModel
    ) -> tuple[HudModel, MapWindow | None, MapWindow | None]:
        """*model* narrowed to the cells actually drawn, plus each axis's window.

        An axis can hold far more clips than the map draws — a loop's group
        especially.  Rather than draw the first few and leave the clip on screen off
        the map once playback moves past them (which showed as the highlight
        vanishing onto an unrecognisable video), each axis is drawn through a window
        of MAP_CELLS that keeps the playing cell near the middle.  Narrowing the
        model here means everything downstream — rects, labels, hit targets, the
        bright cell, and the panel measured around them — works off the drawn cells
        alone.
        """
        if model.corner is None:
            return model, None, None
        bucket, index = model.playing
        seed_strip = [model.corner, *model.seeds]
        action_strip = [model.corner, *model.actions]
        seed_at = index + 1 if bucket == "seed" else 0
        action_at = index + 1 if bucket == "action" else 0
        seed_win = map_window(len(seed_strip), seed_at)
        action_win = map_window(len(action_strip), action_at)
        # The corner slot belongs to whichever axis the clip on screen sits on: that
        # is the only axis whose window can have moved off the corner.  Both strips
        # hold the corner, so both windows hold at least it.
        window = action_win if bucket == "action" else seed_win
        strip = action_strip if bucket == "action" else seed_strip
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

    @staticmethod
    def _count_lines(model: HudModel) -> tuple[str, ...]:
        """"Seeds: n" / "Actions: n" — how many clips each axis stands for.

        Empty until fun_time's index has answered, so a satellite still starting up
        prints nothing rather than a confident "Seeds: 0".
        """
        if not (model.seed_count or model.action_count):
            return ()
        return (f"Seeds: {model.seed_count}", f"Actions: {model.action_count}")

    def _draw_counts(self, draw, x: int, y: int, lines: tuple[str, ...]) -> None:
        """The axis counts, in the corner left of the map and above its first row.

        The map draws only MAP_CELLS of each axis — and a window can hide a whole
        loop's worth — so this is the only place its real size can be read.  It sits
        outside the map proper, in the gutter's own column, and is there whether or
        not a loop is running.
        """
        for line_no, text in enumerate(lines, start=1):
            draw.text((x, y + _COUNT_LINE_H * line_no), text, font=self._tiny,
                      anchor="ls", fill=(*TEXT_MUTED, 255))

    def _draw_ellipses(self, draw, corner_rect, column_rect, seed_rects, action_rects,
                       axis, window) -> None:
        """Three dots in the slots kept at each end of *axis*, on whichever side it
        runs on past what is drawn — along the row, down the column.  They fall inside
        a running loop's rectangle, so they read as "more of these are in the loop"
        rather than as something outside it, and a gap in from its border, so they do
        not read as part of that border either.

        Drawn rather than typed: an "…" glyph hangs off the text baseline, which in a
        slot this small puts it against the bottom edge instead of in the middle.
        """
        before, after = ellipsis_rects(corner_rect, seed_rects, action_rects, axis,
                                       column_rect=column_rect)
        for rect, show in ((before, window.more_before), (after, window.more_after)):
            if not show:
                continue
            bx, by, bw, bh = rect
            mx, my = bx + bw / 2, by + bh / 2
            for step in (-1, 0, 1):
                dx, dy = (step * _DOT_GAP, 0) if axis == "seed" else (0, step * _DOT_GAP)
                draw.ellipse([mx + dx - _DOT, my + dy - _DOT, mx + dx + _DOT, my + dy + _DOT],
                             fill=(*TEXT_PRIMARY, 255))

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
                                       anchor="mm", fill=(*TEXT_MUTED, 255))
            image.alpha_composite(strip, (cx, y))

        def row(row_y: int, row_h: int, text: str) -> None:
            # One block of tight word-lines per act, with a bigger gap between
            # acts, so a two-word act ("Motion" / "Bounce") wraps close but two acts
            # ("Alpha" then "Theta Motion") are clearly separated.  Each act is lit
            # on its own account, and only inside a row the filter actually keeps:
            # the row says whether the clip is here at all, the act says which of
            # its acts is why.  Lighting a matching act inside a row the filter
            # drops would mark a clip that is not in the playlist.
            row_lit = label_is_filtered(text, model.filter_query)
            ascent, descent = self._row.getmetrics()
            line_h = ascent + descent - 4
            blocks = action_label_blocks(text)
            total = sum(len(block) for block in blocks) * line_h + (len(blocks) - 1) * ACT_GAP
            ty = row_y + (row_h - total) // 2
            for block in blocks:
                lit = row_lit and act_is_filtered(" ".join(block), model.filter_query)
                color = TEXT_PRIMARY if lit else TEXT_MUTED
                for line in block:
                    draw.text((x + gutter_w - MAP_GAP, ty + line_h / 2), line,
                              font=self._row, anchor="rm", fill=(*color, 255))
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

    def _button_box(self, draw, rect: Rect, *, on: bool,
                    on_color=WHITE) -> tuple[int, int, int, int]:
        """The panel's square button, and the color to draw its mark in — the
        single button shape every control on this HUD is drawn with, so a new one
        cannot invent its own look.

        Off it is an outline in the muted gray the rest of the chrome uses; on it
        fills *on_color* and the mark reverses out of it.  That fill is white for
        everything here except the lock: green across this family means favorites
        and the funscripts, and the lock is the gesture that favorites a clip, so
        it is the one control on the panel that earns the color.
        """
        bx, by, bw, bh = rect
        draw.rounded_rectangle(
            [bx, by, bx + bw - 1, by + bh - 1], radius=3,
            fill=(*on_color, 255) if on else None,
            outline=(*(on_color if on else TEXT_MUTED), 255), width=1,
        )
        return (*(BG_PRIMARY if on else TEXT_MUTED), 255)

    def _glyph_button(self, image, draw, rect: Rect, glyph: str, *, on: bool = False,
                      on_color=WHITE) -> None:
        """One of the panel's square buttons, with a mark or a glyph on it.

        A mark the family draws is rendered from its geometry, so the bin here is
        the bin on Origenerator's toolbar rather than whatever a symbol face had.
        A typed glyph is centered on its own ink instead: the padlock and the
        transport arrows sit high in a box that runs to the descender, so the
        font's own centering dropped every one of them toward its button's floor.
        """
        ink = self._button_box(draw, rect, on=on, on_color=on_color)
        if glyph.startswith(SHARED_MARK):
            draw_mark(image, shared_mark_name(glyph), rect, ink)
            return
        bx, by, bw, bh = rect
        draw_glyph(draw, bx + bw / 2, by + bh / 2, glyph, self._glyph, ink)

    def _filter_button(self, draw, rect: Rect, *, on: bool = False) -> None:
        """The same square button with a funnel drawn on it, for the act filter."""
        ink = self._button_box(draw, rect, on=on)
        bx, by, bw, bh = rect
        cx, cy = bx + bw / 2, by + bh / 2
        mouth, neck = _FUNNEL_W / 2, _FUNNEL_NECK / 2
        top, bottom = cy - _FUNNEL_H / 2, cy + _FUNNEL_H / 2
        draw.polygon(
            [(cx - mouth, top), (cx + mouth, top), (cx + neck, cy), (cx + neck, bottom),
             (cx - neck, bottom), (cx - neck, cy)],
            fill=ink,
        )

    def _minimize_button(self, draw, rect: Rect) -> None:
        """The same square button with a minimize bar drawn on it.

        Never lit: minimizing is a thing done rather than a state held — and the
        panel is gone the moment it takes effect, so there would be nobody left to
        read a lit button anyway.
        """
        ink = self._button_box(draw, rect, on=False)
        bx, by, bw, bh = rect
        cx, cy = bx + bw / 2, by + bh / 2
        top = cy - _MINIMIZE_H / 2
        draw.rectangle([cx - _MINIMIZE_W / 2, top, cx + _MINIMIZE_W / 2, top + _MINIMIZE_H - 1],
                       fill=ink)

    def _draw_controls(self, image, draw, controls: list[tuple[Rect, str]], favorite: Rect,
                       model: HudModel) -> None:
        """The side's own buttons, and the mark saying whether the clip on screen
        is one of the favorites.

        The lock and F-mode are states, so they light while they are on; the
        others do a thing rather than be in one.  The star is a readout, not a
        button, so it gets no box: a box would invite a press that does nothing.

        Both lit states are green rather than white, and so is the star: locking a
        clip puts it in the favorites and F-mode is the filter over them, so all
        three are the same fact and read as one color.  F-mode's button carries
        its own pink mark on top of that green, the same badge it wears on the
        main console and on the taskbar.
        """
        lit = {"lock": model.locked, "fmode": model.f_mode}
        for rect, name in controls:
            if name in _ICON_CONTROLS:
                self._button_box(draw, rect, on=lit.get(name, False), on_color=GREEN)
                draw_icon(draw, rect, _ICON_CONTROLS[name])
                continue
            if name == "minimize":
                self._minimize_button(draw, rect)
                continue
            self._glyph_button(image, draw, rect, _CONTROL_GLYPHS[name],
                               on=lit.get(name, False), on_color=GREEN)
        draw_mark(image, shared_mark_name(_FAVORITE_GLYPH), favorite,
                  (*(GREEN if model.is_favorite else TEXT_MUTED), 255))

    def _draw_filter_buttons(self, draw, rects: list[tuple[Rect, str]],
                             filter_query: str) -> None:
        """The filter button at the head of each row, lit on every row the filter
        keeps — which is more than the row that names it exactly, since fun_time
        matches a query as a substring (see :func:`label_is_filtered`).

        It lights off the published filter, exactly as the loop buttons light off
        the published loop — so a filter set any other way (spoken, or from the
        other side's map) shows here too, and pressing a lit one lifts it.
        """
        for rect, name in rects:
            self._filter_button(draw, rect, on=label_is_filtered(name, filter_query))

    def _draw_loop_controls(self, image, draw, corner_rect, column_rect, loop_action_rect,
                            loop_seed_rect, seed_rects, action_rects, active_loop,
                            hover_loop) -> None:
        """The two loop buttons, and — while one is hovered or its loop is on — a
        border around the videos it loops (dashed for a hover preview, solid once
        on).  The border wraps the room kept for that axis's "…" marks, so the clips
        they stand for read as part of the looped set."""
        boxes = {
            kind: (
                button,
                looped_group_box(corner_rect, seed_rects, action_rects, kind,
                                 reserve=ELLIPSIS_ROOM, column_rect=column_rect),
            )
            for kind, button in (("action", loop_action_rect), ("seed", loop_seed_rect))
        }
        for kind, (button, group_box) in boxes.items():
            if button is None:
                continue
            on = active_loop == kind
            self._glyph_button(image, draw, button, _LOOP_GLYPH, on=on)
            if on:
                gx, gy, gw, gh = group_box
                draw.rectangle([gx, gy, gx + gw - 1, gy + gh - 1],
                               outline=(*WHITE, 255), width=2)
            elif hover_loop == kind:
                _dashed_rect(draw, group_box, (*WHITE, 255))


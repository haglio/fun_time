"""The satellite's lock HUD: the panel fun_time publishes, and its geometry.

fun_time owns the *model* — which clips sit on the map, whether the satellite is
locked, which axis is looping — because only fun_time has the library metadata.
It serialises that to a small JSON file per side; this module parses it and lays
it out.  :mod:`satellite.hud_paint` turns the layout into a bitmap mpv
composites into the video, so the HUD has no window and therefore no z-order at
all — it *is* the frame.

Kept free of Pillow so the geometry and hit-testing are unit-testable without a
font: the paint module measures text and hands the width back in.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

# --- layout constants (px) ---------------------------------------------------
# Inset of the HUD from the player window's top-left corner.
MARGIN = 12

PAD = 10
MAP_THUMB_H = 54
MAP_GAP = 5
ROW_GAP = 12        # vertical gap between action rows — roomier than the seed gap
ACT_GAP = 6         # gap between two acts stacked in one row label
LOCK_BAND_H = 24
COL_LABEL_H = 13    # header strip above the map for the "Seed N" column labels
COL_LABEL_GAP = 4   # breathing room between a column label and its thumbnail
MIN_GUTTER = 30     # row-label gutter: never narrower than this
MAX_GUTTER = 100    # …and never wider, so a stray long act can't eat the map
LOOP_BTN = 18       # loop-button thickness: below the action column, right of the row
FILTER_BTN = 18     # act-filter button: at the head of each row, in the gutter
FILTER_ROOM = FILTER_BTN + MAP_GAP  # what it takes out of the row-label gutter
CTRL_BTN = 18       # a side-control button — the same square as a loop button
CTRL_BAND_H = 24    # the band those controls sit in, under the status line

# What the map keeps clear past the end of each axis for that axis's own buttons:
# the seed-loop and expand buttons right of the row, the action-loop button below
# the column.  The panel is measured with these and the map laid out against them,
# so a widened row can never push a button off the panel.
MAP_RIGHT_RESERVE = 2 * (LOOP_BTN + MAP_GAP)
MAP_BOTTOM_RESERVE = LOOP_BTN + MAP_GAP

# The map is three cells on a side — the clip on screen in the corner, two of its
# seeds along the row, two of its other acts down the column — and the panel is
# measured to hold exactly that.  Both panels were fixed slabs before (300x430 and
# 500x300), sized against the shape of the window they float over rather than
# against what they draw: the landscape one had no room for a third row, and both
# carried space no map ever reached.
MAP_CELLS = 3
# How wide one map cell draws, per side: a clip of that side's shape scaled to
# MAP_THUMB_H (fun_time caches thumbnails at a 160px longest edge, so a portrait
# 9:16 lands on 30 and a landscape 16:9 on 96).  The panel is measured off this,
# and it is also the placeholder drawn where fun_time has not produced a frame
# yet, so the map holds its shape before any thumbnail exists.
CELL_W = {"portrait": 30, "landscape": 96}

STATUS_SEPARATOR = " · "  # what fun_time joins the status line's parts with
STATUS_LINE_H = 14        # what each line past the first adds to the band
STATUS_DOT = 10           # the active-side dot at the head of the band
STATUS_TEXT_X = PAD + STATUS_DOT + 8  # where the status text starts, clear of it

Rect = tuple[int, int, int, int]  # (x, y, w, h)
Cell = tuple[str, int]            # ("corner", 0) | ("seed", i) | ("action", i)


# --- the status line ---------------------------------------------------------


def wrap_status_line(
    label: str, available: int, measure: Callable[[str], int]
) -> list[str]:
    """fun_time's status line broken into lines that fit *available* px.

    The portrait panel is only as wide as its clips, and the line can carry four
    parts at once — what is holding playback, the browse order, F-mode, and the
    act filter.  Pillow clips at the panel's edge without a word, so an unwrapped
    line does not read as "there is more": it reads as the states that ran out of
    room being *off*, which is the one thing a status line must never say.

    Breaks fall only on the separators, so a part is never split across lines and
    never reads as two states — a part too wide for the panel keeps its own line
    and overhangs instead.
    """
    parts = [part for part in label.split(STATUS_SEPARATOR) if part]
    lines: list[str] = []
    for part in parts:
        if lines:
            candidate = lines[-1] + STATUS_SEPARATOR + part
            if measure(candidate) <= available:
                lines[-1] = candidate
                continue
        lines.append(part)
    return lines


def status_band_height(lines: int) -> int:
    """The room the status takes above the map, for a status of *lines* lines.

    A wrapped line pushes the map down rather than being drawn over its first row.
    An empty status keeps the one-line band: the map's geometry is measured off
    the band's foot, so a side with nothing to say must not shift its own map.
    """
    return LOCK_BAND_H + max(0, lines - 1) * STATUS_LINE_H


@dataclass(frozen=True)
class HudCell:
    """One clip drawn on the map: its path, its cached thumbnail, its row label.

    ``thumb`` is "" while fun_time's background prewarm has not produced the
    frame yet — the map draws a placeholder there rather than waiting.
    """

    path: str
    thumb: str = ""
    label: str = ""


@dataclass(frozen=True)
class HudModel:
    """One satellite's HUD contents, exactly as fun_time published them."""

    side: str
    locked: bool = False
    lock_label: str = ""
    # Whether a bare, side-less command lands here — the player addressed most
    # recently.  Drawn as the dot beside the status line, and the only thing on
    # any HUD that says where those words are going.
    active: bool = False

    # Whether the clip on screen is one of the favourites.  The dashboard said
    # this by turning the side's panel green; the HUD marks it in the control
    # band, beside the buttons that act on that clip.
    is_favorite: bool = False
    corner: HudCell | None = None
    seeds: tuple[HudCell, ...] = ()
    actions: tuple[HudCell, ...] = ()
    current_action: str = ""
    # The act this side is filtered to, if any: the map lights every row the
    # filter keeps, and pressing a lit one lifts it.
    filter_query: str = ""
    active_loop: str = ""
    # How many clips each axis stands for, the clip on screen included.  The map
    # draws only the cells that fit, so it prints these in its top-left corner —
    # the only place the real size of each axis can be read.
    seed_count: int = 0
    action_count: int = 0
    # The map cell actually on screen — the corner normally, or another cell
    # while a loop plays a non-anchor member of the group.  Drawn bright; the
    # rest dim.
    playing: Cell = ("corner", 0)


# --- map geometry ------------------------------------------------------------

# The slot at each end of an axis for the "…" that says the map runs on past what
# is drawn, and the room it takes with a gap either side of it.  That room is kept
# unconditionally — loop or no loop, more to show or not — so nothing on the map
# ever shifts: not when a window slides, not when a mark appears, and not when a
# loop is switched on or off.
ELLIPSIS = 12
ELLIPSIS_ROOM = ELLIPSIS + 2 * MAP_GAP


def cell_width(side: str) -> int:
    """How wide one of *side*'s clips draws on the map.  One lookup, so the panel
    is never measured against a cell width the map does not draw."""
    return CELL_W.get(side, CELL_W["portrait"])


def map_extent(side: str) -> tuple[int, int]:
    """The room a full ``MAP_CELLS`` x ``MAP_CELLS`` map of *side*'s clips takes —
    the cells themselves, plus the gaps between them.  Not the "…" slots or the
    buttons past either end: those are the panel's business, below."""
    return (MAP_CELLS * cell_width(side) + (MAP_CELLS - 1) * MAP_GAP,
            MAP_CELLS * MAP_THUMB_H + (MAP_CELLS - 1) * ROW_GAP)


def panel_width(side: str, gutter: int) -> int:
    """How wide *side*'s panel has to be: the row-label gutter, then the map's
    left "…" slot, a full map, its right "…" slot, and the seed-loop and expand
    buttons past that.

    Independent of anything the status says, so the status can be wrapped into
    this width before the height is known.
    """
    map_w, _map_h = map_extent(side)
    return PAD + gutter + ELLIPSIS_ROOM + map_w + ELLIPSIS_ROOM + MAP_RIGHT_RESERVE + PAD


def panel_height(side: str, *, status_lines: int, mapped: bool) -> int:
    """How tall *side*'s panel has to be: the status band (as many lines as it
    wrapped to) and the control band, then — when there is a map to draw — the
    "Seed N" header strip, the map's own "…" slots and rows, and the action-loop
    button below them.

    *mapped* is false before the satellite's first clip arrives, when the panel is
    the two bands and nothing else: there is no map, so there is no room to keep
    for one.
    """
    _map_w, map_h = map_extent(side)
    foot = PAD + status_band_height(status_lines) + CTRL_BAND_H
    if mapped:
        foot += (COL_LABEL_H + COL_LABEL_GAP + ELLIPSIS_ROOM
                 + map_h + ELLIPSIS_ROOM + MAP_BOTTOM_RESERVE)
    return foot + PAD


@dataclass(frozen=True)
class MapWindow:
    """Which run of an axis is drawn, and whether it runs on past either end."""

    start: int
    count: int
    more_before: bool
    more_after: bool


def map_window(sizes: list[int], playing: int, available: int, *, gap: int = MAP_GAP) -> MapWindow:
    """The run of cells to draw from one axis of the map, keeping *playing* near its
    middle.

    *sizes* are the cells' extents along the axis (widths across the seed row,
    heights down the action column) and *available* is the room they share.  The run
    always holds *playing* and grows outward from it, alternating sides and taking
    the right first: an axis whose head is on screen therefore fills away from the
    corner, while one whose playing cell has moved along keeps that cell in the
    middle — which is what stops the lit cell walking off the end of the map and
    leaving nothing highlighted at all.  A single cell too big for the room is still
    drawn.
    """
    if not sizes:
        return MapWindow(0, 0, False, False)
    playing = max(0, min(playing, len(sizes) - 1))
    start, end = playing, playing + 1
    used = sizes[playing]
    while True:
        sides = []
        if end < len(sizes):
            sides.append(True)
        if start > 0:
            sides.append(False)
        if not sides:
            break
        # Take from whichever side has been given fewer cells so far — that is what
        # centres *playing* — with the right winning ties so a fresh loop reads left
        # to right.  A side that no longer fits defers to the other.
        sides.sort(key=lambda right: (end - playing - 1) if right else (playing - start))
        for right in sides:
            cost = sizes[end if right else start - 1] + gap
            if used + cost > available:
                continue
            used += cost
            if right:
                end += 1
            else:
                start -= 1
            break
        else:
            break
    return MapWindow(start, end - start, start > 0, end < len(sizes))


def thumbnail_rects(
    *,
    map_x: int,
    map_y: int,
    right: int,
    bottom: int,
    corner_size: tuple[int, int],
    seed_sizes: list[tuple[int, int]],
    action_sizes: list[tuple[int, int]],
) -> tuple[Rect, list[Rect], list[Rect]]:
    """Positioned ``(x, y, w, h)`` rects for the map's thumbnails.

    The corner sits at the origin, seeds walk right until one would cross
    *right*, actions walk down until one would cross *bottom* — each dropped
    rather than clipped, exactly as the map is drawn.  Sizes are the thumbnails'
    already-scaled dimensions.  This is the single source of the map geometry, so
    painting and click hit-testing cannot drift apart.
    """
    cw, ch = corner_size
    corner = (map_x, map_y, cw, ch)
    seeds: list[Rect] = []
    seed_x = map_x + cw + MAP_GAP
    for w, h in seed_sizes:
        if seed_x + w > right:
            break
        seeds.append((seed_x, map_y, w, h))
        seed_x += w + MAP_GAP
    actions: list[Rect] = []
    action_y = map_y + ch + ROW_GAP
    for w, h in action_sizes:
        if action_y + h > bottom:
            break
        actions.append((map_x, action_y, w, h))
        action_y += h + ROW_GAP
    return corner, seeds, actions


def playing_rect(
    playing: Cell, corner_rect: Rect, seed_rects: list[Rect], action_rects: list[Rect]
) -> Rect | None:
    """The rect of the cell holding the clip on screen, or None when that cell was
    not drawn (its axis's window closed before reaching it).

    Usually the corner — but a lock taken inside a running loop holds a member the
    map is not anchored on, and the ring saying "this is the clip being held" has to
    land on the cell that clip is actually drawn in, not on the loop's anchor.
    """
    bucket, index = playing
    if bucket == "corner":
        return corner_rect
    rects = seed_rects if bucket == "seed" else action_rects if bucket == "action" else []
    return rects[index] if 0 <= index < len(rects) else None


def _row_right(corner_rect: Rect, seed_rects: list[Rect]) -> int:
    cx, _cy, cw, _ch = corner_rect
    return max([cx + cw] + [sx + sw for sx, _sy, sw, _sh in seed_rects])


def _col_bottom(corner_rect: Rect, action_rects: list[Rect]) -> int:
    _cx, cy, _cw, ch = corner_rect
    return max([cy + ch] + [ay + ah for _ax, ay, _aw, ah in action_rects])


def loop_button_rects(
    corner_rect: Rect | None,
    seed_rects: list[Rect],
    action_rects: list[Rect],
    right: int,
    bottom: int,
    *,
    reserve_row: int = 0,
    reserve_col: int = 0,
) -> tuple[Rect | None, Rect | None]:
    """``(loop_action_rect, loop_seed_rect)``: a button below the action column
    and one right of the seed row — or None for either that would overflow the
    panel.  The action button loops the column, the seed button the row.

    *reserve_row* / *reserve_col* are the room each axis keeps past its end for the
    "…" mark, so the buttons clear it.
    """
    if corner_rect is None:
        return None, None
    cx, cy, cw, ch = corner_rect
    loop_action_y = _col_bottom(corner_rect, action_rects) + reserve_col + MAP_GAP
    loop_action = (cx, loop_action_y, cw, LOOP_BTN) if loop_action_y + LOOP_BTN <= bottom else None
    loop_seed_x = _row_right(corner_rect, seed_rects) + reserve_row + MAP_GAP
    loop_seed = (loop_seed_x, cy, LOOP_BTN, ch) if loop_seed_x + LOOP_BTN <= right else None
    return loop_action, loop_seed


def looped_group_box(
    corner_rect: Rect, seed_rects: list[Rect], action_rects: list[Rect], axis: str,
    *, reserve: int = 0,
) -> Rect:
    """The rectangle drawn around the clips an *axis* loop is cycling — the row for
    "seed", the column for "action" — grown by *reserve* at each end so the loop's
    "…" marks fall inside it, saying those clips are in the loop too."""
    cx, cy, cw, ch = corner_rect
    if axis == "seed":
        row_right = _row_right(corner_rect, seed_rects)
        return (cx - reserve, cy, (row_right + reserve) - (cx - reserve), ch)
    col_bottom = _col_bottom(corner_rect, action_rects)
    return (cx, cy - reserve, cw, (col_bottom + reserve) - (cy - reserve))


def ellipsis_rects(
    corner_rect: Rect, seed_rects: list[Rect], action_rects: list[Rect], axis: str,
) -> tuple[Rect, Rect]:
    """The two slots an axis keeps for the "…" marks that say it runs on past what is
    drawn: flanking the seed row left and right, or the action column above and
    below.  Each sits a gap in from the loop rectangle, so a mark never reads as part
    of that border."""
    cx, cy, cw, ch = corner_rect
    if axis == "seed":
        return ((cx - MAP_GAP - ELLIPSIS, cy, ELLIPSIS, ch),
                (_row_right(corner_rect, seed_rects) + MAP_GAP, cy, ELLIPSIS, ch))
    return ((cx, cy - MAP_GAP - ELLIPSIS, cw, ELLIPSIS),
            (cx, _col_bottom(corner_rect, action_rects) + MAP_GAP, cw, ELLIPSIS))


# --- the side's own controls -------------------------------------------------
# The buttons the dashboard used to carry for this satellite, now on the
# satellite itself: browse first (the pair reached for most), then the two that
# act on the clip on screen.  Each name is also its command's verb, so
# "portrait_prev" and "landscape_trash" fall out of the same tuple that draws
# them and the button can never post a command it isn't labelled for.
CONTROLS = ("prev", "next", "lock", "trash")


def control_button_rects(x: int, y: int) -> list[tuple[Rect, str]]:
    """Each side-control's ``(rect, name)``, in a row running right from ``(x, y)``."""
    return [
        ((x + index * (CTRL_BTN + MAP_GAP), y, CTRL_BTN, CTRL_BTN), name)
        for index, name in enumerate(CONTROLS)
    ]


def favorite_mark_rect(right: int, y: int) -> Rect:
    """The favourite mark, at the far end of the control band.

    A readout, not a button: the dashboard said this with a green panel, and the
    star says it in the space the panel used to occupy.  It keeps the row's far
    end rather than following the buttons, so it does not move when they change.
    """
    return (right - CTRL_BTN, y, CTRL_BTN, CTRL_BTN)


def seed_column_label(index: int) -> str:
    """The header over a seed column: its place in the family, counting from one.

    A window can open partway along the family, so the headers carry the real
    ordinals — "Seed 7" over the seventh seed — rather than restarting at one and
    hiding how far along the row has got.
    """
    return f"Seed {index + 1}"


def expand_button_rect(loop_seed_rect: Rect | None, right: int) -> Rect | None:
    """The "more seeds" expand button, in the seed row just right of the seed-loop
    button — widening is the row's effect, so it lives in the row.  None when there
    is no seed-loop button or it would overflow the panel's right edge."""
    if loop_seed_rect is None:
        return None
    sx, sy, sw, sh = loop_seed_rect
    ex = sx + sw + MAP_GAP
    if ex + LOOP_BTN > right:
        return None
    return (ex, sy, LOOP_BTN, sh)


# --- hit-testing -------------------------------------------------------------


@dataclass
class HudTargets:
    """What the last render put where — the rects a press is tested against."""

    click: list[tuple[Rect, str]]
    loop: list[tuple[Rect, str]]
    filter: list[tuple[Rect, str]]
    expand: Rect | None
    control: list[tuple[Rect, str]] = field(default_factory=list)
    # The favourite mark is a readout, so it is here only to carry its tooltip.
    favorite: Rect | None = None


def build_click_targets(
    corner_rect: Rect | None,
    seed_rects: list[Rect],
    action_rects: list[Rect],
    corner: HudCell | None,
    seeds: list[HudCell] | tuple[HudCell, ...],
    actions: list[HudCell] | tuple[HudCell, ...],
) -> list[tuple[Rect, str]]:
    """(rect, video_path) for every clickable thumbnail: the corner is the current
    clip, then each drawn seed and action zipped to its path."""
    targets: list[tuple[Rect, str]] = []
    if corner_rect is not None and corner is not None and corner.path:
        targets.append((corner_rect, corner.path))
    targets.extend((rect, cell.path) for rect, cell in zip(seed_rects, seeds))
    targets.extend((rect, cell.path) for rect, cell in zip(action_rects, actions))
    return targets


def hit_test_targets(targets: list[tuple[Rect, str]], px: int, py: int) -> str:
    """The value whose rect contains ``(px, py)``, or "" if none does — used for
    the thumbnail (path), loop-button (axis) and action-label (action) targets."""
    for (x, y, w, h), value in targets:
        if x <= px < x + w and y <= py < y + h:
            return value
    return ""


def filter_button_rects(
    corner_rect: Rect | None,
    action_rects: list[Rect],
    gutter_x: int,
    current_action: str,
    action_labels: list[str] | tuple[str, ...],
) -> list[tuple[Rect, str]]:
    """(rect, action_name) for the filter button at the head of each map row — the
    corner's row is the current action, the rows below are the sibling actions.
    Pressing one filters the satellite to that action.

    One button per row, at the gutter's left edge and as tall as its row: the same
    shape the seed-loop button has beside the seed row, because it stands to its row
    the same way.  Filtering used to be a click on the action name itself, which
    nothing on the panel said was clickable — so the button is the whole affordance
    now, and the name beside it is only a label again.

    A row with no action name gets no button: there is nothing to filter to.
    """
    rects: list[tuple[Rect, str]] = []
    if corner_rect is not None and current_action:
        _cx, cy, _cw, ch = corner_rect
        rects.append(((gutter_x, cy, FILTER_BTN, ch), current_action))
    for (_ax, ay, _aw, ah), name in zip(action_rects, action_labels):
        if name:
            rects.append(((gutter_x, ay, FILTER_BTN, ah), name))
    return rects


def _norm_act(text: str) -> str:
    """An act label or a filter query flattened for comparison: lower-cased with
    runs of whitespace collapsed, the way fun_time normalizes both sides of its own
    match (``media_metadata._norm_text``)."""
    return " ".join(str(text or "").split()).lower()


def label_is_filtered(label: str, filter_query: str) -> bool:
    """Whether the side's filter is one *label*'s row satisfies.

    fun_time keeps a clip when the query appears as a *contiguous substring* of its
    metadata (``media_metadata.matches_query``), so the map has to read the same way
    — filtered to "gamma", a row labelled "POV Gamma" and a row carrying two acts as
    "Gamma, Theta" are both clips the filter keeps.  Matching the whole label exactly
    instead left those rows unlit, so the mark flicked off the moment one came up and
    back on at the next exact match: it read as the filter dropping and returning
    while the playlist under it never changed.

    One rule, used by the map to light a row's label and its filter button, and by
    the press to make that button a toggle, so what looks on and what turns off
    cannot disagree.  fun_time records a filter as the act lower-cased, which is how
    a query reaches it.
    """
    query = _norm_act(filter_query)
    return bool(query) and query in _norm_act(label)


LOOP_TOOLTIPS = {"action": "Loop this action column", "seed": "Loop this seed row"}
FILTER_TOOLTIP = "Filter to this action"
EXPAND_TOOLTIP = "More seeds — widen the net"
CONTROL_TOOLTIPS = {
    "prev": "Previous clip",
    "next": "Next clip",
    "lock": "Lock / unlock this clip",
    "trash": "Unfavorite it — or mark weird when it is not a favorite",
}
FAVORITE_TOOLTIP = "In the favourites"


def _in(rect: Rect | None, px: int, py: int) -> bool:
    if rect is None:
        return False
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


def button_tooltip(targets: HudTargets, px: int, py: int) -> str:
    """What the HUD control under ``(px, py)`` is, or "" over none of them.

    Every glyph on this panel is cryptic on purpose — it is read over moving video
    — so each one names itself on hover.  Taking the whole target bundle means a
    new control needs a line in a dict here and nothing else.
    """
    for bucket, tooltips in ((targets.control, CONTROL_TOOLTIPS), (targets.loop, LOOP_TOOLTIPS)):
        hit = hit_test_targets(bucket, px, py)
        if hit:
            return tooltips.get(hit, "")
    # The filter buttons all say the same thing — each one names the act beside it,
    # so the tooltip only has to say what pressing it does.
    if hit_test_targets(targets.filter, px, py):
        return FILTER_TOOLTIP
    if _in(targets.expand, px, py):
        return EXPAND_TOOLTIP
    if _in(targets.favorite, px, py):
        return FAVORITE_TOOLTIP
    return ""


# --- clicks ------------------------------------------------------------------

# Windows' default double-click time.  A click that turns out to be the first
# half of a double-click must not also post a switch, so a lone click waits this
# long before it is posted.  Erring short is safe: a slow double-click simply
# switches to the clip it then locks.
DOUBLE_CLICK_S = 0.5


class HudClicks:
    """Turns presses on the HUD into the fun_time commands they stand for.

    A press on a thumbnail is ambiguous until the double-click window passes —
    single switches to the clip, double locks it — so :meth:`press` defers it and
    :meth:`due` posts it once no second click has arrived.  Every other press
    (loop buttons, expand, filter buttons) is unambiguous and posts immediately.
    """

    def __init__(self, side: str, *, double_click_s: float = DOUBLE_CLICK_S) -> None:
        self._side = side
        self._double_click_s = double_click_s
        self._pending_path = ""
        self._pending_at = 0.0
        # Which axis is looping, and which act the side is filtered to.  Both are
        # mirrored from the published panel on every refresh, and set optimistically
        # on a click so the control lights up before fun_time's answer comes back.
        self.active_loop = ""
        self.active_filter = ""

    def press(self, targets: HudTargets, px: int, py: int, *, now: float) -> str:
        """The command for a press at ``(px, py)``, or "" when it posts nothing
        yet (a first thumbnail click, or empty space)."""
        control = hit_test_targets(targets.control, px, py)
        if control:
            return f"{self._side}_{control}"
        loop = hit_test_targets(targets.loop, px, py)
        if loop:
            return self._toggle_loop(loop)
        if _in(targets.expand, px, py):
            return f"{self._side}_more_seeds"
        action = hit_test_targets(targets.filter, px, py)
        if action:
            # A lit button is a row the filter is keeping, so pressing it lifts that
            # filter: the way out is whichever control shows as on — the same toggle
            # the loop buttons are.  It costs narrowing "gamma" to "pov gamma" from
            # the map, which is the right trade: a green button that stayed green on
            # a press would be the confusing half.
            if label_is_filtered(action, self.active_filter):
                self.active_filter = ""
                return f"{self._side}_no_filter"
            self.active_filter = action.lower()
            return f"filter_{self._side}_{action.lower().replace(' ', '_')}"
        path = hit_test_targets(targets.click, px, py)
        if not path:
            return ""
        if path == self._pending_path and now - self._pending_at <= self._double_click_s:
            self._pending_path = ""
            return f"{self._side}_lock_video|{path}"
        self._pending_path = path
        self._pending_at = now
        return ""

    def due(self, *, now: float) -> str:
        """The deferred single-click switch, once its double-click window lapsed."""
        if not self._pending_path or now - self._pending_at <= self._double_click_s:
            return ""
        path, self._pending_path = self._pending_path, ""
        return f"{self._side}_play_video|{path}"

    def _toggle_loop(self, kind: str) -> str:
        """Turn *kind*'s loop on, or — if it is already on — off.  Turning one on
        turns the other off: the two loops cannot coexist, matching the command
        the dispatch loop runs."""
        if self.active_loop == kind:
            self.active_loop = ""
            return f"{self._side}_no_loop"
        self.active_loop = kind
        return f"{self._side}_{kind}_loop"


# --- action labels -----------------------------------------------------------

# Action words that read wrong in plain title case — kept upper.
_ACTION_ACRONYMS = {"pov": "POV"}


def _titlecase_word(word: str) -> str:
    return _ACTION_ACRONYMS.get(word.lower(), word[:1].upper() + word[1:].lower())


def action_label_blocks(name: str) -> list[list[str]]:
    """A clip's action(s) drawn nicely, as one block of word-lines per action.

    A clip can carry several comma-separated acts ("Alpha, Theta Motion") — each
    becomes its own block, so they can be drawn with a gap between the acts but
    tight wrapping within one.  "(unknown)" when there is no action metadata.
    """
    blocks = [
        [_titlecase_word(word) for word in act.split()]
        for act in name.split(",")
        if act.strip()
    ]
    return blocks or [["(unknown)"]]


def friendly_action_label(name: str) -> str:
    """The flat, newline-per-word form of an action label — used for measuring the
    gutter.  :func:`action_label_blocks` is what the row is actually drawn from."""
    return "\n".join(word for block in action_label_blocks(name) for word in block)


def _cell(raw: object) -> HudCell | None:
    if not isinstance(raw, dict):
        return None
    return HudCell(
        path=str(raw.get("path", "")),
        thumb=str(raw.get("thumb", "") or ""),
        label=str(raw.get("label", "") or ""),
    )


def parse_hud(text: str) -> HudModel | None:
    """The published panel, or None when *text* is not a complete panel.

    fun_time rewrites the file in place while the player is reading it, so a
    torn or empty read is expected and simply means "keep the HUD you have".
    """
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or "side" not in raw:
        return None
    playing = raw.get("playing") or ["corner", 0]
    seeds = [_cell(item) for item in raw.get("seeds", []) or []]
    actions = [_cell(item) for item in raw.get("actions", []) or []]
    return HudModel(
        side=str(raw.get("side", "")),
        locked=bool(raw.get("locked", False)),
        lock_label=str(raw.get("lock_label", "") or ""),
        active=bool(raw.get("active", False)),

        is_favorite=bool(raw.get("is_favorite", False)),
        corner=_cell(raw.get("corner")),
        seeds=tuple(cell for cell in seeds if cell is not None),
        actions=tuple(cell for cell in actions if cell is not None),
        current_action=str(raw.get("current_action", "") or ""),
        filter_query=str(raw.get("filter_query", "") or ""),
        active_loop=str(raw.get("active_loop", "") or ""),
        seed_count=int(raw.get("seed_count", 0) or 0),
        action_count=int(raw.get("action_count", 0) or 0),
        playing=(str(playing[0]), int(playing[1])),
    )

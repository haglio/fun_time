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
from dataclasses import dataclass

# --- layout constants (px) ---------------------------------------------------
# The panel is shaped like its satellite's clips so more of the map fits: the
# landscape HUD is wide (wide clips -> wide seed columns, plus room for the
# seed-loop and expand buttons past them), the portrait HUD is tall (tall clips
# -> tall rows, plus room for the action column to grow down).
PANEL_SIZE = {"portrait": (300, 430), "landscape": (500, 300)}
# Inset of the HUD from the player window's top-left corner.
MARGIN = 12

PAD = 10
MAP_THUMB_H = 54
MAP_GAP = 5
ROW_GAP = 12        # vertical gap between action rows — roomier than the seed gap
ACT_GAP = 6         # gap between two acts stacked in one row label
LOCK_BAND_H = 24
STATUS_LINE_H = 15
COL_LABEL_H = 13    # header strip above the map for the "Seed N" column labels
COL_LABEL_GAP = 4   # breathing room between a column label and its thumbnail
MIN_GUTTER = 30     # row-label gutter: never narrower than this
MAX_GUTTER = 100    # …and never wider, so a stray long act can't eat the map
LOOP_BTN = 18       # loop-button thickness: below the action column, right of the row

Rect = tuple[int, int, int, int]  # (x, y, w, h)
Cell = tuple[str, int]            # ("corner", 0) | ("seed", i) | ("action", i)


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
    corner: HudCell | None = None
    seeds: tuple[HudCell, ...] = ()
    actions: tuple[HudCell, ...] = ()
    current_action: str = ""
    filter_query: str = ""
    active_loop: str = ""
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


def build_label_targets(
    corner_rect: Rect | None,
    action_rects: list[Rect],
    gutter_x: int,
    gutter_w: int,
    current_action: str,
    action_labels: list[str] | tuple[str, ...],
) -> list[tuple[Rect, str]]:
    """(rect, action_name) for each clickable action-name label in the left gutter —
    the corner's row is the current action, the rows below are the sibling actions.
    Clicking one filters the satellite to that action."""
    targets: list[tuple[Rect, str]] = []
    if corner_rect is not None and current_action:
        _cx, cy, _cw, ch = corner_rect
        targets.append(((gutter_x, cy, gutter_w, ch), current_action))
    for (_ax, ay, _aw, ah), name in zip(action_rects, action_labels):
        if name:
            targets.append(((gutter_x, ay, gutter_w, ah), name))
    return targets


LOOP_TOOLTIPS = {"action": "Loop this action column", "seed": "Loop this seed row"}
EXPAND_TOOLTIP = "More seeds — widen the net"


def button_tooltip(
    loop_targets: list[tuple[Rect, str]],
    expand_rect: Rect | None,
    px: int,
    py: int,
) -> str:
    """The tooltip for whichever HUD button is under ``(px, py)`` — the loop buttons
    or the expand button — or "" when the cursor is over neither, so the user can
    tell what each cryptic glyph does."""
    loop = hit_test_targets(loop_targets, px, py)
    if loop:
        return LOOP_TOOLTIPS.get(loop, "")
    if expand_rect is not None:
        ex, ey, ew, eh = expand_rect
        if ex <= px < ex + ew and ey <= py < ey + eh:
            return EXPAND_TOOLTIP
    return ""


# --- clicks ------------------------------------------------------------------

# Windows' default double-click time.  A click that turns out to be the first
# half of a double-click must not also post a switch, so a lone click waits this
# long before it is posted.  Erring short is safe: a slow double-click simply
# switches to the clip it then locks.
DOUBLE_CLICK_S = 0.5


@dataclass
class HudTargets:
    """What the last render put where — the rects a press is tested against."""

    click: list[tuple[Rect, str]]
    loop: list[tuple[Rect, str]]
    label: list[tuple[Rect, str]]
    expand: Rect | None


class HudClicks:
    """Turns presses on the HUD into the fun_time commands they stand for.

    A press on a thumbnail is ambiguous until the double-click window passes —
    single switches to the clip, double locks it — so :meth:`press` defers it and
    :meth:`due` posts it once no second click has arrived.  Every other press
    (loop buttons, expand, action labels) is unambiguous and posts immediately.
    """

    def __init__(self, side: str, *, double_click_s: float = DOUBLE_CLICK_S) -> None:
        self._side = side
        self._double_click_s = double_click_s
        self._pending_path = ""
        self._pending_at = 0.0
        # Which axis is looping.  Mirrored from the published panel on every
        # refresh, and set optimistically on a click so the button lights up
        # before fun_time's answer comes back.
        self.active_loop = ""

    def press(self, targets: HudTargets, px: int, py: int, *, now: float) -> str:
        """The command for a press at ``(px, py)``, or "" when it posts nothing
        yet (a first thumbnail click, or empty space)."""
        loop = hit_test_targets(targets.loop, px, py)
        if loop:
            return self._toggle_loop(loop)
        if targets.expand is not None:
            ex, ey, ew, eh = targets.expand
            if ex <= px < ex + ew and ey <= py < ey + eh:
                return f"{self._side}_more_seeds"
        action = hit_test_targets(targets.label, px, py)
        if action:
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
        corner=_cell(raw.get("corner")),
        seeds=tuple(cell for cell in seeds if cell is not None),
        actions=tuple(cell for cell in actions if cell is not None),
        current_action=str(raw.get("current_action", "") or ""),
        filter_query=str(raw.get("filter_query", "") or ""),
        active_loop=str(raw.get("active_loop", "") or ""),
        playing=(str(playing[0]), int(playing[1])),
    )

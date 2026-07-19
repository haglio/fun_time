"""What each satellite's HUD shows.

Assembles the panel drawn over each satellite: the side's own state — locked,
looping, filtered, favourite — and the other videos reachable in the current
clip's action group and seed family.  Only fun_time has the library metadata this
needs, so the model lives here — :mod:`fun_time.hud_transport` publishes it, and
each satellite player draws it straight into its own video (:mod:`satellite.hud`).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fun_time.media_metadata import (
    GroupIndex,
    action_group_members,
    cached_group_index,
    normalize_path_key,
    seed_family_members,
    widened_seed_members,
)
from fun_time.modes import collect_video_files
from fun_time.thumbnail_cache import thumbnail_for

THUMBNAIL_CACHE_DIRNAME = "hud_thumbnails"

# What F-mode is called on screen.  One key toggles it for every player at once, so
# it has to read the same on both HUDs and on the dashboard — Nau's own HUD carries
# a matching constant (``nau.hud.F_MODE_LABEL`` in genau), which is the one place
# the wording could drift.
F_MODE_LABEL = "F-Mode"


def _lock_label(locked: bool) -> str:
    """The status word for the lock: a lock is repeat-one on the clip on screen, and
    the one kind there is — the grid's other "lock <scope>" words are the loops."""
    return "Locked" if locked else "Unlocked"


def _status_label(
    locked: bool, loop_axis: str, latest: bool, filter_query: str, f_mode: bool
) -> str:
    """The HUD's one status line — everything the side is in, at a glance.

    What is looping (or, with no loop, the lock), which order the browse is in, and
    the two filtering layers: a reader should not have to look anywhere else to know
    how the satellite is behaving.  A running loop displaces the lock word, which is
    off and beside the point while a group repeats — but never the order, since
    ending the loop drops the side straight back into it.

    The filters run coarse to fine: F-mode cuts the whole library to favourites,
    then the act filter narrows what is left.  The act goes last and unlabelled — a
    phrase from the vocabulary in *that* position can only be the filter — so the
    fixed word has to precede it rather than follow it.  How big each axis is belongs
    to the map, which prints its own counts.
    """
    parts = [f"Looping {loop_axis}s" if loop_axis else _lock_label(locked)]
    parts.append("Latest" if latest else "Shuffle")
    if f_mode:
        parts.append(F_MODE_LABEL)
    if filter_query:
        parts.append(filter_query)
    return " · ".join(parts)


@dataclass(frozen=True)
class HudPanel:
    """One satellite's HUD contents (portrait or landscape).

    The current clip anchors the map: seeds (the same act, other subjects) run
    right from it, and distinct other actions run down from it.
    """

    side: str
    locked: bool
    lock_label: str
    current: str
    seed_siblings: list[str]
    action_siblings: list[str]
    # Whether the clip on screen is one of the favourites.  The dashboard used to
    # say this by turning the side's panel green; the HUD marks it in its control
    # band instead, beside the buttons that act on that clip.  It would read
    # better beside ``locked``, but a defaulted field cannot precede a required
    # one, and defaulting it keeps every construction site from naming it.
    is_favorite: bool = False
    # Labels for the map's axes: the current clip's own action (the top row),
    # and each action sibling's action name (the rows down the column). Seed
    # columns are labelled by ordinal ("Seed 1", …) so need no data here.
    current_action: str = ""
    action_labels: tuple[str, ...] = ()
    # How many clips each axis stands for, the clip on screen included: the seed
    # family (widened when the row is) and the distinct acts of the subject.  The map
    # draws only the cells that fit, so these are the only place the real size of
    # each axis can be read.
    seed_count: int = 0
    action_count: int = 0
    filter_query: str = ""
    # Whether this side is the one a bare, side-less command reaches — the player
    # addressed most recently.  Global state, published per side so each panel can
    # answer "is it me?" without knowing what the others are.
    active: bool = False
    # Which axis this side is looping ("" none / "action" / "seed").  While a
    # loop runs, ``current`` is frozen to the group's anchor (so the map does not
    # re-orient as the clip auto-advances) and ``playing`` names the map cell
    # actually on screen, which the overlay lights up.  With no loop, ``playing``
    # is just ``current`` — the corner.
    active_loop: str = ""
    playing: str = ""


def _others(members: list[str], current: str) -> list[str]:
    """*members* without *current* itself — the clips you could reach from here."""
    key = normalize_path_key(current)
    return [member for member in members if normalize_path_key(member) != key]


def _distinct_action_siblings(index: GroupIndex, current: str) -> list[str]:
    """One representative clip per OTHER action in the current clip's group.

    The action axis steps between distinct acts, so same-act twins collapse to a
    single entry and the current clip's own act is left out — it is the corner.
    """
    current_key = normalize_path_key(current)
    current_action = index.action_by_path.get(current_key, "")
    reps: list[str] = []
    seen: set[str] = set()
    for member in action_group_members(index, current):
        action = index.action_by_path.get(normalize_path_key(member), "")
        if not action or action == current_action or action in seen:
            continue
        seen.add(action)
        reps.append(member)
    return reps


# Map navigation geometry
# -----------------------
# The map is an L: the current clip anchors the corner, its seed family runs
# right (columns) and its distinct other actions run down (rows).  These
# framework-free helpers let a keyboard selection move around that L and resolve
# a cell to the clip drawn there, so the dispatch loop can drive the map from the
# arrow / WASD keys exactly as a thumbnail click drives it.

# How many seed columns and action rows the overlay draws — navigation walks the
# same capped lists so a selection never lands on a thumbnail that was dropped
# for want of room.
SEED_LIMIT = 6
ACTION_LIMIT = 4

Cell = tuple[str, int]  # ("corner", 0) | ("seed", i) | ("action", i)


# Each direction rides one axis of the L: right/left the seed row, down/up the
# action column.
_AXIS_STEPS = {
    "right": ("seed", 1),
    "left": ("seed", -1),
    "down": ("action", 1),
    "up": ("action", -1),
}


def navigate_cell(cell: Cell, direction: str, *, seed_count: int, action_count: int) -> Cell:
    """The cell reached by moving *direction* from *cell* on the L-shaped map.

    Each axis is a ring with the corner at its head, so walking off either end
    comes back around rather than dead-ending — hold a direction and you tour
    that axis.  Two moves still keep the selection put, because there is nowhere
    for them to go: off the axis the cell is on (a seed has nothing below it, an
    action nothing beside it), and a ring holding only the corner.
    """
    axis_step = _AXIS_STEPS.get(direction)
    if axis_step is None:
        return cell
    axis, step = axis_step
    bucket, index = cell
    if bucket not in ("corner", axis):
        return cell
    ring = (seed_count if axis == "seed" else action_count) + 1  # the corner heads it
    position = ((0 if bucket == "corner" else index + 1) + step) % ring
    return ("corner", 0) if position == 0 else (axis, position - 1)


def locate_cell(current: str, corner: str, seeds: list[str], actions: list[str]) -> Cell | None:
    """Which cell holds *current* — the corner, a seed or an action — by exact
    path match, or None when *current* is not drawn on the map at all (e.g. the
    satellite auto-advanced off the family)."""
    key = normalize_path_key
    if key(current) == key(corner):
        return ("corner", 0)
    for i, path in enumerate(seeds):
        if key(path) == key(current):
            return ("seed", i)
    for i, path in enumerate(actions):
        if key(path) == key(current):
            return ("action", i)
    return None


def cell_path(cell: Cell, corner: str, seeds: list[str], actions: list[str]) -> str:
    """The clip drawn at *cell*, or "" when the cell index is out of range."""
    bucket, index = cell
    if bucket == "corner":
        return corner
    members = seeds if bucket == "seed" else actions if bucket == "action" else []
    return members[index] if 0 <= index < len(members) else ""


def hud_map_cells(
    index: GroupIndex, anchor: str, *, seed_limit: int = SEED_LIMIT, action_limit: int = ACTION_LIMIT
) -> tuple[list[str], list[str]]:
    """The seed-row and action-column clips the map draws around *anchor*, capped
    at the overlay's draw limits.  The seed row is the exact family — keyboard
    navigation walks the core family, never the widened "more seeds" pool — so the
    axes match what a fresh (un-widened) map shows."""
    seeds = _others(seed_family_members(index, anchor), anchor)[:seed_limit]
    actions = _distinct_action_siblings(index, anchor)[:action_limit]
    return seeds, actions


def _playing_member(
    index: GroupIndex, anchor: str, current: str, seed: list[str], action: list[str], axis: str
) -> str:
    """Which drawn map cell the live *current* clip is — the one the overlay
    lights up.  On the seed axis the clip is itself a seed cell; on the action
    axis it is represented by the sibling sharing its action."""
    key = normalize_path_key
    if key(current) == key(anchor):
        return anchor
    if axis == "seed":
        return current
    current_action = index.action_by_path.get(key(current), "")
    if current_action == index.action_by_path.get(key(anchor), ""):
        return anchor
    for member in action:
        if index.action_by_path.get(key(member), "") == current_action:
            return member
    return anchor


def _map_anchor_in(group: list[str], anchor: str) -> str:
    """The clip a running loop's map hangs on: *anchor* — the clip the loop started
    on, which heads the queue it wrote — whenever it is still one of *group*.

    Falls back to the group's lowest-keyed member when there is no usable anchor (a
    loop still running from before the anchor was recorded, or one whose anchor clip
    has since been trashed).  That is at least the same clip whichever member is
    playing, so the map goes on holding still as the loop advances.
    """
    key = normalize_path_key(anchor)
    if anchor and any(normalize_path_key(member) == key for member in group):
        return anchor
    return min(group, key=normalize_path_key)


def _axis_holding(index: GroupIndex, anchor: str, current: str, widened_pool: list[str]) -> str:
    """Which axis of *anchor*'s map the live clip sits on — "seed", "action", or ""
    once it is on none of them.

    This is what decides that a map hung on *anchor* goes on hanging there: while the
    clip on screen is somewhere on that map there is a cell to light, so nothing has
    to move.  It is how ending a loop leaves the map alone — the clip the loop was
    playing is still one of its cells — and how the map re-homes anyway once the
    browse moves on past the whole group.
    """
    key = normalize_path_key(current)
    if key == normalize_path_key(anchor):
        return "seed"  # the corner: on both axes at once, and lit either way
    row = widened_pool or seed_family_members(index, anchor)
    if any(normalize_path_key(member) == key for member in row):
        return "seed"
    if any(normalize_path_key(member) == key for member in action_group_members(index, anchor)):
        return "action"
    return ""


def build_hud_panel(
    side: str,
    *,
    locked: bool,
    current: str,
    index: GroupIndex | None,
    filter_query: str = "",
    loop_axis: str = "",
    map_anchor: str = "",
    widen_clip: str = "",
    nav_anchor: str = "",
    latest: bool = False,
    f_mode: bool = False,
    active: bool = False,
    is_favorite: bool = False,
) -> HudPanel:
    """The HUD panel for *side*, given its lock flag, current clip and index.

    to one clip per distinct other act.  *widen_clip* names the clip the seed row
    was widened around ("more seeds"); while widening is in force the row grows
    past the exact parameter set to the clips nearest that one's scene.

    *map_anchor* is the clip the map hangs on — the clip a loop started on, which
    heads the queue that loop wrote.  While it holds, the map reads in the order the
    player plays it (that clip in the corner, the group running away from it) and
    does not re-orient as the loop advances; ``playing`` marks the cell actually on
    screen.  It goes on holding after the loop is switched off, for as long as the
    clip on screen is still one of the map's cells, so ending a loop takes away the
    loop's chrome and nothing else; once the browse moves on past the group there is
    no cell left to light and the map re-homes on the live clip.  *loop_axis* names
    only whether a loop is actually *running* — the lit button and the rectangle.

    ``nav_anchor`` hangs the map the same way for keyboard navigation, so an
    arrow-driven selection moves across a stable map.  A loop wins over it.
    """
    have_siblings = bool(current) and index is not None
    # The widened pool, ranked once around *widen_clip* and reused: ranking it again
    # from another member would score a different set and shuffle the row underneath
    # a map that is supposed to be holding still.
    widened_pool = widened_seed_members(index, widen_clip) if have_siblings and widen_clip else []
    pool_keys = {normalize_path_key(member) for member in widened_pool}
    anchor = current
    active_loop = ""
    map_held = False
    nav_frozen = False
    if have_siblings and loop_axis in ("seed", "action"):
        if loop_axis == "action":
            group = action_group_members(index, current)
        elif normalize_path_key(current) in pool_keys:
            group = widened_pool
        else:
            group = seed_family_members(index, current)
        if len(group) >= 2:
            anchor = _map_anchor_in(group, map_anchor)
            active_loop = loop_axis
            map_held = True
    elif have_siblings and map_anchor and _axis_holding(index, map_anchor, current, widened_pool):
        anchor = map_anchor
        map_held = True
    elif have_siblings and nav_anchor and normalize_path_key(nav_anchor) != normalize_path_key(current):
        nav_seed, nav_action = hud_map_cells(index, nav_anchor)
        if locate_cell(current, nav_anchor, nav_seed, nav_action) is not None:
            anchor = nav_anchor
            nav_frozen = True
    # Is the row the widened pool?  While the map is held, that is settled by the
    # clip on screen being somewhere in the pool the row already drew — so the row
    # keeps its width across a loop's advances and across the loop ending.  Off any
    # hold, the widen applies only while its own clip is on screen, so a plain
    # auto-advance drops it.  Navigation walks the exact family (never widened), so
    # a nav-frozen map matches what the keys can reach.
    widen = bool(widened_pool) and (
        normalize_path_key(current) in pool_keys if map_held
        else normalize_path_key(current) == normalize_path_key(widen_clip)
    )
    if not have_siblings:
        seed = []
    elif widen and not nav_frozen:
        seed = _others(widened_pool, anchor)
    else:
        seed = _others(seed_family_members(index, anchor), anchor)
    action = _distinct_action_siblings(index, anchor) if have_siblings else []
    current_action = ""
    action_labels: tuple[str, ...] = ()
    playing = anchor
    if have_siblings:
        current_action = index.action_by_path.get(normalize_path_key(anchor), "")
        action_labels = tuple(
            index.action_by_path.get(normalize_path_key(member), "") for member in action
        )
        if map_held:
            axis = active_loop or _axis_holding(index, anchor, current, widened_pool)
            playing = _playing_member(index, anchor, current, seed, action, axis)
        elif nav_frozen:
            playing = current  # the live clip is exactly the cell to light
    return HudPanel(
        side=side,
        locked=locked,
        lock_label=_status_label(locked, active_loop, latest, filter_query, f_mode),
        is_favorite=is_favorite,
        current=anchor,
        seed_siblings=seed,
        action_siblings=action,
        current_action=current_action,
        action_labels=action_labels,
        seed_count=len(seed) + 1 if have_siblings else 0,
        action_count=len(action) + 1 if have_siblings else 0,
        filter_query=filter_query,
        active=active,
        active_loop=active_loop,
        playing=playing,
    )


@dataclass(frozen=True)
class SideInputs:
    """Everything one satellite's panel is built from.

    The two sides take an identical set of inputs, so they travel as one object
    each rather than as ``portrait_``/``landscape_`` twins of every field — which
    is what kept every caller, and this module's own helper, restating the whole
    list twice.
    """

    side: str
    sources: str = ""
    current: str = ""
    locked: bool = False
    filter_query: str = ""
    loop_axis: str = ""
    map_anchor: str = ""
    widen_clip: str = ""
    nav_anchor: str = ""
    latest: bool = False
    is_favorite: bool = False


def _side_panel(
    inputs: SideInputs, metadata_root: Path | None, f_mode: bool, active_side: str,
) -> HudPanel:
    index: GroupIndex | None = None
    if inputs.current:
        # must_contain=None: read the up-front index (see prime_group_indexes),
        # never a per-clip rebuild — the library does not change during a session,
        # so the map is drawn from memory the instant the clip changes.
        sources = inputs.sources
        index = cached_group_index(
            sources,
            paths_supplier=lambda: collect_video_files(sources),
            metadata_root=metadata_root,
            must_contain=None,
        )
    return build_hud_panel(
        inputs.side, locked=inputs.locked, current=inputs.current, index=index,
        filter_query=inputs.filter_query, loop_axis=inputs.loop_axis,
        map_anchor=inputs.map_anchor, widen_clip=inputs.widen_clip,
        nav_anchor=inputs.nav_anchor, latest=inputs.latest,
        is_favorite=inputs.is_favorite, f_mode=f_mode,
        active=active_side == inputs.side,
    )


def prime_group_indexes(sources: tuple[str, ...], metadata_root: Path | None) -> None:
    """Build both satellites' group indexes up front — behind the loading screen,
    before the first clip is drawn — so the map is instant on the first refresh
    and no later refresh pays for a rebuild.  The library is fixed for the run,
    so one build is enough (a Latest reload is what would extend it)."""
    for source in sources:
        if source:
            cached_group_index(
                source,
                paths_supplier=lambda captured=source: collect_video_files(captured),
                metadata_root=metadata_root,
                must_contain=None,
            )


def prewarm_thumbnails(
    sources: tuple[str, ...],
    cache_dir: str | Path,
    thumbnailer: Callable[[str, str | Path], object] = thumbnail_for,
    sleep_fn: Callable[[float], None] = time.sleep,
    pause_s: float = 0.05,
) -> None:
    """Extract and cache every library clip's thumbnail in the background, so the
    map paints from cache instead of blocking on a first-use frame grab.
    Idempotent — an already cached thumbnail is skipped.  Sleeps briefly between
    clips so decoding a big HEVC library never starves the session's own work
    (that starvation was showing up as multi-second blinks); run it off the main
    thread."""
    for source in sources:
        if not source:
            continue
        for path in collect_video_files(source):
            thumbnailer(path, cache_dir)
            sleep_fn(pause_s)


def build_panels(
    portrait: SideInputs, landscape: SideInputs, *,
    metadata_root: Path | None = None, f_mode: bool = False, active_side: str = "",
) -> tuple[HudPanel, HudPanel]:
    """Both satellites' HUD panels, indexing each side from its own sources.

    Each side's widen-clip and nav-anchor are threaded through as-is;
    ``build_hud_panel`` decides whether each still applies — the widen off a loop
    only while it is the clip on screen (so it auto-resets on navigation) and
    across a widened seed loop for every member of the looped pool, and the
    ``nav_anchor`` while the live clip is still one of its map cells.

    ``f_mode`` is unsided on purpose: one key narrows every player at once, so it
    goes to both panels or to neither.  ``active_side`` is unsided for the opposite
    reason — it *names* exactly one player, so at most one of these two panels can
    claim it, and neither does while the primary holds it.  A name rather than the
    dispatcher's slot number, because that is what a side is called everywhere else
    in here; the one translation lives where the number does.
    """
    return (_side_panel(portrait, metadata_root, f_mode, active_side),
            _side_panel(landscape, metadata_root, f_mode, active_side))


def panel_thumbnails(
    paths: list[str],
    cache_dir: str | Path,
    *,
    limit: int,
    thumbnailer: Callable[[str, str | Path], Path | None] = thumbnail_for,
) -> list[tuple[str, Path]]:
    """Up to *limit* ``(video_path, thumbnail_path)`` pairs, skipping unreadable clips.

    Iterates *paths* in order until *limit* thumbnails resolve, so a clip whose
    frame cannot be read simply drops out rather than leaving a gap.
    """
    resolved: list[tuple[str, Path]] = []
    for path in paths:
        if len(resolved) >= limit:
            break
        thumb = thumbnailer(path, cache_dir)
        if thumb is not None:
            resolved.append((path, thumb))
    return resolved

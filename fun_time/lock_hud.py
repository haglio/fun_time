from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from funestra.hud_placement import HudCorner, HudEdge
from funestra.hud_title import (
    LATEST_LABEL,
    SHUFFLE_LABEL,
    HudTitleFacts,
    hud_title,
    looping_label,
)
from funestra.modes import SatellitesMode

from fun_time.media_metadata import (
    GroupIndex,
    action_group_items,
    cached_group_index,
    normalize_path_key,
    seed_family_items,
    widened_seed_items,
)
from fun_time.modes import collect_video_files
from fun_time.thumbnail_cache import thumbnail_for


def _title(
    locked: bool, loop_axis: str, latest: bool, filter_query: str, favorites_filter: bool
) -> str:
    return hud_title(HudTitleFacts(
        playing_set=looping_label(loop_axis) if loop_axis else "",
        locked=locked,
        order=LATEST_LABEL if latest else SHUFFLE_LABEL,
        f_mode=favorites_filter,
        filters=(filter_query,),
    ))


@dataclass(frozen=True)
class SatelliteHud:
    funestra: str
    locked: bool
    title: str
    current: str
    seed_siblings: list[str]
    action_siblings: list[str]
    hud_corner: HudCorner = HudCorner.UPPER_LEFT
    hud_edge: HudEdge = HudEdge.LOWER
    hud_minimized: bool = False
    is_favorite: bool = False
    favorites_filter: bool = False
    latest: bool | None = None
    current_action: str = ""
    action_labels: tuple[str, ...] = ()
    seed_count: int = 0
    action_count: int = 0
    filter_query: str = ""
    active: bool = False
    active_loop: str = ""
    playing: str = ""
    mode: SatellitesMode | None = None
    origenerator_ready: bool = True
    in_vr: bool = False
    nothing_to_reset: bool = False
    has_other_versions: bool = False
    longer_than_a_step: bool = False
    crowned: bool = False
    has_osr2: bool = False
    has_funscript: bool = False


def _others(items: list[str], current: str) -> list[str]:
    key = normalize_path_key(current)
    return [item for item in items if normalize_path_key(item) != key]


def _distinct_action_siblings(index: GroupIndex, current: str) -> list[str]:
    current_action = index.act_of(current)
    reps: list[str] = []
    seen: set[str] = set()
    for item in action_group_items(index, current):
        action = index.act_of(item)
        if action == current_action or action in seen:
            continue
        seen.add(action)
        reps.append(item)
    return reps


SEED_LIMIT = 6
ACTION_LIMIT = 4

Cell = tuple[str, int]


_AXIS_STEPS = {"down": 1, "up": -1}


def navigate_cell(cell: Cell, direction: str, *, action_count: int) -> Cell:
    step = _AXIS_STEPS.get(direction)
    if step is None:
        return cell
    bucket, index = cell
    if bucket not in ("corner", "action"):
        return cell
    ring = action_count + 1
    position = ((0 if bucket == "corner" else index + 1) + step) % ring
    return ("corner", 0) if position == 0 else ("action", position - 1)


def locate_cell(current: str, corner: str, seeds: list[str], actions: list[str]) -> Cell | None:
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
    bucket, index = cell
    if bucket == "corner":
        return corner
    items = seeds if bucket == "seed" else actions if bucket == "action" else []
    return items[index] if 0 <= index < len(items) else ""


def nav_map_cells(
    index: GroupIndex, anchor: str, *, seed_limit: int = SEED_LIMIT, action_limit: int = ACTION_LIMIT
) -> tuple[list[str], list[str]]:
    seeds = _others(seed_family_items(index, anchor), anchor)[:seed_limit]
    actions = _distinct_action_siblings(index, anchor)[:action_limit]
    return seeds, actions


def _playing_item(
    index: GroupIndex, anchor: str, current: str, action: list[str], axis: str
) -> str:
    key = normalize_path_key
    if key(current) == key(anchor):
        return anchor
    if axis == "seed":
        return current
    if any(key(item) == key(current) for item in action):
        return current
    current_action = index.act_of(current)
    if current_action == index.act_of(anchor):
        return anchor
    for item in action:
        if index.act_of(item) == current_action:
            return item
    return anchor


def _map_anchor_in(group: list[str], anchor: str) -> str:
    key = normalize_path_key(anchor)
    if anchor and any(normalize_path_key(item) == key for item in group):
        return anchor
    return min(group, key=normalize_path_key)


def _axis_holding(index: GroupIndex, anchor: str, current: str, widened_pool: list[str]) -> str:
    key = normalize_path_key(current)
    if key == normalize_path_key(anchor):
        return "seed"
    row = widened_pool or seed_family_items(index, anchor)
    if any(normalize_path_key(item) == key for item in row):
        return "seed"
    if any(normalize_path_key(item) == key for item in action_group_items(index, anchor)):
        return "action"
    return ""


@dataclass(frozen=True)
class SatelliteInputs:
    funestra: str
    sources: str = ""
    current: str = ""
    locked: bool = False
    hud_corner: HudCorner = HudCorner.UPPER_LEFT
    hud_edge: HudEdge = HudEdge.LOWER
    hud_minimized: bool = False
    filter_query: str = ""
    loop_axis: str = ""
    nav_map_anchor: str = ""
    widen_video: str = ""
    nav_anchor: str = ""
    latest: bool = False
    favorites_filter: bool = False
    is_favorite: bool = False
    nothing_to_reset: bool = False
    has_other_versions: bool = False
    longer_than_a_step: bool = False
    mode: SatellitesMode | None = None
    has_osr2: bool = False
    has_funscript: bool = False
    active: bool = False
    origenerator_ready: bool = True
    in_vr: bool = False
    crowned: bool = False


@dataclass(frozen=True)
class _NavMapHold:
    anchor: str
    active_loop: str = ""
    held: bool = False
    nav_cell: Cell | None = None

    @property
    def nav_frozen(self) -> bool:
        return self.nav_cell is not None


@dataclass(frozen=True)
class _NavMap:
    seed: list[str]
    action: list[str]
    current_action: str
    action_labels: tuple[str, ...]
    playing: str
    seed_count: int
    action_count: int

    @classmethod
    def unmapped(cls, anchor: str) -> _NavMap:
        return cls([], [], "", (), anchor, 0, 0)


def _looping_group(
    index: GroupIndex, inputs: SatelliteInputs, widened_pool: list[str], pool_keys: set[str]
) -> list[str]:
    if inputs.loop_axis == "action":
        return action_group_items(index, inputs.current)
    if normalize_path_key(inputs.current) in pool_keys:
        return widened_pool
    return seed_family_items(index, inputs.current)


def _resolve_anchor(
    index: GroupIndex, inputs: SatelliteInputs, widened_pool: list[str], pool_keys: set[str]
) -> _NavMapHold:
    if inputs.loop_axis in ("seed", "action"):
        group = _looping_group(index, inputs, widened_pool, pool_keys)
        if len(group) > 1:
            return _NavMapHold(_map_anchor_in(group, inputs.nav_map_anchor), inputs.loop_axis, held=True)
    elif inputs.nav_map_anchor and _axis_holding(index, inputs.nav_map_anchor, inputs.current, widened_pool):
        return _NavMapHold(inputs.nav_map_anchor, held=True)
    elif inputs.nav_anchor and normalize_path_key(inputs.nav_anchor) != normalize_path_key(inputs.current):
        nav_seed, nav_action = nav_map_cells(index, inputs.nav_anchor)
        cell = locate_cell(inputs.current, inputs.nav_anchor, nav_seed, nav_action)
        if cell is not None:
            return _NavMapHold(inputs.nav_anchor, nav_cell=cell)
    return _NavMapHold(inputs.current)


def _axis_shown(
    index: GroupIndex, hold: _NavMapHold, inputs: SatelliteInputs, widened_pool: list[str]
) -> str:
    if hold.held:
        return hold.active_loop or _axis_holding(index, hold.anchor, inputs.current, widened_pool)
    return hold.nav_cell[0] if hold.nav_cell is not None else ""


def _row_is_widened(
    inputs: SatelliteInputs, widened_pool: list[str], pool_keys: set[str], *, held: bool
) -> bool:
    if not widened_pool:
        return False
    if held:
        return normalize_path_key(inputs.current) in pool_keys
    return normalize_path_key(inputs.current) == normalize_path_key(inputs.widen_video)


def _seed_row(index: GroupIndex, hold: _NavMapHold, widened_pool: list[str], *, widen: bool) -> list[str]:
    if widen and not hold.nav_frozen:
        return _others(widened_pool, hold.anchor)
    return _others(seed_family_items(index, hold.anchor), hold.anchor)


def _action_column(index: GroupIndex, hold: _NavMapHold, current: str, axis: str) -> list[str]:
    if hold.active_loop == "action":
        return _others(action_group_items(index, hold.anchor), hold.anchor)
    return _distinct_action_siblings(index, current if axis == "seed" else hold.anchor)


def _map_around(
    index: GroupIndex, hold: _NavMapHold, inputs: SatelliteInputs,
    widened_pool: list[str], pool_keys: set[str],
) -> _NavMap:
    axis = _axis_shown(index, hold, inputs, widened_pool)
    widen = _row_is_widened(inputs, widened_pool, pool_keys, held=hold.held)
    seed = _seed_row(index, hold, widened_pool, widen=widen)
    action = _action_column(index, hold, inputs.current, axis)
    if hold.held:
        playing = _playing_item(index, hold.anchor, inputs.current, action, axis)
    elif hold.nav_frozen:
        playing = inputs.current
    else:
        playing = hold.anchor
    return _NavMap(
        seed=seed,
        action=action,
        current_action=index.act_of(hold.anchor),
        action_labels=tuple(index.act_of(item) for item in action),
        playing=playing,
        seed_count=len(seed) + 1,
        action_count=len(action) + 1,
    )


def build_hud(inputs: SatelliteInputs, *, index: GroupIndex | None) -> SatelliteHud:
    if index is None or not inputs.current:
        hold, drawn = _NavMapHold(inputs.current), _NavMap.unmapped(inputs.current)
    else:
        widened_pool = widened_seed_items(index, inputs.widen_video) if inputs.widen_video else []
        pool_keys = {normalize_path_key(item) for item in widened_pool}
        hold = _resolve_anchor(index, inputs, widened_pool, pool_keys)
        drawn = _map_around(index, hold, inputs, widened_pool, pool_keys)
    return SatelliteHud(
        funestra=inputs.funestra,
        locked=inputs.locked,
        hud_corner=inputs.hud_corner,
        hud_edge=inputs.hud_edge,
        hud_minimized=inputs.hud_minimized,
        title=_title(inputs.locked, hold.active_loop, inputs.latest, inputs.filter_query, inputs.favorites_filter),
        is_favorite=inputs.is_favorite,
        favorites_filter=inputs.favorites_filter,
        latest=inputs.latest,
        current=hold.anchor,
        seed_siblings=drawn.seed,
        action_siblings=drawn.action,
        current_action=drawn.current_action,
        action_labels=drawn.action_labels,
        seed_count=drawn.seed_count,
        action_count=drawn.action_count,
        filter_query=inputs.filter_query,
        active=inputs.active,
        active_loop=hold.active_loop,
        playing=drawn.playing,
        mode=inputs.mode,
        origenerator_ready=inputs.origenerator_ready,
        in_vr=inputs.in_vr,
        nothing_to_reset=inputs.nothing_to_reset,
        has_other_versions=inputs.has_other_versions,
        longer_than_a_step=inputs.longer_than_a_step,
        crowned=inputs.crowned,
        has_osr2=inputs.has_osr2,
        has_funscript=inputs.has_funscript,
    )


def build_satellite_hud(inputs: SatelliteInputs, metadata_root: Path | None) -> SatelliteHud:
    index: GroupIndex | None = None
    if inputs.current:
        sources = inputs.sources
        index = cached_group_index(
            sources,
            paths_supplier=lambda: collect_video_files(sources),
            metadata_root=metadata_root,
            must_contain=None,
        )
    return build_hud(inputs, index=index)


def prime_group_indexes(sources: tuple[str, ...], metadata_root: Path | None) -> None:
    for source in sources:
        if source:
            cached_group_index(
                source,
                paths_supplier=lambda captured=source: collect_video_files(captured),
                metadata_root=metadata_root,
                must_contain=None,
            )


def hud_thumbnails(
    paths: list[str],
    cache_dir: str | Path,
    *,
    limit: int,
    thumbnailer: Callable[[str, str | Path], Path | None] = thumbnail_for,
) -> list[tuple[str, Path]]:
    resolved: list[tuple[str, Path]] = []
    for path in paths:
        if len(resolved) >= limit:
            break
        thumb = thumbnailer(path, cache_dir)
        if thumb is not None:
            resolved.append((path, thumb))
    return resolved

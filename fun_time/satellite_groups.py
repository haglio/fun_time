from __future__ import annotations

import logging
from pathlib import Path

from app_support.media_renditions import renditions
from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_OFF, LOCK_ON, RELOAD_PLAYLIST, play_file, step_version
from funestra.loop_cycle import LOCK, next_stop, standing_at
from funestra.video_families import actions_text
from funestra.video_groups import (
    GroupIndex,
    action_group_items,
    action_label,
    normalize_path_key,
    seed_family_items,
    widened_seed_items,
)

from .bridge_records import BridgeConfig, WindowOp
from .event_log import FAVORITE
from .fun_time_funestras import FunTimeFunestra
from .lock import a_lock_holds
from .lock_hud import cell_path, locate_cell, nav_map_cells, navigate_cell
from .media_metadata import (
    cached_group_index,
    recorded_for,
    reject_action,
    reset_group_index_cache,
)
from .modes import SatelliteBuild, collect_video_files, scripted_item, write_playlist_file
from .runtime_flow import satellite_browse_paths
from .satellite_control import read_satellite_status
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


def satellite_source(funestra: FunTimeFunestra) -> str:
    return FunTimeFunestra(funestra).label


def same_video(left: str, right: str) -> bool:
    return normalize_path_key(left) == normalize_path_key(right)


def satellite_current(config: BridgeConfig, funestra: FunTimeFunestra) -> str:
    return read_satellite_status(config.satellite(funestra).status_file).video


def send_satellite(config: BridgeConfig, funestra: FunTimeFunestra, verb: str) -> None:
    append_command(config.satellite(funestra).cmd_file, verb)


def play_video(config: BridgeConfig, funestra: FunTimeFunestra, path: str) -> None:
    send_satellite(config, funestra, play_file(scripted_item(path, config.regen_metadata_root)))


def lock_satellite(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, aimed_at: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    item = lock_on_what_was_meant(funestra, config, aimed_at,
                                  already_locked=state.satellite(funestra).locked)
    return a_lock_holds(item, state.with_satellite(funestra, locked=True), config)


def lock_on_what_was_meant(funestra: FunTimeFunestra, config: BridgeConfig, aimed_at: str = "", *,
                           already_locked: bool = False) -> str:
    on_screen = satellite_current(config, funestra)
    item = aimed_at or on_screen
    if not same_video(item, on_screen):
        play_video(config, funestra, item)
        logger.info("Lock moved Funestra %d onto %s", funestra, item)
    if not already_locked:
        send_satellite(config, funestra, LOCK_ON)
    logger.info("Locked %s satellite", FunTimeFunestra(funestra).label)
    return item


def cancel_lock(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig) -> BridgeState:
    if state.satellite(funestra).locked:
        send_satellite(config, funestra, LOCK_OFF)
    return state.with_satellite(funestra, locked=False)


def clear_satellite_grouping(state: BridgeState, funestra: FunTimeFunestra) -> BridgeState:
    return state.with_satellite(funestra, loop="", nav_map_anchor="", widen_video="")


def _satellite_group_index(funestra: FunTimeFunestra, config: BridgeConfig, current: str) -> GroupIndex:
    sources = config.satellite(funestra).sources
    return cached_group_index(
        sources,
        paths_supplier=lambda: collect_video_files(sources),
        metadata_root=config.regen_metadata_root,
        must_contain=current,
    )


def _next_action_sibling(index: GroupIndex, current: str) -> str | None:
    group_key = index.entry(current).action_key
    if group_key is None:
        return None
    items = [m for m in index.action_items[group_key] if Path(m).exists()]
    if len(items) <= 1:
        return None
    current_key = normalize_path_key(current)
    for position, item in enumerate(items):
        if normalize_path_key(item) == current_key:
            return items[(position + 1) % len(items)]
    return items[0]


def _next_seed_sibling(index: GroupIndex, current: str) -> str | None:
    current_entry = index.entry(current)
    current_action = current_entry.action
    if current_entry.seed_key is None:
        return None
    family, current_seed = current_entry.seed_key
    found: list[tuple[str, str]] = []
    for path in (m for m in index.seed_items.get(family, []) if Path(m).exists()):
        sibling = index.entry(path)
        candidate = sibling.seed_key
        if (
            candidate
            and candidate[0] == family
            and sibling.action == current_action
            and candidate[1] != current_seed
        ):
            found.append((candidate[1], path))
    found.sort()
    if not found:
        return None
    for seed, path in found:
        if seed > current_seed:
            return path
    return found[0][1]


def video_action_label(video_path: str, config: BridgeConfig) -> str:
    return actions_text(recorded_for(video_path, config.regen_metadata_root))


def cycle_variant(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
    target_path: str = "", *, kind: str,
) -> tuple[BridgeState, list[WindowOp]]:
    source = satellite_source(funestra)
    ops: list[WindowOp] = []
    current = target_path or satellite_current(config, funestra)
    if not current:
        return state, ops
    index = _satellite_group_index(funestra, config, current)
    if kind == "action":
        target = _next_action_sibling(index, current)
        missing_message = "No other actions"
    else:
        target = _next_seed_sibling(index, current)
        missing_message = "No other seeds"
    if target is None:
        ops.append(WindowOp(op="message", key=missing_message, source=source, level=logging.WARNING))
        return state, ops
    play_video(config, funestra, target)
    if kind == "action":
        action = action_label(index, target)
        if action:
            ops.append(WindowOp(op="message", key=f"Action: {action}", source=source))
    else:
        ops.append(WindowOp(op="message", key="Next seed", source=source))
    return state, ops


def more_seeds(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    source = satellite_source(funestra)
    current = target_path or satellite_current(config, funestra)
    if not current:
        return state, []
    index = _satellite_group_index(funestra, config, current)
    current_key = normalize_path_key(current)
    exact = {normalize_path_key(m) for m in seed_family_items(index, current)} - {current_key}
    wide = {normalize_path_key(m) for m in widened_seed_items(index, current)} - {current_key}
    if wide <= exact:
        return state, [WindowOp(op="message", key="Widening net failed", source=source, level=logging.WARNING)]
    state = state.with_satellite(funestra, widen_video=current)
    state, _loop_ops = group_loop(funestra, state, config, target_path=current, axis="seed")
    return state, [WindowOp(op="message", key="More seeds", source=source)]


def wrong_action(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    source = satellite_source(funestra)
    current = target_path or satellite_current(config, funestra)
    if not current:
        return state, []
    action = reject_action(current, config.regen_metadata_root)
    if not action:
        return state, [WindowOp(
            op="message", key="No action to remove", source=source, level=logging.WARNING
        )]
    reset_group_index_cache()
    logger.info("Wrong action on %s: removed %r", current, action)
    return state, [WindowOp(op="message", key=f"Action removed: {action}", source=source)]


def _loop_items(
    funestra: FunTimeFunestra, axis: str, state: BridgeState, config: BridgeConfig, current: str
) -> tuple[list[str], bool]:
    index = _satellite_group_index(funestra, config, current)
    widened = axis == "seed" and same_video(state.satellite(funestra).widen_video, current)
    gather = widened_seed_items if widened else (
        action_group_items if axis == "action" else seed_family_items
    )
    return [item for item in gather(index, current) if Path(item).exists()], widened


def group_loop(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, target_path: str = "",
    *, axis: str,
) -> tuple[BridgeState, list[WindowOp]]:
    source = satellite_source(funestra)
    ops: list[WindowOp] = []
    current = target_path or satellite_current(config, funestra)
    if not current:
        return state, ops
    items, widened = _loop_items(funestra, axis, state, config, current)
    if len(items) <= 1:
        return _locked_by_a_loop_control(funestra, clear_satellite_grouping(state, funestra), config,
                                         current)
    state = cancel_lock(funestra, state, config)
    items = [current] + [m for m in items if normalize_path_key(m) != normalize_path_key(current)]
    write_playlist_file(config.satellite(funestra).playlist_file, items,
                        metadata_root=config.regen_metadata_root)
    send_satellite(config, funestra, RELOAD_PLAYLIST)
    message = f"Loop {FunTimeFunestra(funestra).label}: {len(items)} {axis}s"
    logger.info(message)
    state = state.with_satellite(funestra, loop=axis, nav_map_anchor=current)
    state = state.with_satellite(funestra, widen_video=current if widened else "")
    ops.append(WindowOp(op="message", key=message, source=source))
    return state, ops


def lock_a_loop_left_with_one_video(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, video_that_left: str
) -> tuple[BridgeState, list[WindowOp]]:
    axis = state.satellite(funestra).loop
    if not axis:
        return state, []
    items, _widened = _loop_items(funestra, axis, state, config, video_that_left)
    if len(items) > 1:
        return state, []
    send_satellite(config, funestra, LOCK_ON)
    state = clear_satellite_grouping(state.with_satellite(funestra, locked=True), funestra)
    return state, [WindowOp(op="message", key="Locked", source=satellite_source(funestra))]


def _locked_by_a_loop_control(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, current: str
) -> tuple[BridgeState, list[WindowOp]]:
    state, ops = lock_satellite(funestra, state, config, current)
    return state, [WindowOp(op="message", key="Locked", source=satellite_source(funestra),
                            level=FAVORITE), *ops]


def loop_cycle(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    current = target_path or satellite_current(config, funestra)
    if not current:
        return state, []
    satellite = state.satellite(funestra)
    stop = next_stop(standing_at(loop=satellite.loop, locked=satellite.locked),
                     lambda axis: len(_loop_items(funestra, axis, state, config, current)[0]) > 1)
    if stop == LOCK:
        return _locked_by_a_loop_control(funestra, state, config, current)
    if stop:
        return group_loop(funestra, state, config, current, axis=stop)
    return _loop_and_lock_off(funestra, state, config)


def _loop_and_lock_off(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    looping = state.satellite(funestra).loop
    state = cancel_lock(funestra, state, config)
    if looping:
        return no_loop(funestra, state, config)
    return state, [WindowOp(op="message", key="Unlocked", source=satellite_source(funestra))]


def _browse_after(browse: list[str], current: str) -> list[str]:
    if not current or any(same_video(path, current) for path in browse):
        return browse
    return [current, *browse]


def is_single_video_loop(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig) -> bool:
    return bool(state.satellite(funestra).loop) and (
        read_satellite_status(config.satellite(funestra).status_file).playlist_length == 1)


def no_loop(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    current = satellite_current(config, funestra)
    satellite = state.satellite(funestra)
    browse = satellite_browse_paths(
        SatelliteBuild(
            sources=config.satellite(funestra).sources,
            favorites_filter=satellite.favorites_filter,
            recent=satellite.latest,
            filter_query=satellite.filter,
            enhancement=satellite.enhancement,
            media_root=config.regen_media_root,
        ),
        regen_metadata_root=config.regen_metadata_root,
    )
    if browse:
        write_playlist_file(config.satellite(funestra).playlist_file, _browse_after(browse, current),
                            metadata_root=config.regen_metadata_root)
        send_satellite(config, funestra, RELOAD_PLAYLIST)
    state = state.with_satellite(funestra, loop="")
    return state, [WindowOp(op="message", key="Loop off", source=satellite_source(funestra))]


def cycle_version(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
    target_path: str = "", *, delta: int,
) -> tuple[BridgeState, list[WindowOp]]:
    current = satellite_current(config, funestra)
    video = target_path or current
    family = renditions(video, config.regen_media_root)
    if not family:
        return state, [WindowOp(op="message", key="No other version",
                                source=satellite_source(funestra), level=logging.WARNING)]
    if target_path and not same_video(target_path, current):
        play_video(config, funestra, target_path)
    send_satellite(config, funestra, step_version(delta, family))
    return state, []


def switch_to_video(
    funestra: FunTimeFunestra, path: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    if not path:
        return state, []
    play_video(config, funestra, path)
    return state, [WindowOp(op="message", key="Switched", source=satellite_source(funestra))]


def navigate_hud(
    funestra: FunTimeFunestra, direction: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    source = satellite_source(funestra)
    current = satellite_current(config, funestra)
    if not current:
        return state, []
    index = _satellite_group_index(funestra, config, current)
    anchor = state.satellite(funestra).nav_anchor
    if anchor:
        seeds, actions = nav_map_cells(index, anchor)
        if locate_cell(current, anchor, seeds, actions) is None:
            anchor = ""
    if not anchor:
        anchor = current
    root = anchor
    seeds, actions = nav_map_cells(index, root)
    cell = locate_cell(current, root, seeds, actions) or ("corner", 0)
    if cell[0] == "seed" and direction in ("down", "up"):
        root, cell = current, ("corner", 0)
        seeds, actions = nav_map_cells(index, root)
    target_cell = navigate_cell(cell, direction, action_count=len(actions))
    target = cell_path(target_cell, root, seeds, actions)
    if target_cell == cell or not target or same_video(target, current):
        state = state.with_satellite(funestra, nav_anchor=anchor)
        return state, [WindowOp(op="message", key="No video that way", source=source, level=logging.WARNING)]
    state = state.with_satellite(funestra, nav_anchor=root)
    return switch_to_video(funestra, target, state, config)

from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial
from pathlib import Path

from funestra.file_channel import append_command
from funestra.flick_folder import move_to_trash
from funestra.funestra_verbs import JUMP_TO_FUNSCRIPT, LOCK_OFF, NEXT, PREV, TRASH, set_speed
from funestra.hud_placement import HudCorner
from funestra.hud_title import LATEST_LABEL, SHUFFLE_LABEL
from funestra.playback_rate import NORMAL_RATE

from .bridge_records import BridgeConfig, Route, SatelliteHandler, WindowOp
from .command_value import FILTER, Command
from .event_log import FAVORITE, MESSAGE
from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import slideshows_enhancement
from .lock import a_hosted_lock_holds, build_discard_plan, unfavorite
from .media_metadata import forget_indexed_video, is_favorite
from .mode_plan import EnhancementLevel
from .origenerator_switches import hosts
from .origenerator_trash import trash_file_line
from .osr2_commands import (
    FUNSCRIPT_MOVES,
    PLAYBACK_RATE_ACTS,
    percent_rate,
    skip_ahead_refused,
)
from .runtime_flow import SatelliteBuild, SatelliteFilterFlowResult, apply_satellite_filter
from .satellite_control import SatelliteStatus, read_satellite_status
from .satellite_groups import (
    cancel_lock,
    clear_satellite_grouping,
    cycle_variant,
    cycle_version,
    group_loop,
    is_single_video_loop,
    lock_a_loop_left_with_one_video,
    lock_on_what_was_meant,
    lock_satellite,
    loop_cycle,
    more_seeds,
    navigate_hud,
    no_loop,
    play_video,
    same_file_to_windows,
    satellite_current,
    satellite_source,
    send_satellite,
    switch_to_video,
    video_action_label,
    wrong_action,
)
from .shared_state import BridgeState, SatelliteState

logger = logging.getLogger(__name__)


def _lock_as_said(
    locked: bool, funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
    target_path: str = "",
) -> tuple[BridgeState, list[WindowOp]]:
    if hosts(funestra, state, config):
        return _lock_a_hosted_satellite(funestra, lock_said(funestra, locked), state, config,
                                   target_path)
    if state.satellite(funestra).locked == locked:
        return state, []
    return _set_lock(funestra, locked, state, config, target_path)


def _toggle_lock(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    if hosts(funestra, state, config):
        return _lock_a_hosted_satellite(funestra, _lock_pressed(funestra), state, config, target_path)
    return _set_lock(funestra, not state.satellite(funestra).locked, state, config, target_path)


def _lock_a_hosted_satellite(
    funestra: FunTimeFunestra, said: str, state: BridgeState, config: BridgeConfig,
    target_path: str,
) -> tuple[BridgeState, list[WindowOp]]:
    if said != lock_said(funestra, False):
        lock_on_what_was_meant(funestra, config, target_path)
    append_command(config.origenerator_cmd_file, said)
    return state, []


def _set_lock(
    funestra: FunTimeFunestra, locked: bool, state: BridgeState, config: BridgeConfig,
    target_path: str,
) -> tuple[BridgeState, list[WindowOp]]:
    if locked:
        return lock_satellite(funestra, state, config, target_path)
    state = cancel_lock(funestra, state, config)
    send_satellite(config, funestra, NEXT)
    logger.info("Unlocked %s satellite", FunTimeFunestra(funestra).label)
    return state, []


def _discard(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    locked = state.satellite(funestra).locked
    current_path = satellite_current(config, funestra)
    condemned = target_path or current_path
    if not condemned:
        logger.info("Nothing discarded on Funestra %d: it has not said which clip it is showing", funestra)
        return state, []
    already_moved_on = bool(target_path) and not same_file_to_windows(target_path, current_path)
    plan = build_discard_plan(funestra=funestra, current_path=condemned,
                              is_favorite=is_favorite(condemned, config.regen_metadata_root))
    if locked:
        send_satellite(config, funestra, LOCK_OFF)
    unfavorite(condemned, config)
    if plan.condemns:
        if already_moved_on:
            play_video(config, funestra, condemned)
        send_satellite(config, funestra, TRASH)
    elif not already_moved_on:
        send_satellite(config, funestra, NEXT)
    state = state.with_satellite(funestra, locked=False)
    lock_ops: list[WindowOp] = []
    if plan.condemns:
        _send_to_trash(config, condemned)
        state, lock_ops = lock_a_loop_left_with_one_video(funestra, state, config, condemned)
        forget_indexed_video(condemned)
    logger.info(plan.log_message)
    flashed = WindowOp(op="message", key=plan.message_text, source=satellite_source(funestra),
                      level=MESSAGE if plan.condemns else FAVORITE)
    return state, [flashed, *lock_ops]


def _send_to_trash(config: BridgeConfig, video: str) -> None:
    if config.hosts_origenerator:
        append_command(config.origenerator_cmd_file, trash_file_line(video))
    else:
        move_to_trash(Path(video), config.trash_dir)


def lock_said(funestra: FunTimeFunestra, locked: bool) -> str:
    return Command.to(funestra, "lock_on" if locked else "lock_off").text


def _lock_pressed(funestra: FunTimeFunestra) -> str:
    return Command.to(funestra, "lock").text


_NAV_DIRECTIONS = ("up", "down")

_HUD_NAV = frozenset(f"nav_{direction}" for direction in _NAV_DIRECTIONS)


def is_hud_nav_command(command: Command) -> bool:
    return command.action in _HUD_NAV


def _dispatch_reorder(
    funestra: FunTimeFunestra, recent: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    state = state.with_satellite(funestra, latest=recent)
    result = _rebuild_satellite(funestra, state.satellite(funestra).filter, state, config, start_at_top=True)
    state = state.with_satellite(funestra, locked=False)
    state = clear_satellite_grouping(state, funestra)
    funestra_name = FunTimeFunestra(funestra).label
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: %s (%d clips)", label, funestra_name, result.count)
    return state, [WindowOp(op="message", key=label, source=satellite_source(funestra))]


def _browse_at_defaults(satellite: SatelliteState) -> bool:
    fresh = SatelliteState()
    return replace(satellite, nav_anchor="", speed=fresh.speed, volume=fresh.volume,
                   muted=fresh.muted) == fresh


def satellite_at_defaults(satellite: SatelliteState, status: SatelliteStatus) -> bool:
    return _browse_at_defaults(satellite) and status.speed == NORMAL_RATE


def _reset(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if satellite_at_defaults(state.satellite(funestra),
                             read_satellite_status(config.satellite(funestra).status_file)):
        logger.info("Reset %s: already at its defaults", satellite_source(funestra))
        return state, []
    if not _browse_at_defaults(state.satellite(funestra)):
        state = cancel_lock(funestra, state, config)
        state = state.with_satellite(
            funestra, latest=False, filter="", favorites_filter=False,
            enhancement=EnhancementLevel.BOTH, nav_anchor="")
        state = clear_satellite_grouping(state, funestra)
        result = _rebuild_satellite(funestra, "", state, config, start_at_top=True)
        logger.info("Reset %s: %s", satellite_source(funestra), result.log_message)
    send_satellite(config, funestra, set_speed(NORMAL_RATE))
    return state, [WindowOp(op="message", key="Reset", source=satellite_source(funestra))]


def _rebuild_satellite(
    funestra: FunTimeFunestra, query: str, state: BridgeState, config: BridgeConfig,
    *, start_at_top: bool = False,
) -> SatelliteFilterFlowResult:
    satellite = state.satellite(funestra)
    channel = config.satellite(funestra)
    return apply_satellite_filter(
        funestra=funestra,
        channel=channel,
        build=SatelliteBuild(channel.sources, favorites_filter=satellite.favorites_filter,
                             recent=satellite.latest, filter_query=query,
                             enhancement=satellite.enhancement,
                             media_root=config.regen_media_root),
        start_at_top=start_at_top,
        regen_metadata_root=config.regen_metadata_root,
    )


def _set_filter(query: str, funestra: FunTimeFunestra, state: BridgeState,
                config: BridgeConfig, _target_path: str = "") -> tuple[BridgeState, list[WindowOp]]:
    result = _rebuild_satellite(funestra, query, state, config)
    if result.applied:
        state = state.with_satellite(funestra, filter=query)
        state = clear_satellite_grouping(state, funestra)
    logger.info(result.log_message)
    level = MESSAGE if result.applied else logging.WARNING
    return state, [WindowOp(op="message", key=result.log_message,
                            source=satellite_source(funestra), level=level)]


def _dispatch_enhancement(
    funestra: FunTimeFunestra, level: EnhancementLevel, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    if hosts(funestra, state, config):
        return slideshows_enhancement(funestra, level, state, config)
    if state.satellite(funestra).enhancement is level:
        return state, []
    narrowed = state.with_satellite(funestra, enhancement=level)
    result = _rebuild_satellite(funestra, narrowed.satellite(funestra).filter, narrowed, config)
    logger.info(result.log_message)
    if not result.applied:
        return state, [WindowOp(op="message", key=result.log_message,
                                source=satellite_source(funestra), level=logging.WARNING)]
    return narrowed, [WindowOp(op="message", key=level.flash, source=satellite_source(funestra),
                               level=MESSAGE)]


def _lock_action(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
                 target_path: str = "") -> tuple[BridgeState, list[WindowOp]]:
    current = target_path or satellite_current(config, funestra)
    if not current:
        return state, []
    action = video_action_label(current, config)
    if not action:
        return state, [WindowOp(op="message", key="No action metadata", source=satellite_source(funestra), level=logging.WARNING)]
    return _set_filter(action.lower(), funestra, state, config)


def _transport(verb: str, funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state = cancel_lock(funestra, state, config)
    if verb == NEXT and is_single_video_loop(funestra, state, config):
        state, _loop_off = no_loop(funestra, state, config)
    send_satellite(config, funestra, verb)
    return state, []


def _send_to_satellite(verb: str, funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if verb == JUMP_TO_FUNSCRIPT and (refused := skip_ahead_refused(funestra, state)):
        return state, refused
    send_satellite(config, funestra, verb)
    return state, []


def _no_loop(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return no_loop(funestra, state, config)


def _reorder(recent: bool, funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_reorder(funestra, recent, state, config)


def _nav(direction: str, funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
         _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return navigate_hud(funestra, direction, state, config)


def _play_video(command: Command, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return switch_to_video(command.satellite, command.argument, state, config)


def _lock_video(command: Command, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return lock_satellite(command.satellite, state, config, command.argument)


LOCK_HELD = "lock_held"


def _hosted_lock(command: Command, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return a_hosted_lock_holds(command.argument, state, config)


def _filter(command: Command, state: BridgeState, config: BridgeConfig,
            _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _set_filter(command.argument, command.satellite, state, config)


def _speed(command: Command, state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    verb = percent_rate(command.argument)
    if verb is None:
        return state, []
    send_satellite(config, command.satellite, verb)
    return state, []


def _enhancement(level: EnhancementLevel, funestra: FunTimeFunestra, state: BridgeState,
                 config: BridgeConfig, _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_enhancement(funestra, level, state, config)


THE_FUNESTRAS_OWN = frozenset({
    "minimize", "crown", "take_osr2", "kino_activate", "origenerator_activate",
    "lock", "lock_on", "lock_off", LOCK_HELD,
    "speed", *PLAYBACK_RATE_ACTS,
    "hud_minimize", "hud_restore", "hud_restore_at", "hud_left", "hud_right", "hud_up", "hud_down",
    *(f"hud_{corner}" for corner in HudCorner),
    "mute", "unmute", "volume_down", "volume_up",
    *(f"enhancement_{level}" for level in EnhancementLevel),
})


SATELLITE_ACTIONS: dict[str, SatelliteHandler] = {
    "prev": partial(_transport, PREV),
    "next": partial(_transport, NEXT),
    **{act: partial(_send_to_satellite, verb)
       for act, verb in (*PLAYBACK_RATE_ACTS.items(), *FUNSCRIPT_MOVES.items())},
    "lock": _toggle_lock,
    "lock_on": partial(_lock_as_said, True),
    "lock_off": partial(_lock_as_said, False),
    "trash": _discard,
    "cycle_action": partial(cycle_variant, kind="action"),
    "cycle_seed": partial(cycle_variant, kind="seed"),
    "cycle_version": partial(cycle_version, delta=1),
    "cycle_version_back": partial(cycle_version, delta=-1),
    "more_seeds": more_seeds,
    "wrong_action": wrong_action,
    "action_loop": partial(group_loop, axis="action"),
    "seed_loop": partial(group_loop, axis="seed"),
    "loop": loop_cycle,
    "no_loop": _no_loop,
    "lock_action": _lock_action,
    "latest": partial(_reorder, True),
    "shuffle": partial(_reorder, False),
    "reset": _reset,
    "no_filter": partial(_set_filter, ""),
    **{f"nav_{direction}": partial(_nav, direction) for direction in _NAV_DIRECTIONS},
    **{f"enhancement_{level}": partial(_enhancement, level) for level in EnhancementLevel},
}

SATELLITE_VALUED: dict[str, Route] = {
    "play_video": _play_video,
    "lock_video": _lock_video,
    LOCK_HELD: _hosted_lock,
    FILTER: _filter,
    "speed": _speed,
}

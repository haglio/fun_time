from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial

from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_ON, set_speed
from funestra.hud_title import LATEST_LABEL, SHUFFLE_LABEL
from funestra.inclusion_pair import Included, InclusionPair
from funestra.modes import LengthMode
from funestra.playback_rate import NORMAL_RATE

from main_funestra.genau_verbs import genau_orientations
from main_funestra.snippet_controller import SnippetState

from .bridge_records import IN_GENAU_MODE, BridgeConfig, Handler, Op, WindowOp, ignored
from .content import no_other_flicks
from .event_log import MESSAGE, SOURCE_MAIN
from .funestra_status import MainFunestraStatus, read_main_funestra_status
from .inclusion_pairs import (
    LENGTH,
    ORIENTATION,
    PROJECTION,
    length_mode,
    lengths_included,
)
from .main_funestra_commands import MAIN_FUNESTRA_CMD_MAP, MAIN_LOCK_COMMANDS
from .main_list import MainListSettings, new_shuffle_seed
from .main_list_builds import MainListBuild
from .mode_plan import EnhancementLevel, kino_shows
from .modes import VideoShapes
from .shared_state import BridgeState
from .vr_videos import shapes_verb

logger = logging.getLogger(__name__)


MAIN_RESET = "main_reset"


_GENAU_ORDER_CMD: dict[bool, str] = {True: "LATEST", False: "SHUFFLE"}


def _dispatch_main_reorder(
    recent: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    kino = kino_shows(state.main_mode)
    if kino:
        state = replace(state, main_latest=recent,
                        main_shuffle_seed=state.main_shuffle_seed if recent else new_shuffle_seed())
        _rebuild_the_main_list(state, config, start_at_top=True)
    else:
        state = replace(state, genau_latest=recent)
        append_command(config.genau_cmd_file, _GENAU_ORDER_CMD[recent])
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: the Main Funestra (%s)", label, "kino" if kino else "genau")
    return state, [WindowOp(op="message", key=label, source=SOURCE_MAIN)]


def _rebuild_the_main_list(state: BridgeState, config: BridgeConfig, *,
                           start_at_top: bool = False, says: str = "") -> None:
    config.main_list_builds.build(MainListBuild(
        main_list=config.main_list, settings=MainListSettings.of(state),
        main_funestra_cmd_file=config.main_funestra_cmd_file,
        on_screen=on_the_main_funestra(config), start_at_top=start_at_top, says=says))


def on_the_main_funestra(config: BridgeConfig) -> str:
    return read_main_funestra_status(config.main_funestra_status_file).video


def main_video_shapes(state: BridgeState, config: BridgeConfig) -> VideoShapes:
    return config.main_list.shapes(MainListSettings.of(state))


def genau_flick_shapes(state: BridgeState, config: BridgeConfig) -> VideoShapes:
    return VideoShapes(vr_dirs=config.genau_vr_flicks,
                       plays_vr=state.genau_plays_vr,
                       plays_flat=state.genau_plays_flat)


@dataclass(frozen=True)
class _Inclusions:
    kinds: str
    read: Callable[[BridgeState], Included]
    write: Callable[[BridgeState, Included], BridgeState]
    offered: Callable[[BridgeState, BridgeConfig], bool] = lambda _state, _config: True
    tell_genau: Callable[[BridgeConfig, Included], None] | None = None
    a_compilation_is_left_by_asking_again: bool = False


def _shapes_offered_on_the_main_funestra(state: BridgeState, config: BridgeConfig) -> bool:
    return main_video_shapes(state, config).offered


def _shapes_offered_in_genau(state: BridgeState, config: BridgeConfig) -> bool:
    return genau_flick_shapes(state, config).offered


def _tell_genau_its_shapes(config: BridgeConfig, wanted: Included) -> None:
    plays_flat, plays_vr = wanted
    append_command(config.genau_cmd_file, shapes_verb(plays_vr=plays_vr, plays_flat=plays_flat))


def _tell_genau_its_orientations(config: BridgeConfig, wanted: Included) -> None:
    append_command(config.main_funestra_cmd_file, genau_orientations(wanted))


_INCLUSIONS_ON_THE_MAIN_FUNESTRA: dict[InclusionPair, _Inclusions] = {
    PROJECTION: _Inclusions(
        "shapes",
        read=lambda state: (state.main_plays_flat, state.main_plays_vr),
        write=lambda state, wanted: replace(
            state, main_plays_flat=wanted[0], main_plays_vr=wanted[1]),
        offered=_shapes_offered_on_the_main_funestra),
    LENGTH: _Inclusions(
        "lengths",
        read=lambda state: lengths_included(state.main_length_mode),
        write=lambda state, wanted: replace(state, main_length_mode=length_mode(wanted)),
        a_compilation_is_left_by_asking_again=True),
    ORIENTATION: _Inclusions(
        "orientations",
        read=lambda state: (state.main_plays_portrait, state.main_plays_landscape),
        write=lambda state, wanted: replace(
            state, main_plays_portrait=wanted[0], main_plays_landscape=wanted[1])),
}


_INCLUSIONS_IN_GENAU: dict[InclusionPair, _Inclusions] = {
    PROJECTION: _Inclusions(
        "shapes",
        read=lambda state: (state.genau_plays_flat, state.genau_plays_vr),
        write=lambda state, wanted: replace(
            state, genau_plays_flat=wanted[0], genau_plays_vr=wanted[1]),
        offered=_shapes_offered_in_genau,
        tell_genau=_tell_genau_its_shapes),
    ORIENTATION: _Inclusions(
        "orientations",
        read=lambda state: (state.genau_plays_portrait, state.genau_plays_landscape),
        write=lambda state, wanted: replace(
            state, genau_plays_portrait=wanted[0], genau_plays_landscape=wanted[1]),
        tell_genau=_tell_genau_its_orientations),
}


def _dispatch_inclusion(
    pair: InclusionPair, wanted: Included, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    on_the_main_funestra = kino_shows(state.main_mode)
    inclusions = (_INCLUSIONS_ON_THE_MAIN_FUNESTRA if on_the_main_funestra
                  else _INCLUSIONS_IN_GENAU).get(pair)
    if inclusions is None:
        return ignored(state, IN_GENAU_MODE)
    if not inclusions.offered(state, config):
        logger.info("%s has nothing but one kind in this session; %s filter ignored",
                    "The Main Funestra" if on_the_main_funestra else "Genau", inclusions.kinds)
        return state, []
    if inclusions.read(state) == wanted and not (
            inclusions.a_compilation_is_left_by_asking_again
            and read_main_funestra_status(config.main_funestra_status_file).compilation):
        return state, []
    state = inclusions.write(state, wanted)
    logger.info("%s %s: %s", "Main Funestra" if on_the_main_funestra else "Genau",
                inclusions.kinds, pair.flash(wanted) or "neither")
    if on_the_main_funestra:
        _rebuild_the_main_list(state, config, says=pair.flash(wanted))
        return state, [WindowOp(op=Op.NARROWING_ANSWERS)]
    if not any(wanted):
        append_command(config.genau_cmd_file, LOCK_ON)
        return state, [WindowOp(op="message", key=no_other_flicks(), source=SOURCE_MAIN,
                                level=logging.WARNING)]
    inclusions.tell_genau(config, wanted)
    return state, [WindowOp(op=Op.NARROWING_ANSWERS)]


def _the_main_list_is_narrowed_or_ordered(state: BridgeState, config: BridgeConfig) -> bool:
    return (state.main_scripted_filter or state.main_latest
            or main_video_shapes(state, config).narrows
            or state.main_length_mode is not LengthMode.MIXED
            or state.main_enhancement is not EnhancementLevel.BOTH
            or not (state.main_plays_portrait and state.main_plays_landscape))


_ENHANCEMENT_COMMANDS: dict[str, EnhancementLevel] = {
    f"main_enhancement_{level}": level for level in EnhancementLevel
}


def _dispatch_enhancement(
    level: EnhancementLevel, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    if not kino_shows(state.main_mode):
        return ignored(state, IN_GENAU_MODE)
    if state.main_enhancement is level:
        return state, []
    state = replace(state, main_enhancement=level)
    _rebuild_the_main_list(state, config)
    logger.info("Main Funestra enhancement: %s", level)
    return state, [WindowOp(op="message", key=level.flash, source=SOURCE_MAIN, level=MESSAGE)]


def main_funestra_at_defaults(
    state: BridgeState, config: BridgeConfig, status: MainFunestraStatus
) -> bool:
    if _the_main_list_is_narrowed_or_ordered(state, config):
        return False
    if not kino_shows(state.main_mode):
        return True
    return (not status.locked and status.snippet_state is SnippetState.NORMAL and status.speed == NORMAL_RATE
            and not status.compilation)


_MAIN_FUNESTRA_RESET_VERBS = (
    MAIN_FUNESTRA_CMD_MAP["main_funestra_snippet_cancel"],
    MAIN_LOCK_COMMANDS["main_lock_off"],
    set_speed(NORMAL_RATE),
)


def _dispatch_main_reset(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    status = read_main_funestra_status(config.main_funestra_status_file)
    if main_funestra_at_defaults(state, config, status):
        logger.info("Reset Main Funestra: already at its defaults")
        return state, []
    if _the_main_list_is_narrowed_or_ordered(state, config) or status.compilation:
        state = MainListSettings(shuffle_seed=new_shuffle_seed()).into(state)
        _rebuild_the_main_list(state, config, start_at_top=True)
    if kino_shows(state.main_mode):
        for verb in _MAIN_FUNESTRA_RESET_VERBS:
            append_command(config.main_funestra_cmd_file, verb)
    logger.info("Reset Main Funestra")
    return state, [WindowOp(op="message", key="Reset", source=SOURCE_MAIN)]


def _include(pair: InclusionPair, wanted: Included, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_inclusion(pair, wanted, state, config)


def _enhancement(level: EnhancementLevel, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_enhancement(level, state, config)


def _main_reset(state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_main_reset(state, config)


_MAIN_REORDER_COMMANDS = {"main_latest": True, "main_shuffle": False}


def _main_reorder(recent: bool, state: BridgeState, config: BridgeConfig,
                  _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_main_reorder(recent, state, config)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_main_reorder, recent) for cmd, recent in _MAIN_REORDER_COMMANDS.items()},
    **{cmd: partial(_include, pair, wanted)
       for pair in (PROJECTION, LENGTH, ORIENTATION)
       for cmd, wanted in pair.asked.items()},
    **{cmd: partial(_enhancement, level) for cmd, level in _ENHANCEMENT_COMMANDS.items()},
    MAIN_RESET: _main_reset,
}

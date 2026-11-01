from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial

from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_ON
from funestra.hud_title import LATEST_LABEL, SHUFFLE_LABEL
from funestra.modes import LengthMode, LoopState

from main_funestra.genau_verbs import genau_orientations

from .bridge_records import IN_GENAU_MODE, BridgeConfig, Handler, Op, WindowOp, ignored
from .content import no_other_flicks
from .event_log import NOTICE, SOURCE_MAIN
from .funestra_status import MainFunestraStatus, read_main_funestra_status
from .inclusion_pairs import (
    LENGTH,
    ORIENTATION,
    PROJECTION,
    Included,
    InclusionPair,
    length_mode,
    lengths_included,
)
from .main_funestra_commands import MAIN_FUNESTRA_CMD_MAP, MAIN_LOCK_COMMANDS
from .main_list import MainListSettings, new_shuffle_seed
from .mode_plan import EnhancementLevel, kino_shows
from .modes import VideoShapes
from .osr2_commands import NORMAL_SPEED
from .runtime_flow import rebuild_main_list
from .shared_state import BridgeState
from .vr_videos import shapes_verb

logger = logging.getLogger(__name__)


MAIN_RESET = "main_reset"


# The same two orders as Genau answers to, keyed by ``recent``.  Every other
# Funestra is handed a rewritten playlist file; Genau owns its own sequence and
# rescans its clips folder for itself, so its order crosses as a verb.
_GENAU_ORDER_CMD: dict[bool, str] = {True: "LATEST", False: "SHUFFLE"}


def _dispatch_main_reorder(
    recent: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Reload the Main Funestra in a fresh order — Latest (newest-first) or Shuffle.

    To whichever Funestra owns the main slot's screen, the split the lock makes (see
    ``MAIN_LOCK_COMMANDS``) and for the same reason: a browse order is about what
    you are looking at.  Sent to the Main Funestra regardless, "main latest" said in
    genau mode rewrote a playlist for a Funestra neither on screen nor playing.  Both
    branches rescan as they go, which is most of what "latest" is for.

    The Main Funestra's playlist is ours to write, so that branch rewrites the file
    and hands it the same RELOAD_PLAYLIST an F-mode change gets, from the top of
    the new order -- a reorder filters nothing out, so it would otherwise carry on
    from where it sits and the arrivals would never come up.  Genau owns its own
    sequence, so it is told the order and rescans its clips folder itself.

    Each Funestra's order is remembered under its own flag, since ``main_latest``
    describes the playlist file we built and a Genau reorder recorded there would
    light "Latest" over a playlist nobody reordered.
    """
    kino = kino_shows(state.main_mode)
    if kino:
        state = replace(state, main_latest=recent,
                        main_shuffle_seed=state.main_shuffle_seed if recent else new_shuffle_seed())
        _rebuild_the_main_list(state, config, start_at_top=True)
    else:
        state = replace(state, genau_latest=recent)
        append_command(config.genau_cmd_file, _GENAU_ORDER_CMD[recent])
    # The order's own word alone, and self-reported — see _dispatch_reorder.
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: the Main Funestra (%s)", label, "kino" if kino else "genau")
    return state, [WindowOp(op="notice", key=label, source=SOURCE_MAIN)]


def _rebuild_the_main_list(state: BridgeState, config: BridgeConfig, *,
                           start_at_top: bool = False, says: str = "") -> None:
    rebuild_main_list(config.main_list, MainListSettings.of(state),
                      main_funestra_cmd_file=config.main_funestra_cmd_file,
                      on_screen=on_the_main_funestra(config),
                      start_at_top=start_at_top, builds=config.main_list_builds, says=says)


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
    """Asking for what is already playing changes nothing: a rebuild
    reshuffles, and this must not become a second way to reshuffle."""
    on_the_main_funestra = kino_shows(state.main_mode)
    inclusions = (_INCLUSIONS_ON_THE_MAIN_FUNESTRA if on_the_main_funestra
                  else _INCLUSIONS_IN_GENAU).get(pair)
    if inclusions is None:
        return ignored(state, IN_GENAU_MODE)
    if not inclusions.offered(state, config):
        logger.info("Nothing but one kind of %s in this session; %s filter ignored",
                    "video" if on_the_main_funestra else "flick", inclusions.kinds)
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
        return state, [WindowOp(op="notice", key=no_other_flicks(), source=SOURCE_MAIN,
                                level=logging.WARNING)]
    inclusions.tell_genau(config, wanted)
    return state, [WindowOp(op=Op.NARROWING_ANSWERS)]


def _the_main_list_is_narrowed_or_ordered(state: BridgeState, config: BridgeConfig) -> bool:
    return (state.main_scripted_filter or state.main_latest
            or main_video_shapes(state, config).narrows
            or state.main_length_mode is not LengthMode.MIXED
            or state.main_enhancement is not EnhancementLevel.BOTH
            or not (state.main_plays_portrait and state.main_plays_landscape))


# Which of what the Main Funestra plays gets through, by whether an enhanced
# version exists: one verb per level, as its shapes have one per state.
_ENHANCEMENT_COMMANDS: dict[str, EnhancementLevel] = {
    f"main_enhancement_{level}": level for level in EnhancementLevel
}


def _dispatch_enhancement(
    level: EnhancementLevel, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Asking for the level already playing changes nothing: a rebuild
    reshuffles, and this must not become a second way to reshuffle."""
    if not kino_shows(state.main_mode):
        return ignored(state, IN_GENAU_MODE)
    if state.main_enhancement is level:
        return state, []
    state = replace(state, main_enhancement=level)
    _rebuild_the_main_list(state, config)
    logger.info("Main Funestra enhancement: %s", level)
    return state, [WindowOp(op="notice", key=level.flash, source=SOURCE_MAIN, level=NOTICE)]


def main_funestra_at_defaults(
    state: BridgeState, config: BridgeConfig, status: MainFunestraStatus
) -> bool:
    if _the_main_list_is_narrowed_or_ordered(state, config):
        return False
    if not kino_shows(state.main_mode):
        return True
    return (not status.locked and status.loop_state is LoopState.NORMAL and status.speed == 1.0
            and not status.compilation)


_MAIN_FUNESTRA_RESET_VERBS = (
    MAIN_FUNESTRA_CMD_MAP["main_funestra_loop_cancel"],
    MAIN_LOCK_COMMANDS["main_lock_off"],
    NORMAL_SPEED,
)


def _dispatch_main_reset(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Put the Main Funestra back to its defaults, as a satellite's reset does."""
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
    return state, [WindowOp(op="notice", key="Reset", source=SOURCE_MAIN)]


def _include(pair: InclusionPair, wanted: Included, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_inclusion(pair, wanted, state, config)


def _enhancement(level: EnhancementLevel, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_enhancement(level, state, config)


def _main_reset(state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_main_reset(state, config)


# The Main Funestra's two browse orderings: Latest reloads newest-first, Shuffle
# reshuffles.  Both reach whichever Funestra holds the main slot's screen.
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

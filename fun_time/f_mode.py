from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial

from funestra.file_channel import append_command
from funestra.hud_title import F_MODE_LABEL

from main_funestra.genau_verbs import genau_f_mode

from .aiming import FUNESTRA_SOURCES
from .bridge_records import BridgeConfig, Handler, WindowOp
from .event_log import FAVORITE, NOTICE, SOURCE_SYSTEM
from .funestra_handover import show_f_mode
from .main_list import MainListSettings
from .main_list_commands import on_the_main_funestra
from .mode_plan import kino_shows
from .origenerator_switches import hosts
from .room_funestras import RoomFunestra
from .runtime_flow import FMODE_FUNESTRAS, SatelliteFmodeInputs, apply_fmode
from .satellite_groups import cancel_lock, clear_side_grouping
from .shared_state import BridgeState
from .voice_commands import FAVORITES_PHRASE, say_command

logger = logging.getLogger(__name__)


def _sided_fmode_command(funestra: RoomFunestra, target: bool | None) -> str:
    return f"{funestra.label}_fmode" + {None: "", True: "_on", False: "_off"}[target]


# Which Funestras each F-mode command reaches, and what it sets them to — None for
# the toggles, True/False for the forms that assert a state and so cannot land on
# the opposite of what was asked when a phrase is misheard twice.  The bare
# command — the F key, the spoken "f mode" — still means all three at once; naming
# a Funestra reaches that one alone, off its own HUD button or its own spoken
# phrase.  ``both_fmode`` never arrives here: the dispatch loop expands every
# both_* into its portrait/landscape pair first.
_FMODE_COMMANDS: dict[str, tuple[tuple[RoomFunestra, ...], bool | None]] = {
    "fmode_toggle": (FMODE_FUNESTRAS, None),
    "fmode_on": (FMODE_FUNESTRAS, True),
    "fmode_off": (FMODE_FUNESTRAS, False),
    **{_sided_fmode_command(funestra, target): ((funestra,), target)
       for funestra in RoomFunestra for target in (None, True, False)},
    **{say_command(funestra.label, FAVORITES_PHRASE): ((funestra,), None)
       for funestra in RoomFunestra.SATELLITES},
}


def _funestra_f_mode(state: BridgeState, config: BridgeConfig, funestra: RoomFunestra) -> bool | None:
    if funestra is RoomFunestra.MAIN:
        return state.main_scripted_filter if kino_shows(state.main_mode) else state.genau_favorites_filter
    if hosts(funestra, state, config):
        return show_f_mode(config.satellite(funestra))
    return state.satellite(funestra).favorites_filter


def _with_f_mode(state: BridgeState, funestras: tuple[RoomFunestra, ...], enabled: bool) -> BridgeState:
    """*state* with each of *Funestras* put into F-mode, or out of it."""
    for funestra in funestras:
        if funestra is not RoomFunestra.MAIN:
            state = state.with_satellite(funestra, favorites_filter=enabled)
        elif kino_shows(state.main_mode):
            state = replace(state, main_scripted_filter=enabled)
        else:
            state = replace(state, genau_favorites_filter=enabled)
    return state


def _genau_among(funestras: tuple[RoomFunestra, ...], state: BridgeState) -> tuple[RoomFunestra, ...]:
    return () if kino_shows(state.main_mode) else tuple(
        funestra for funestra in funestras if funestra is RoomFunestra.MAIN)


def every_funestra_in_f_mode(state: BridgeState, config: BridgeConfig,
                           funestras: tuple[RoomFunestra, ...] = FMODE_FUNESTRAS) -> bool:
    return all(f_mode for funestra in funestras
               if (f_mode := _funestra_f_mode(state, config, funestra)) is not None)


def _next_f_mode(state: BridgeState, config: BridgeConfig, funestras: tuple[RoomFunestra, ...]) -> bool:
    """What a toggle over *Funestras* should set them all to.

    One Funestra is an ordinary flip.  Several — the F key, or a spoken "f mode" —
    turn on unless every one of them is already on, so the key that means "narrow
    everything" can never leave half the room narrowed and half not: it either
    completes the narrowing or lifts it.
    """
    return not every_funestra_in_f_mode(state, config, funestras)


def _set_the_hosted_shows_f_mode(funestras: tuple[RoomFunestra, ...], enabled: bool,
                                 config: BridgeConfig) -> None:
    for funestra in funestras:
        if show_f_mode(config.satellite(funestra)) not in (None, enabled):
            append_command(config.origenerator_cmd_file, _sided_fmode_command(funestra, enabled))


def _dispatch_fmode(
    funestras: tuple[RoomFunestra, ...], target: bool | None,
    state: BridgeState, config: BridgeConfig,
) -> tuple[BridgeState, list[WindowOp]]:
    """Put *Funestras* into F-mode or out of it, rebuilding only what moves.

    *target* is the state to assert, or None to toggle.  A Funestra already in the
    asked-for state is left out of the rebuild entirely: its playlist file is not
    rewritten, so "portrait f mode on" said twice does not reshuffle the queue the
    first one built.
    """
    enabled = _next_f_mode(state, config, funestras) if target is None else target
    hosted = tuple(funestra for funestra in funestras if hosts(funestra, state, config))
    changed = tuple(funestra for funestra in funestras
                    if funestra not in hosted and _funestra_f_mode(state, config, funestra) != enabled)
    _set_the_hosted_shows_f_mode(hosted, enabled, config)
    genau = _genau_among(changed, state)
    if genau:
        append_command(config.main_funestra_cmd_file, genau_f_mode(enabled))
    result = apply_fmode(
        funestras=tuple(funestra for funestra in changed if funestra not in genau),
        enabled=enabled,
        main_list=config.main_list,
        main_settings=MainListSettings.of(state),
        main_on_screen=on_the_main_funestra(config),
        state_dir=config.state_dir,
        main_funestra_cmd_file=config.main_funestra_cmd_file,
        main_builds=config.main_list_builds,
        satellites={
            funestra: SatelliteFmodeInputs(
                sources=config.satellite(funestra).sources,
                cmd_file=config.satellite(funestra).cmd_file,
                recent=state.satellite(funestra).latest,
                filter_query=state.satellite(funestra).filter,
            )
            for funestra in RoomFunestra.SATELLITES
        },
        regen_metadata_root=config.regen_metadata_root,
    )
    if result.funestras:
        logger.info(result.log_message)
    state = _with_f_mode(state, (*result.funestras, *genau), enabled)
    # A rebuilt satellite got a new queue, which drops its lock, its group loop and
    # the widened seed row that rode on the loop — the same as any other rebuild.
    for funestra in RoomFunestra.SATELLITES:
        if funestra in result.funestras:
            state = cancel_lock(funestra, state, config)
            state = clear_side_grouping(state, funestra)
    # Flash which way it went, on the display it went on — a sided F-mode reports
    # from that Funestra, the all-players one from the room.  It goes off the Funestras
    # *asked for*, not the ones that moved: "f mode" is a gesture at the room even
    # on the press where only one Funestra was left to narrow.  Enabling is green,
    # since what it narrows to is the favorites and the funscripts; disabling is
    # an ordinary white notice.
    source = FUNESTRA_SOURCES[funestras[0]] if len(funestras) == 1 else SOURCE_SYSTEM
    notice_op = WindowOp(
        op="notice",
        key=f"{F_MODE_LABEL} enabled" if enabled else f"{F_MODE_LABEL} disabled",
        source=source,
        level=FAVORITE if enabled else NOTICE,
    )
    return state, [notice_op]


def _fmode(funestras: tuple[str, ...], target: bool | None, state: BridgeState,
           config: BridgeConfig, _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_fmode(funestras, target, state, config)


HANDLERS: dict[str, Handler] = {
    cmd: partial(_fmode, funestras, target) for cmd, (funestras, target) in _FMODE_COMMANDS.items()
}

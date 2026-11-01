from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import replace

from funestra.file_channel import append_command

from . import (
    audio_commands,
    crown,
    f_mode,
    genau_commands,
    main_funestra_commands,
    main_list_commands,
    max_intensity,
    mode_switches,
    omnipause,
    osr2_commands,
    satellite_commands,
)
from .aiming import command_funestra
from .bridge_records import (
    IN_KINO_MODE,
    ON_THE_MAIN_FUNESTRA,
    BridgeConfig,
    Handler,
    WindowOp,
    ignored,
)
from .filter_vocab import decode_filter_command, set_command
from .fun_time_funestras import FunTimeFunestra
from .funestra_status import MainFunestraStatus
from .genau_commands import about_genaus_flick
from .hosted_contract import said_to_the_hosted_app, with_when_said
from .hud_moves import minimize_ops, placed_hud
from .main_list_commands import main_funestra_at_defaults
from .mode_plan import kino_shows
from .origenerator_switches import hosts, hosts_a_satellite
from .satellite_commands import (
    LOCK_STATE_COMMANDS,
    LOCK_TOGGLES,
    is_hud_nav_command,
    lock_said,
    satellite_at_defaults,
)
from .satellite_control import read_satellite_status
from .shared_state import BridgeState
from .voice_commands import said_to_a_satellite

logger = logging.getLogger(__name__)


def _addressed(funestra: FunTimeFunestra, command: str, state: BridgeState) -> BridgeState:
    state = replace(state, active_funestra=funestra)
    if funestra is FunTimeFunestra.MAIN or is_hud_nav_command(command):
        return state
    return state.with_satellite(funestra, nav_anchor="")


def _answered_ahead_of_the_handlers(
    command: str, state: BridgeState, config: BridgeConfig,
) -> tuple[BridgeState, list[WindowOp]] | None:
    if about_genaus_flick(command) and kino_shows(state.main_mode):
        return ignored(state, IN_KINO_MODE)
    parked = minimize_ops(command)
    if parked is not None:
        return state, parked
    placed = placed_hud(command, state, config)
    return None if placed is None else (placed, [])


def dispatch_command(
    command: str,
    state: BridgeState,
    config: BridgeConfig,
    *,
    target_path: str = "",
    spoken_at: float | None = None,
) -> tuple[BridgeState, list[WindowOp]]:
    funestra = command_funestra(command)
    if funestra is not None:
        state = _addressed(funestra, command, state)
    command = _a_lock_said_in_the_hosted_apps_words(command, state, config)
    answered = _answered_ahead_of_the_handlers(command, state, config)
    if answered is not None:
        return answered

    if halves := _said_to_satellites_in_two_modes(command, state, config):
        ops: list[WindowOp] = []
        for half in halves:
            state, more = dispatch_command(half, state, config, target_path=target_path,
                                           spoken_at=spoken_at)
            ops.extend(more)
        return state, ops

    routed = _origenerator_transport(command, state, config, spoken_at)
    if routed is not None:
        return state, routed

    handler = _HANDLERS.get(command)
    if handler is not None:
        return handler(state, config, target_path)

    for parse in _PARSED_FORMS:
        handled = parse(command, state, config, target_path)
        if handled is not None:
            return handled

    logger.warning("no handler for command %r", command)
    return state, []


_THE_FUNESTRAS_OWN = ("_minimize", "_crown", "_speed_", "_take_osr2", "_kino_activate",
                      "_origenerator_activate", "_mute", "_unmute", "_volume_", "_enhancement_")


_THE_FUNESTRAS_LOCK = frozenset({*LOCK_TOGGLES, *LOCK_STATE_COMMANDS})


def _satellite_said_to(command: str) -> FunTimeFunestra | None:
    head = command.removeprefix("filter_")
    return next((funestra for funestra in FunTimeFunestra.SATELLITES
                 if head.startswith(f"{funestra.label}_")), None)


def _about_a_satellite(command: str) -> bool:
    if _satellite_said_to(command) is None or command in _THE_FUNESTRAS_LOCK:
        return False
    verb = command.removeprefix("filter_").partition("|")[0]
    return not any(own in verb for own in _THE_FUNESTRAS_OWN)


def _said_to_each_satellite(command: str) -> tuple[str, ...]:
    target = decode_filter_command(command)
    if target is None or target[0] != "satellites":
        return (command,)
    return tuple(set_command(funestra.label, target[1]) for funestra in FunTimeFunestra.SATELLITES)


_SAID_TO_A_FUNESTRA_AND_A_SHOW: dict[str, tuple[str, str]] = {
    "portrait_no_filter": ("portrait", "clear filter"),
    "landscape_no_filter": ("landscape", "clear filter"),
}


def _words_for_the_hosted_app(command: str) -> tuple[str, str] | None:
    return said_to_a_satellite(command) or _SAID_TO_A_FUNESTRA_AND_A_SHOW.get(command)


def _a_lock_said_in_the_hosted_apps_words(command: str, state: BridgeState,
                                          config: BridgeConfig) -> str:
    said = said_to_a_satellite(command)
    if (said is None or said[1] not in config.origenerator_lock_phrases
            or not routes_to_origenerator(command, state, config)):
        return command
    return lock_said(FunTimeFunestra[said[0].upper()], True)


def routes_to_origenerator(command: str, state: BridgeState, config: BridgeConfig) -> bool:
    spoken = _words_for_the_hosted_app(command)
    if spoken is not None:
        return hosts(FunTimeFunestra[spoken[0].upper()], state, config)
    return all(_about_a_satellite(said) and hosts(_satellite_said_to(said), state, config)
               for said in _said_to_each_satellite(command))


def _said_to_satellites_in_two_modes(command: str, state: BridgeState,
                                config: BridgeConfig) -> tuple[str, ...]:
    said = _said_to_each_satellite(command)
    if len(said) <= 1 or not all(_about_a_satellite(each) for each in said):
        return ()
    hosted = {hosts(_satellite_said_to(each), state, config) for each in said}
    return said if len(hosted) > 1 else ()


def _origenerator_transport(
    command: str, state: BridgeState, config: BridgeConfig, spoken_at: float | None
) -> list[WindowOp] | None:
    if not routes_to_origenerator(command, state, config):
        return None
    spoken = _words_for_the_hosted_app(command)
    lines = ([said_to_the_hosted_app(*spoken)] if spoken is not None
             else _said_to_each_satellite(command))
    for line in lines:
        append_command(config.origenerator_cmd_file,
                       with_when_said(line, spoken_at, config.origenerator_said_at_suffix))
    return []


def app_at_defaults(state: BridgeState, config: BridgeConfig, status: MainFunestraStatus) -> bool:
    return (main_funestra_at_defaults(state, config, status)
            and not hosts_a_satellite(state, config)
            and all(satellite_at_defaults(
                        state.satellite(funestra),
                        read_satellite_status(config.satellite(funestra).status_file))
                    for funestra in FunTimeFunestra.SATELLITES))


def _parsed_unresolved_active(command: str, state: BridgeState, _config: BridgeConfig,
                              _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if not command.startswith("active_"):
        return None
    return ignored(state, ON_THE_MAIN_FUNESTRA)


def _parsed_words_for_a_show_that_is_not_up(
        command: str, state: BridgeState, _config: BridgeConfig,
        _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if said_to_a_satellite(command) is None:
        return None
    return ignored(state, IN_KINO_MODE)


def _bound_once(tables: Iterable[dict[str, Handler]]) -> dict[str, Handler]:
    handlers: dict[str, Handler] = {}
    for table in tables:
        twice = handlers.keys() & table.keys()
        if twice:
            raise ValueError(f"commands bound twice: {sorted(twice)}")
        handlers.update(table)
    return handlers


_DOMAINS = (satellite_commands, main_funestra_commands, main_list_commands, f_mode,
            audio_commands, omnipause, mode_switches, osr2_commands, genau_commands, crown)

_HANDLERS: dict[str, Handler] = _bound_once(domain.HANDLERS for domain in _DOMAINS)

_PARSED_FORMS = (
    *satellite_commands.PARSED_FORMS,
    *audio_commands.PARSED_FORMS,
    *max_intensity.PARSED_FORMS,
    *osr2_commands.PARSED_FORMS,
    *genau_commands.PARSED_FORMS,
    _parsed_unresolved_active,
    _parsed_words_for_a_show_that_is_not_up,
)

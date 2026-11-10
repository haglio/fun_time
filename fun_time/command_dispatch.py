from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import replace

from . import (
    audio_commands,
    crown,
    f_mode,
    genau_commands,
    hud_moves,
    main_funestra_commands,
    main_list_commands,
    max_intensity,
    mode_switches,
    omnipause,
    osr2_commands,
    satellite_commands,
)
from .bridge_records import (
    IN_KINO_MODE,
    ON_THE_MAIN_FUNESTRA,
    BridgeConfig,
    Handler,
    Result,
    Route,
    SatelliteHandler,
    ignored,
)
from .command_value import SAY, Command, Scope, parse_command
from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import show_has_something_to_reset
from .funestra_status import MainFunestraStatus
from .genau_commands import about_genaus_flick
from .hosted_contract import written_for_the_hosted_app
from .hosted_words import (
    a_lock_said_in_the_hosted_apps_words,
    about_a_satellite,
    line_said_bare_to_the_hosted_app,
    routes_to_origenerator,
    told_to_the_hosted_app,
)
from .main_list_commands import main_funestra_at_defaults
from .mode_plan import kino_shows
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


def dispatch_command(
    command: str | Command,
    state: BridgeState,
    config: BridgeConfig,
    *,
    target_path: str = "",
    spoken_at: float | None = None,
    hosted: bool | None = None,
) -> Result:
    if isinstance(command, str):
        command = parse_command(command)
    if command.funestra is not None:
        state = replace(state, active_funestra=command.funestra)
    reworded = a_lock_said_in_the_hosted_apps_words(command, config)
    if reworded is not command:
        command, hosted = reworded, None
    if about_genaus_flick(command) and kino_shows(state.main_mode):
        return ignored(state, IN_KINO_MODE)
    if hosted is None:
        hosted = routes_to_origenerator(command)
    if hosted:
        return state, told_to_the_hosted_app(command, state, config, spoken_at)

    if len(command.halves()) > 1:
        return _half_by_half(command, state, config, target_path, spoken_at)
    route = ROUTES.get(command.key, _unanswered)
    return route(command, state, config, target_path)


def _half_by_half(command: Command, state: BridgeState, config: BridgeConfig,
                  target_path: str, spoken_at: float | None) -> Result:
    ops = []
    for half in command.halves():
        state, more = dispatch_command(half, state, config, target_path=target_path,
                                       spoken_at=spoken_at)
        ops.extend(more)
    return state, ops


def _unanswered(command: Command, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> Result:
    if command.said_to is Scope.ACTIVE:
        bare = line_said_bare_to_the_hosted_app(command)
        return ((state, written_for_the_hosted_app([bare], config, None)) if bare
                else ignored(state, ON_THE_MAIN_FUNESTRA))
    if command.action == SAY:
        return ignored(state, IN_KINO_MODE)
    logger.warning("no handler for command %r", command.text)
    return state, []


def app_at_defaults(state: BridgeState, config: BridgeConfig, status: MainFunestraStatus) -> bool:
    return (main_funestra_at_defaults(state, config, status)
            and not any(show_has_something_to_reset(config.satellite(funestra))
                        for funestra in FunTimeFunestra.SATELLITES))


def _ignoring_the_command(handler: Handler) -> Route:
    def route(_command: Command, state: BridgeState, config: BridgeConfig,
              target_path: str) -> Result:
        return handler(state, config, target_path)
    return route


def _for_its_satellite(handler: SatelliteHandler) -> Route:
    def route(command: Command, state: BridgeState, config: BridgeConfig,
              target_path: str) -> Result:
        return handler(command.satellite, state, config, target_path)
    return route


_SATELLITES = (Scope.PORTRAIT, Scope.LANDSCAPE)


def _bound_once(domains: Iterable[object]) -> dict[tuple[Scope, str], Route]:
    routes: dict[tuple[Scope, str], Route] = {}

    def bind(key: tuple[Scope, str], route: Route) -> None:
        if key in routes:
            raise ValueError(f"command bound twice: {key[0] + '_' if key[0] else ''}{key[1]}")
        routes[key] = route

    for domain in domains:
        for text, handler in getattr(domain, "HANDLERS", {}).items():
            bind(parse_command(text).key, _ignoring_the_command(handler))
        for stem, route in getattr(domain, "VALUED", {}).items():
            bind(parse_command(stem).key, route)
        for action, handler in getattr(domain, "SATELLITE_ACTIONS", {}).items():
            for satellite in _SATELLITES:
                bind((satellite, action), _for_its_satellite(handler))
        for action, route in getattr(domain, "SATELLITE_VALUED", {}).items():
            for satellite in _SATELLITES:
                bind((satellite, action), route)
    return routes


_DOMAINS = (satellite_commands, main_funestra_commands, main_list_commands, f_mode,
            audio_commands, omnipause, mode_switches, osr2_commands, genau_commands, crown,
            hud_moves, max_intensity)

ROUTES: dict[tuple[Scope, str], Route] = _bound_once(_DOMAINS)


def handles(text: str) -> bool:
    return all(half.key in ROUTES or about_a_satellite(half)
               for each in parse_command(text).each() for half in each.halves())

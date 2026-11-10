from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .command_value import SAY, Command, Scope, parse_command
from .hosted_contract import said_to_core, said_to_the_hosted_app, written_for_the_hosted_app
from .main_funestra_commands import refused_on_a_satellite
from .satellite_commands import THE_FUNESTRAS_OWN

if TYPE_CHECKING:
    from .bridge_records import BridgeConfig
    from .shared_state import BridgeState


def _said_bare(command: Command) -> bool:
    return command.said_to is Scope.ACTIVE and command.action == SAY


def with_the_target_it_names(command: Command, state: BridgeState) -> BridgeState:
    if command.funestra is None:
        return state
    return replace(state, active_funestra=command.funestra, core_named_last=False)


def aimed_at_the_tracked_target(command: Command, state: BridgeState) -> Command:
    if _said_bare(command) and state.core_named_last:
        return command
    return command.aimed_at_the_active(state.active_funestra)


def the_satellite_a_bare_line_reaches(command: Command, state: BridgeState) -> Command | None:
    if not _said_bare(command):
        return None
    aimed = aimed_at_the_tracked_target(command, state)
    return None if aimed is command else aimed


def line_said_bare_to_core(command: Command, state: BridgeState) -> str | None:
    if _said_bare(command) and state.core_named_last:
        return said_to_core(command.argument)
    return None


def about_a_satellite(command: Command) -> bool:
    return command.satellite is not None and command.action not in THE_FUNESTRAS_OWN


_CLEAR_FILTER = "clear filter"


def _words_for_the_hosted_app_to_match(command: Command) -> tuple[str, str] | None:
    if command.satellite is None:
        return None
    if command.action == SAY:
        return command.said_to, command.argument
    return (command.said_to, _CLEAR_FILTER) if command.action == "no_filter" else None


def a_lock_said_in_the_hosted_apps_words(command: Command, config: BridgeConfig) -> Command:
    if (command.action != SAY or command.argument not in config.origenerator_lock_phrases
            or not routes_to_origenerator(command)):
        return command
    return Command.to(command.satellite, "lock_on")


def routes_to_origenerator(command: str | Command) -> bool:
    if isinstance(command, str):
        command = parse_command(command)
    if _words_for_the_hosted_app_to_match(command) is not None:
        return True
    return all(about_a_satellite(said) for said in command.halves())


def told_to_the_hosted_app(command: Command, state: BridgeState, config: BridgeConfig,
                           spoken_at: float | None) -> list:
    if refused := _refused_on_its_funestra(command, state, config):
        return refused
    spoken = _words_for_the_hosted_app_to_match(command)
    return written_for_the_hosted_app(
        [said_to_the_hosted_app(*spoken)] if spoken is not None
        else [half.text for half in command.halves()], config, spoken_at)


def _refused_on_its_funestra(command: Command, state: BridgeState, config: BridgeConfig) -> list:
    if command.satellite is None:
        return []
    return refused_on_a_satellite(command.satellite, command.action,
                                                         state, config)

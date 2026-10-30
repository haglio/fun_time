"""The command registry: one dispatch command in, an updated state and a list of
window ops out.  Each domain binds its own commands (``HANDLERS``, ``PARSED_FORMS``)
and this module holds the map and the guards that run ahead of it."""
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
from .funestra_status import MainFunestraStatus
from .genau_commands import about_genaus_flick
from .hosted_contract import said_to_the_hosted_app, with_when_said
from .hud_moves import minimize_ops, placed_hud
from .main_list_commands import main_funestra_at_defaults
from .mode_plan import kino_shows
from .origenerator_switches import hosts, hosts_a_satellite
from .room_funestras import RoomFunestra
from .satellite_commands import (
    LOCK_STATE_COMMANDS,
    LOCK_TOGGLES,
    is_hud_nav_command,
    lock_said,
    satellite_at_defaults,
)
from .satellite_control import read_satellite_status
from .shared_state import BridgeState
from .voice_commands import said_to_a_side

logger = logging.getLogger(__name__)


def _addressed(funestra: RoomFunestra, command: str, state: BridgeState) -> BridgeState:
    state = replace(state, active_funestra=funestra)
    if funestra is RoomFunestra.MAIN or is_hud_nav_command(command):
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
    """Dispatch a dashboard/hotkey command, returning updated state and window operations.

    ``target_path`` names the video a spoken command was aimed at — the one on
    screen when the utterance began, which an auto-advancing satellite may have
    moved on from by the time the phrase was recognized.  Every satellite action
    that is *about a particular video* honors it: lock, weird, wrong-action,
    cycle, the group loops and lock-action.  Navigation is relative rather than
    video-scoped, and the rest of the vocabulary names no video at all, so both
    ignore it.  Empty means "whatever is playing now", which is how every
    keyboard and dashboard command arrives.
    """
    funestra = command_funestra(command)
    if funestra is not None:
        state = _addressed(funestra, command, state)
    command = _a_lock_said_in_the_hosted_apps_words(command, state, config)
    answered = _answered_ahead_of_the_handlers(command, state, config)
    if answered is not None:
        return answered

    # Said to two sides in different modes, each side's half goes where its
    # own mode sends it.
    if halves := _said_to_sides_in_two_modes(command, state, config):
        ops: list[WindowOp] = []
        for half in halves:
            state, more = dispatch_command(half, state, config, target_path=target_path,
                                           spoken_at=spoken_at)
            ops.extend(more)
        return state, ops

    # In its Origenerator mode what is said to a side is the hosted app's to
    # answer -- ahead of the handler lookup, whose Funestra handlers it shadows.
    routed = _origenerator_transport(command, state, config, spoken_at)
    if routed is not None:
        return state, routed

    handler = _HANDLERS.get(command)
    if handler is not None:
        return handler(state, config, target_path)

    # The argument-carrying forms — a parsed suffix or a "|" payload — matched
    # after the exact ids.  No parsed form can collide with an exact id: every
    # parser demands a suffix (an integer, a direction, a payload) that no
    # table key carries.
    for parse in _PARSED_FORMS:
        handled = parse(command, state, config, target_path)
        if handled is not None:
            return handled

    # Said, because a missed rung is otherwise indistinguishable from a handled
    # no-op: a key bound to a misspelled id reads as simply dead.  The state
    # still comes back unchanged; the log says why nothing happened.
    logger.warning("no handler for command %r", command)
    return state, []


# In its Origenerator mode a Satellite Funestra is the hosted app's: it hands the
# Funestra what to play and answers every press on the buttons it drew.  So what
# is said to that side goes there as it was said, and the session keeps only what
# is about the Funestra itself -- parking its window, its crown, its rate, taking
# the OSR2, its mode, and its lock, which cannot wait for the app to read a press.
_THE_FUNESTRAS_OWN = ("_minimize", "_crown", "_speed_", "_take_osr2", "_kino_activate",
                      "_origenerator_activate")


_THE_FUNESTRAS_LOCK = frozenset({*LOCK_TOGGLES, *LOCK_STATE_COMMANDS})


def _side_said_to(command: str) -> RoomFunestra | None:
    head = command.removeprefix("filter_")
    return next((funestra for funestra in RoomFunestra.SATELLITES
                 if head.startswith(f"{funestra.label}_")), None)


def _about_a_satellite(command: str) -> bool:
    """Whether *command* is said to one satellite side, about what it plays."""
    if _side_said_to(command) is None or command in _THE_FUNESTRAS_LOCK:
        return False
    verb = command.removeprefix("filter_").partition("|")[0]
    return not any(own in verb for own in _THE_FUNESTRAS_OWN)


def _said_to_each_side(command: str) -> tuple[str, ...]:
    target = decode_filter_command(command)
    if target is None or target[0] != "both":
        return (command,)
    return tuple(set_command(funestra.label, target[1]) for funestra in RoomFunestra.SATELLITES)


# One the session already says to a Funestra, which means the same thing to the
# hosted app's show on that side.
_SAID_TO_A_FUNESTRA_AND_A_SHOW: dict[str, tuple[str, str]] = {
    "portrait_no_filter": ("portrait", "clear filter"),
    "landscape_no_filter": ("landscape", "clear filter"),
}


def _words_for_the_hosted_app(command: str) -> tuple[str, str] | None:
    return said_to_a_side(command) or _SAID_TO_A_FUNESTRA_AND_A_SHOW.get(command)


def _a_lock_said_in_the_hosted_apps_words(command: str, state: BridgeState,
                                          config: BridgeConfig) -> str:
    said = said_to_a_side(command)
    if (said is None or said[1] not in config.origenerator_lock_phrases
            or not routes_to_origenerator(command, state, config)):
        return command
    return lock_said(RoomFunestra[said[0].upper()], True)


def routes_to_origenerator(command: str, state: BridgeState, config: BridgeConfig) -> bool:
    """Whether *command* is bound for the hosted app rather than the session.

    Everything said to a side in its Origenerator mode about what it plays goes
    there (:func:`_about_a_satellite`), and so does the app's own spoken vocabulary
    about that side, as the words themselves: the session hears them for the
    whole room and the hosted app matches them.  Public because the dispatch loop
    asks the same question -- its watch tracking must not book a show's step or
    cull against a library video.
    """
    spoken = _words_for_the_hosted_app(command)
    if spoken is not None:
        return hosts(RoomFunestra[spoken[0].upper()], state, config)
    return all(_about_a_satellite(said) and hosts(_side_said_to(said), state, config)
               for said in _said_to_each_side(command))


def _said_to_sides_in_two_modes(command: str, state: BridgeState,
                                config: BridgeConfig) -> tuple[str, ...]:
    """*command* as said to each side, where one of the two sides it reaches is
    the hosted app's and the other the session's; empty otherwise."""
    said = _said_to_each_side(command)
    if len(said) < 2 or not all(_about_a_satellite(each) for each in said):
        return ()
    hosted = {hosts(_side_said_to(each), state, config) for each in said}
    return said if len(hosted) > 1 else ()


def _origenerator_transport(
    command: str, state: BridgeState, config: BridgeConfig, spoken_at: float | None
) -> list[WindowOp] | None:
    """Hand a side's press to the hosted app, or ``None`` to fall through to
    the session's own handling.

    None of Kino's bookkeeping (lock flags, favorites, RFB tabs)
    applies to a show, and a press goes over as it was posted: the app declared
    the button, so it is what knows the verb.
    """
    if not routes_to_origenerator(command, state, config):
        return None
    spoken = _words_for_the_hosted_app(command)
    lines = ([said_to_the_hosted_app(*spoken)] if spoken is not None
             else _said_to_each_side(command))
    for line in lines:
        append_command(config.origenerator_cmd_file,
                       with_when_said(line, spoken_at, config.origenerator_said_at_suffix))
    return []


def room_at_defaults(state: BridgeState, config: BridgeConfig, status: MainFunestraStatus) -> bool:
    return (main_funestra_at_defaults(state, config, status)
            and not hosts_a_satellite(state, config)
            and all(satellite_at_defaults(
                        state.satellite(funestra),
                        read_satellite_status(config.satellite(funestra).status_file))
                    for funestra in RoomFunestra.SATELLITES))


def _parsed_unresolved_active(command: str, state: BridgeState, _config: BridgeConfig,
                              _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if not command.startswith("active_"):
        return None
    return ignored(state, ON_THE_MAIN_FUNESTRA)


def _parsed_words_for_a_show_that_is_not_up(
        command: str, state: BridgeState, _config: BridgeConfig,
        _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if said_to_a_side(command) is None:
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

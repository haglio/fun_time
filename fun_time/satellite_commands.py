from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial
from pathlib import Path

from funestra.file_channel import append_command
from funestra.funestra_verbs import JUMP_TO_FUNSCRIPT, LOCK_OFF, NEXT, PREV, TRASH
from funestra.hud_title import LATEST_LABEL, SHUFFLE_LABEL

from .bridge_records import BridgeConfig, Handler, WindowOp
from .event_log import FAVORITE, MESSAGE
from .filter_vocab import decode_filter_command
from .fun_time_funestras import FunTimeFunestra
from .lock import build_discard_plan, unfavorite
from .media_actions import move_to_weird
from .media_metadata import forget_indexed_video, is_favorite
from .origenerator_switches import hosts
from .osr2_commands import (
    FUNSCRIPT_MOVES,
    NORMAL_SPEED,
    PLAYBACK_RATE_ACTS,
    percent_rate,
    skip_ahead_refused,
)
from .runtime_flow import SatelliteFilterFlowResult, apply_satellite_filter
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
    same_video,
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
    funestra: FunTimeFunestra, locked: bool, state: BridgeState, config: BridgeConfig,
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
    # "Weird" judges the video the speaker saw.  When the satellite advanced
    # while the phrase was being recognized, jump back to the condemned video
    # before trashing it, so the wrong (innocent) video is never the one dropped.
    condemned = target_path or current_path
    if not condemned:
        logger.info("Nothing discarded on Funestra %d: it has not said which clip it is showing", funestra)
        return state, []
    already_moved_on = bool(target_path) and not same_video(target_path, current_path)
    # Whether this is a demotion or a condemnation is read from the same record
    # that lights the HUD's ★ for this video, so the key does what the badge
    # on screen implies: a starred video loses the star, an unstarred one goes.
    plan = build_discard_plan(funestra=funestra, current_path=condemned,
                              is_favorite=is_favorite(condemned, config.regen_metadata_root))
    if locked:
        # A locked satellite is repeat-one; drop the lock so TRASH advances into
        # the playlist instead of looping the video that replaced the discarded one.
        send_satellite(config, funestra, LOCK_OFF)
    unfavorite(condemned, config)
    if plan.condemns:
        if already_moved_on:
            play_video(config, funestra, condemned)
        send_satellite(config, funestra, TRASH)
    elif not already_moved_on:
        # A demotion leaves the video in the playlist, so this is a plain
        # advance (NEXT) and PREV comes straight back to it.  Nothing has to
        # be done to the video itself, so a satellite that already moved on is
        # left alone rather than dragged back to a video it would leave again.
        send_satellite(config, funestra, NEXT)
    state = state.with_satellite(funestra, locked=False)
    lock_ops: list[WindowOp] = []
    if plan.condemns:
        move_to_weird(config.weird_dir, Path(condemned))
        state, lock_ops = lock_a_loop_left_with_one_video(funestra, state, config, condemned)
        forget_indexed_video(condemned)
    logger.info(plan.log_message)
    flashed = WindowOp(op="message", key=plan.message_text, source=satellite_source(funestra),
                      level=MESSAGE if plan.condemns else FAVORITE)
    return state, [flashed, *lock_ops]


# The satellite and the variation axis each cycle command reaches.
_CYCLE_COMMANDS = {
    "portrait_cycle_action": (FunTimeFunestra.PORTRAIT, "action"),
    "portrait_cycle_seed": (FunTimeFunestra.PORTRAIT, "seed"),
    "landscape_cycle_action": (FunTimeFunestra.LANDSCAPE, "action"),
    "landscape_cycle_seed": (FunTimeFunestra.LANDSCAPE, "seed"),
}


_VERSION_COMMANDS: dict[str, tuple[FunTimeFunestra, int]] = {
    "portrait_cycle_version": (FunTimeFunestra.PORTRAIT, 1),
    "portrait_cycle_version_back": (FunTimeFunestra.PORTRAIT, -1),
    "landscape_cycle_version": (FunTimeFunestra.LANDSCAPE, 1),
    "landscape_cycle_version_back": (FunTimeFunestra.LANDSCAPE, -1),
}


# "more seeds" — see :func:`satellite_groups.more_seeds`.
_MORE_SEEDS_SATELLITES = {"portrait_more_seeds": FunTimeFunestra.PORTRAIT,
                     "landscape_more_seeds": FunTimeFunestra.LANDSCAPE}


# Slot and axis per group-loop command — see :func:`satellite_groups.group_loop`.
_LOOP_COMMANDS: dict[str, tuple[FunTimeFunestra, str]] = {
    "portrait_action_loop": (FunTimeFunestra.PORTRAIT, "action"),
    "portrait_seed_loop": (FunTimeFunestra.PORTRAIT, "seed"),
    "landscape_action_loop": (FunTimeFunestra.LANDSCAPE, "action"),
    "landscape_seed_loop": (FunTimeFunestra.LANDSCAPE, "seed"),
}


_LOCK_ACTION_SATELLITES: dict[str, FunTimeFunestra] = {
    "portrait_lock_action": FunTimeFunestra.PORTRAIT,
    "landscape_lock_action": FunTimeFunestra.LANDSCAPE,
}


def lock_said(funestra: FunTimeFunestra, locked: bool) -> str:
    return f"{funestra.label}_lock_{'on' if locked else 'off'}"


def _lock_pressed(funestra: FunTimeFunestra) -> str:
    return f"{funestra.label}_lock"


LOCK_STATE_COMMANDS: dict[str, tuple[FunTimeFunestra, bool]] = {
    lock_said(funestra, locked): (funestra, locked)
    for funestra in FunTimeFunestra.SATELLITES
    for locked in (True, False)
}


LOCK_TOGGLES: dict[str, FunTimeFunestra] = {_lock_pressed(funestra): funestra
                                          for funestra in FunTimeFunestra.SATELLITES}


# "reset" clears a satellite's filter and reshuffles it back to the default
# browse; "both reset" expands to these two before dispatch.
_RESET_SATELLITES: dict[str, tuple[FunTimeFunestra, ...]] = {
    "portrait_reset": (FunTimeFunestra.PORTRAIT,),
    "landscape_reset": (FunTimeFunestra.LANDSCAPE,),
}


# "no loop" ends a group loop but, unlike reset, keeps the satellite's filter.
_NO_LOOP_SATELLITES: dict[str, FunTimeFunestra] = {
    "portrait_no_loop": FunTimeFunestra.PORTRAIT,
    "landscape_no_loop": FunTimeFunestra.LANDSCAPE,
}


_LOOP_CYCLE_SATELLITES: dict[str, FunTimeFunestra] = {
    "portrait_loop": FunTimeFunestra.PORTRAIT,
    "landscape_loop": FunTimeFunestra.LANDSCAPE,
}


# "no filter" drops just the filter — the narrow counterpart of reset, which puts
# the whole Satellite back to its defaults.
_NO_FILTER_SATELLITES: dict[str, tuple[FunTimeFunestra, ...]] = {
    "portrait_no_filter": (FunTimeFunestra.PORTRAIT,),
    "landscape_no_filter": (FunTimeFunestra.LANDSCAPE,),
}


# "Wrong action" — the video is fine, its label is not.  Per side, like every
# other judgement of the video on screen.
_WRONG_ACTION_SATELLITES: dict[str, FunTimeFunestra] = {"portrait_wrong_action": FunTimeFunestra.PORTRAIT,
                                          "landscape_wrong_action": FunTimeFunestra.LANDSCAPE}


def _dispatch_lock_action(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Filter the satellite to the current video's action — "portrait [act]",
    with the act read off the video instead of spoken."""
    current = target_path or satellite_current(config, funestra)
    if not current:
        return state, []
    action = video_action_label(current, config)
    if not action:
        return state, [WindowOp(op="message", key="No action metadata", source=satellite_source(funestra), level=logging.WARNING)]
    return _dispatch_set_filter((funestra,), action.lower(), state, config)


def _dispatch_lock_video(
    funestra: FunTimeFunestra, path: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Double-click a HUD thumbnail: switch to *path* and lock it."""
    return lock_satellite(funestra, state, config, path)


_NAV_DIRECTIONS = ("up", "down")


def _parse_nav(command: str) -> tuple[int, str] | None:
    """``(funestra, direction)`` for a ``<satellite>_nav_<dir>`` command, else None."""
    for prefix, funestra in (("portrait_nav_", FunTimeFunestra.PORTRAIT),
                          ("landscape_nav_", FunTimeFunestra.LANDSCAPE)):
        if command.startswith(prefix):
            direction = command[len(prefix):]
            if direction in _NAV_DIRECTIONS:
                return funestra, direction
    return None


def is_hud_nav_command(command: str) -> bool:
    """Whether *command* drives HUD keyboard navigation — a direction step.  Those
    manage the Satellite's nav anchor themselves, so the generic "any other side
    command re-homes the nav map" rule leaves them alone."""
    return _parse_nav(command) is not None


def _dispatch_reorder(
    funestra: FunTimeFunestra, recent: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Reload one satellite in a fresh order — Latest (newest-first) or Shuffle.

    Either rescans that Satellite's sources, so videos that have arrived since are picked
    up, and keeps its filter.  The order is remembered per Satellite — the two satellites
    can be in different ones — so a later filter or F-mode rebuild reloads it the
    same way.  The rebuild replaces the queue, which drops the Satellite's lock and any
    group loop (with the widened row that rode on it).
    """
    state = state.with_satellite(funestra, latest=recent)
    # From the top of the new order: asking for the latest is asking to see what has
    # just arrived, and the reload alone would leave the video on screen playing with
    # the new order applying only after it.
    result = _rebuild_satellite(funestra, state.satellite(funestra).filter, state, config, start_at_top=True)
    state = state.with_satellite(funestra, locked=False)
    state = clear_satellite_grouping(state, funestra)
    funestra_name = FunTimeFunestra(funestra).label
    # The order's own word and nothing else.  The message flashes on the Funestra it
    # was said to, and this is what that Funestra's HUD calls the order it is now
    # in, so naming the Funestra and then spelling the order out a second time
    # ("Latest: portrait newest-first") only read as a log line that had escaped
    # onto the screen.  The count and the Satellite stay in the log, where they are of
    # use.
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: %s (%d clips)", label, funestra_name, result.count)
    return state, [WindowOp(op="message", key=label, source=satellite_source(funestra))]


def _browse_at_defaults(satellite: SatelliteState) -> bool:
    fresh = SatelliteState()
    return replace(satellite, nav_anchor="", speed=fresh.speed, volume=fresh.volume,
                   muted=fresh.muted) == fresh


def satellite_at_defaults(satellite: SatelliteState, status: SatelliteStatus) -> bool:
    return _browse_at_defaults(satellite) and status.speed == 1.0


def _dispatch_reset(
    funestras: tuple[FunTimeFunestra, ...], state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Put a satellite (or both) back to every default, not just its filter.

    The lock is released, the filter cleared, F-mode dropped, the order returned to
    shuffled, any group loop and widened row and frozen nav map dropped, and the Satellite
    starts from the top of a freshly-built browse — one video per subject.  "no
    filter" is the narrow gesture; this one is "put it back how it started".

    F-mode is one of those defaults, and is cleared here rather than left standing:
    a Satellite reset while it was narrowed to the favorites came back still narrowed to
    them, which is a browse of a few dozen videos wearing the label of the whole
    library.  The flag goes into the state before the rebuild, because the rebuild
    reads it back to decide how wide to build.

    A Satellite already sitting at those defaults is left alone entirely.  The rebuild
    reshuffles and starts at the top, so a second press landed on a different video
    than the first, and a third on another again: a button that says "put it back"
    was the fastest way to keep changing what was playing.  Nothing is narrowed by
    then, so there is nothing the press could put back.
    """
    ops: list[WindowOp] = []
    for funestra in funestras:
        # Every default is the empty value of its field, so "already reset" is
        # the Satellite's whole SatelliteState sitting at the default — a narrowing the
        # reset clears cannot be one this test forgets.  (The nav anchor is
        # already gone: every Satellite command that is not itself a nav step clears
        # it on the way in, so no reset has ever seen one set.)
        if satellite_at_defaults(state.satellite(funestra),
                                 read_satellite_status(config.satellite(funestra).status_file)):
            logger.info("Reset %s: already at its defaults", satellite_source(funestra))
            continue
        if not _browse_at_defaults(state.satellite(funestra)):
            state = cancel_lock(funestra, state, config)
            state = state.with_satellite(
                funestra, latest=False, filter="", favorites_filter=False, nav_anchor="")
            state = clear_satellite_grouping(state, funestra)
            result = _rebuild_satellite(funestra, "", state, config, start_at_top=True)
            logger.info("Reset %s: %s", satellite_source(funestra), result.log_message)
        send_satellite(config, funestra, NORMAL_SPEED)
        ops.append(WindowOp(op="message", key="Reset", source=satellite_source(funestra)))
    return state, ops


def _rebuild_satellite(
    funestra: FunTimeFunestra, query: str, state: BridgeState, config: BridgeConfig,
    *, start_at_top: bool = False,
) -> SatelliteFilterFlowResult:
    """Rebuild one satellite's browse under *query* and its own current ordering.

    The single place a satellite's playlist is rebuilt from its sources, so a
    filter and a reorder cannot drift apart in how they read the Satellite's state.
    ``start_at_top`` is for the callers that mean "start over" — a reorder or a
    reset — since the reload otherwise keeps the video on screen and carries on from
    where it sat, leaving the new order to apply only after it.
    """
    satellite = state.satellite(funestra)
    return apply_satellite_filter(
        funestra=funestra,
        query=query,
        favorites_filter=satellite.favorites_filter,
        recent=satellite.latest,
        sources=config.satellite(funestra).sources,
        state_dir=config.state_dir,
        cmd_file=config.satellite(funestra).cmd_file,
        start_at_top=start_at_top,
        regen_metadata_root=config.regen_metadata_root,
    )


def _dispatch_set_filter(
    funestras: tuple[FunTimeFunestra, ...], query: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Apply a metadata filter to *Funestras* and rebuild them.

    ``query`` is the substring to match ("" clears).  Each targeted satellite
    records its own filter in the state so later F-mode / reorder rebuilds keep
    it, then reloads under its own ordering.
    """
    ops: list[WindowOp] = []
    for funestra in funestras:
        result = _rebuild_satellite(funestra, query, state, config)
        # Only remember a filter that actually selected videos: a zero-match
        # filter left the current playlist alone, so recording it would let the
        # next F-mode/reorder rebuild blank the satellite.  A filter that *did* rebuild
        # also replaced any loop's sub-playlist, so the loop (and its widened row)
        # is gone; a zero-match one touched nothing, so a running loop survives it.
        if result.applied:
            state = state.with_satellite(funestra, filter=query)
            state = clear_satellite_grouping(state, funestra)
        logger.info(result.log_message)
        # A filter that selected nothing left the playlist untouched — a dead end,
        # so a warning like the other no-effect messages.
        level = MESSAGE if result.applied else logging.WARNING
        ops.append(WindowOp(op="message", key=result.log_message, source=satellite_source(funestra), level=level))
    return state, ops


# Plain playlist navigation, per Satellite.
_TRANSPORT_COMMANDS: dict[str, tuple[FunTimeFunestra, str]] = {
    "portrait_prev": (FunTimeFunestra.PORTRAIT, PREV),
    "portrait_next": (FunTimeFunestra.PORTRAIT, NEXT),
    "landscape_prev": (FunTimeFunestra.LANDSCAPE, PREV),
    "landscape_next": (FunTimeFunestra.LANDSCAPE, NEXT),
}


def _transport(funestra: FunTimeFunestra, verb: str, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state = cancel_lock(funestra, state, config)
    if verb == NEXT and is_single_video_loop(funestra, state, config):
        state, _loop_off = no_loop(funestra, state, config)
    send_satellite(config, funestra, verb)
    return state, []


# A satellite's own playback rate, and its funscript moves: each verb goes to
# that satellite as it is.
_SENT_TO_A_SATELLITE: dict[str, tuple[FunTimeFunestra, str]] = {
    f"{funestra.label}_{act}": (funestra, verb)
    for funestra in FunTimeFunestra.SATELLITES
    for act, verb in (*PLAYBACK_RATE_ACTS.items(), *FUNSCRIPT_MOVES.items())
}


def _send_to_satellite(funestra: FunTimeFunestra, verb: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if verb == JUMP_TO_FUNSCRIPT and (refused := skip_ahead_refused(funestra, state)):
        return state, refused
    send_satellite(config, funestra, verb)
    return state, []


def _no_loop(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return no_loop(funestra, state, config)


def _reset(funestras: tuple[FunTimeFunestra, ...], state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_reset(funestras, state, config)


def _set_filter(funestras: tuple[FunTimeFunestra, ...], query: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_set_filter(funestras, query, state, config)


def _parsed_nav(command: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """Keyboard navigation of the HUD map: "<satellite>_nav_<dir>" moves the
    selection and switches the satellite to it."""
    nav = _parse_nav(command)
    if nav is None:
        return None
    return navigate_hud(*nav, state, config)


def _parsed_play_video(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """A HUD thumbnail click: "<satellite>_play_video|<path>" switches straight to
    that video.  The path rides after the "|" ("|" is illegal in a Windows path,
    so it is an unambiguous delimiter)."""
    if "_play_video|" not in command:
        return None
    head, _, path = command.partition("|")
    return switch_to_video(
        FunTimeFunestra.PORTRAIT if head.startswith("portrait_") else FunTimeFunestra.LANDSCAPE,
        path, state, config)


def _parsed_lock_video(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """Double-click of a HUD thumbnail: "<satellite>_lock_video|<path>" — switch and lock."""
    if "_lock_video|" not in command:
        return None
    head, _, path = command.partition("|")
    return _dispatch_lock_video(
        FunTimeFunestra.PORTRAIT if head.startswith("portrait_") else FunTimeFunestra.LANDSCAPE,
        path, state, config)


def _parsed_filter(command: str, state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    filter_target = decode_filter_command(command)
    if filter_target is None:
        return None
    scope, query = filter_target
    return _dispatch_set_filter(FunTimeFunestra.for_scope(scope), query, state, config)


def _parsed_satellite_speed(command: str, state: BridgeState, config: BridgeConfig,
                            _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    for funestra in FunTimeFunestra.SATELLITES:
        verb = percent_rate(command, f"{funestra.label}_speed_")
        if verb is not None:
            send_satellite(config, funestra, verb)
            return state, []
    return None


# The two browse orderings, per satellite: Latest reloads newest-first, Shuffle
# reshuffles; the Main Funestra's pair is main_list_commands'.
_REORDER_COMMANDS: dict[str, tuple[FunTimeFunestra, bool]] = {
    "portrait_latest": (FunTimeFunestra.PORTRAIT, True),
    "landscape_latest": (FunTimeFunestra.LANDSCAPE, True),
    "portrait_shuffle": (FunTimeFunestra.PORTRAIT, False),
    "landscape_shuffle": (FunTimeFunestra.LANDSCAPE, False),
}


def _reorder(funestra: FunTimeFunestra, recent: bool, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_reorder(funestra, recent, state, config)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_transport, funestra, verb)
       for cmd, (funestra, verb) in _TRANSPORT_COMMANDS.items()},
    **{cmd: partial(_send_to_satellite, funestra, verb)
       for cmd, (funestra, verb) in _SENT_TO_A_SATELLITE.items()},
    **{cmd: partial(_toggle_lock, funestra) for cmd, funestra in LOCK_TOGGLES.items()},
    **{cmd: partial(_lock_as_said, funestra, locked)
       for cmd, (funestra, locked) in LOCK_STATE_COMMANDS.items()},
    "portrait_trash": partial(_discard, FunTimeFunestra.PORTRAIT),
    "landscape_trash": partial(_discard, FunTimeFunestra.LANDSCAPE),
    **{cmd: partial(cycle_variant, funestra, kind)
       for cmd, (funestra, kind) in _CYCLE_COMMANDS.items()},
    **{cmd: partial(cycle_version, funestra, delta)
       for cmd, (funestra, delta) in _VERSION_COMMANDS.items()},
    **{cmd: partial(more_seeds, funestra) for cmd, funestra in _MORE_SEEDS_SATELLITES.items()},
    **{cmd: partial(wrong_action, funestra) for cmd, funestra in _WRONG_ACTION_SATELLITES.items()},
    **{cmd: partial(group_loop, funestra, axis)
       for cmd, (funestra, axis) in _LOOP_COMMANDS.items()},
    **{cmd: partial(loop_cycle, funestra) for cmd, funestra in _LOOP_CYCLE_SATELLITES.items()},
    **{cmd: partial(_no_loop, funestra) for cmd, funestra in _NO_LOOP_SATELLITES.items()},
    **{cmd: partial(_dispatch_lock_action, funestra)
       for cmd, funestra in _LOCK_ACTION_SATELLITES.items()},
    **{cmd: partial(_reorder, funestra, recent)
       for cmd, (funestra, recent) in _REORDER_COMMANDS.items()},
    **{cmd: partial(_reset, funestras) for cmd, funestras in _RESET_SATELLITES.items()},
    **{cmd: partial(_set_filter, funestras, "") for cmd, funestras in _NO_FILTER_SATELLITES.items()},
}

PARSED_FORMS = (_parsed_nav, _parsed_play_video, _parsed_lock_video, _parsed_filter,
                _parsed_satellite_speed)

from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial
from pathlib import Path

from funestra.file_channel import append_command
from funestra.funestra_verbs import JUMP_TO_FUNSCRIPT, LOCK_OFF, NEXT, PREV, TRASH
from funestra.hud_title import LATEST_LABEL, SHUFFLE_LABEL

from .bridge_records import IN_ORIGENERATOR_MODE, BridgeConfig, Handler, WindowOp, ignored
from .event_log import FAVORITE, NOTICE
from .filter_vocab import decode_filter_command
from .funestra_handover import show_enhanced_filter
from .hosted_contract import ENHANCED_ONLY, said_to_the_hosted_app
from .lock import build_discard_plan, unfavorite
from .media_actions import move_to_weird
from .media_metadata import forget_indexed_clip, is_favorite
from .mode_plan import EnhancementLevel
from .origenerator_switches import hosts
from .osr2_commands import (
    FUNSCRIPT_MOVES,
    NORMAL_SPEED,
    PLAYBACK_RATE_ACTS,
    percent_rate,
    skip_ahead_refused,
)
from .room_funestras import RoomFunestra
from .runtime_flow import SatelliteFilterFlowResult, apply_satellite_filter
from .satellite_control import SatelliteStatus, read_satellite_status
from .satellite_groups import (
    cancel_lock,
    clear_side_grouping,
    cycle_variant,
    cycle_version,
    group_loop,
    is_single_video_loop,
    lock_a_loop_left_with_one_clip,
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
    funestra: RoomFunestra, locked: bool, state: BridgeState, config: BridgeConfig,
    target_path: str = "",
) -> tuple[BridgeState, list[WindowOp]]:
    if hosts(funestra, state, config):
        return _lock_a_hosted_side(funestra, lock_said(funestra, locked), state, config,
                                   target_path)
    if state.satellite(funestra).locked == locked:
        return state, []
    return _set_lock(funestra, locked, state, config, target_path)


def _toggle_lock(
    funestra: RoomFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    if hosts(funestra, state, config):
        return _lock_a_hosted_side(funestra, _lock_pressed(funestra), state, config, target_path)
    return _set_lock(funestra, not state.satellite(funestra).locked, state, config, target_path)


def _lock_a_hosted_side(
    funestra: RoomFunestra, said: str, state: BridgeState, config: BridgeConfig,
    target_path: str,
) -> tuple[BridgeState, list[WindowOp]]:
    if said != lock_said(funestra, False):
        lock_on_what_was_meant(funestra, config, target_path)
    append_command(config.origenerator_cmd_file, said)
    return state, []


def _set_lock(
    funestra: RoomFunestra, locked: bool, state: BridgeState, config: BridgeConfig,
    target_path: str,
) -> tuple[BridgeState, list[WindowOp]]:
    if locked:
        return lock_satellite(funestra, state, config, target_path)
    state = cancel_lock(funestra, state, config)
    send_satellite(config, funestra, NEXT)
    logger.info("Unlocked %s satellite", RoomFunestra(funestra).label)
    return state, []


def _discard(
    funestra: RoomFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    locked = state.satellite(funestra).locked
    current_path = satellite_current(config, funestra)
    # "Weird" judges the video the speaker saw.  When the satellite advanced
    # while the phrase was being recognized, jump back to the condemned clip
    # before trashing it, so the wrong (innocent) clip is never the one dropped.
    condemned = target_path or current_path
    if not condemned:
        logger.info("Nothing discarded on Funestra %d: it has not said which clip it is showing", funestra)
        return state, []
    already_moved_on = bool(target_path) and not same_video(target_path, current_path)
    # Whether this is a demotion or a condemnation is read from the same record
    # that lights the HUD's ★ for this clip, so the key does what the badge
    # on screen implies: a starred clip loses the star, an unstarred one goes.
    plan = build_discard_plan(funestra=funestra, current_path=condemned,
                              is_favorite=is_favorite(condemned, config.regen_metadata_root))
    if locked:
        # A locked satellite is repeat-one; drop the lock so TRASH advances into
        # the playlist instead of looping the clip that replaced the discarded one.
        send_satellite(config, funestra, LOCK_OFF)
    unfavorite(condemned, config)
    if plan.condemns:
        if already_moved_on:
            play_video(config, funestra, condemned)
        send_satellite(config, funestra, TRASH)
    elif not already_moved_on:
        # A demotion leaves the clip in the playlist, so this is a plain
        # advance (NEXT) and PREV comes straight back to it.  Nothing has to
        # be done to the clip itself, so a satellite that already moved on is
        # left alone rather than dragged back to a clip it would leave again.
        send_satellite(config, funestra, NEXT)
    state = state.with_satellite(funestra, locked=False)
    lock_ops: list[WindowOp] = []
    if plan.condemns:
        move_to_weird(config.weird_dir, Path(condemned))
        state, lock_ops = lock_a_loop_left_with_one_clip(funestra, state, config, condemned)
        forget_indexed_clip(condemned)
    logger.info(plan.log_message)
    notice = WindowOp(op="notice", key=plan.notice_message, source=satellite_source(funestra),
                      level=NOTICE if plan.condemns else FAVORITE)
    return state, [notice, *lock_ops]


# The satellite and the variation axis each cycle command reaches.
_CYCLE_COMMANDS = {
    "portrait_cycle_action": (RoomFunestra.PORTRAIT, "action"),
    "portrait_cycle_seed": (RoomFunestra.PORTRAIT, "seed"),
    "landscape_cycle_action": (RoomFunestra.LANDSCAPE, "action"),
    "landscape_cycle_seed": (RoomFunestra.LANDSCAPE, "seed"),
}


_VERSION_COMMANDS: dict[str, tuple[RoomFunestra, int]] = {
    "portrait_cycle_version": (RoomFunestra.PORTRAIT, 1),
    "portrait_cycle_version_back": (RoomFunestra.PORTRAIT, -1),
    "landscape_cycle_version": (RoomFunestra.LANDSCAPE, 1),
    "landscape_cycle_version_back": (RoomFunestra.LANDSCAPE, -1),
}


# "more seeds" — see :func:`satellite_groups.more_seeds`.
_MORE_SEEDS_SIDES = {"portrait_more_seeds": RoomFunestra.PORTRAIT,
                     "landscape_more_seeds": RoomFunestra.LANDSCAPE}


# Slot and axis per group-loop command — see :func:`satellite_groups.group_loop`.
_LOOP_COMMANDS: dict[str, tuple[RoomFunestra, str]] = {
    "portrait_action_loop": (RoomFunestra.PORTRAIT, "action"),
    "portrait_seed_loop": (RoomFunestra.PORTRAIT, "seed"),
    "landscape_action_loop": (RoomFunestra.LANDSCAPE, "action"),
    "landscape_seed_loop": (RoomFunestra.LANDSCAPE, "seed"),
}


_LOCK_ACTION_SIDES: dict[str, RoomFunestra] = {
    "portrait_lock_action": RoomFunestra.PORTRAIT,
    "landscape_lock_action": RoomFunestra.LANDSCAPE,
}


def lock_said(funestra: RoomFunestra, locked: bool) -> str:
    return f"{funestra.label}_lock_{'on' if locked else 'off'}"


def _lock_pressed(funestra: RoomFunestra) -> str:
    return f"{funestra.label}_lock"


LOCK_STATE_COMMANDS: dict[str, tuple[RoomFunestra, bool]] = {
    lock_said(funestra, locked): (funestra, locked)
    for funestra in RoomFunestra.SATELLITES
    for locked in (True, False)
}


LOCK_TOGGLES: dict[str, RoomFunestra] = {_lock_pressed(funestra): funestra
                                          for funestra in RoomFunestra.SATELLITES}


# "reset" clears a satellite's filter and reshuffles it back to the default
# browse; "both reset" expands to these two before dispatch.
_RESET_SIDES: dict[str, tuple[RoomFunestra, ...]] = {
    "portrait_reset": (RoomFunestra.PORTRAIT,),
    "landscape_reset": (RoomFunestra.LANDSCAPE,),
}


# "no loop" ends a group loop but, unlike reset, keeps the satellite's filter.
_NO_LOOP_SIDES: dict[str, RoomFunestra] = {
    "portrait_no_loop": RoomFunestra.PORTRAIT,
    "landscape_no_loop": RoomFunestra.LANDSCAPE,
}


_LOOP_CYCLE_SIDES: dict[str, RoomFunestra] = {
    "portrait_loop": RoomFunestra.PORTRAIT,
    "landscape_loop": RoomFunestra.LANDSCAPE,
}


# "no filter" drops just the filter — the narrow counterpart of reset, which puts
# the whole side back to its defaults.
_NO_FILTER_SIDES: dict[str, tuple[RoomFunestra, ...]] = {
    "portrait_no_filter": (RoomFunestra.PORTRAIT,),
    "landscape_no_filter": (RoomFunestra.LANDSCAPE,),
}


# "Wrong action" — the clip is fine, its label is not.  Per side, like every
# other judgement of the clip on screen.
_WRONG_ACTION_SIDES: dict[str, RoomFunestra] = {"portrait_wrong_action": RoomFunestra.PORTRAIT,
                                          "landscape_wrong_action": RoomFunestra.LANDSCAPE}


def _dispatch_lock_action(
    funestra: RoomFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Filter the satellite to the current clip's action — "portrait [act]",
    with the act read off the clip instead of spoken."""
    current = target_path or satellite_current(config, funestra)
    if not current:
        return state, []
    action = video_action_label(current, config)
    if not action:
        return state, [WindowOp(op="notice", key="No action metadata", source=satellite_source(funestra), level=logging.WARNING)]
    return _dispatch_set_filter((funestra,), action.lower(), state, config)


def _dispatch_lock_video(
    funestra: RoomFunestra, path: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Double-click a HUD thumbnail: switch to *path* and lock it."""
    return lock_satellite(funestra, state, config, path)


_NAV_DIRECTIONS = ("up", "down")


def _parse_nav(command: str) -> tuple[int, str] | None:
    """``(slot, direction)`` for a ``<side>_nav_<dir>`` command, else None."""
    for prefix, funestra in (("portrait_nav_", RoomFunestra.PORTRAIT),
                          ("landscape_nav_", RoomFunestra.LANDSCAPE)):
        if command.startswith(prefix):
            direction = command[len(prefix):]
            if direction in _NAV_DIRECTIONS:
                return funestra, direction
    return None


def is_hud_nav_command(command: str) -> bool:
    """Whether *command* drives HUD keyboard navigation — a direction step.  Those
    manage the side's nav anchor themselves, so the generic "any other side
    command re-homes the map" rule leaves them alone."""
    return _parse_nav(command) is not None


def _dispatch_reorder(
    funestra: RoomFunestra, recent: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Reload one satellite in a fresh order — Latest (newest-first) or Shuffle.

    Either rescans that side's sources, so clips that have arrived since are picked
    up, and keeps its filter.  The order is remembered per side — the two satellites
    can be in different ones — so a later filter or F-mode rebuild reloads it the
    same way.  The rebuild replaces the queue, which drops the side's lock and any
    group loop (with the widened row that rode on it).
    """
    state = state.with_satellite(funestra, latest=recent)
    # From the top of the new order: asking for the latest is asking to see what has
    # just arrived, and the reload alone would leave the clip on screen playing with
    # the new order applying only after it.
    result = _rebuild_satellite(funestra, state.satellite(funestra).filter, state, config, start_at_top=True)
    state = state.with_satellite(funestra, locked=False)
    state = clear_side_grouping(state, funestra)
    funestra_name = RoomFunestra(funestra).label
    # The order's own word and nothing else.  The notice flashes on the Funestra it
    # was said to, and this is what that Funestra's HUD calls the order it is now
    # in, so naming the Funestra and then spelling the order out a second time
    # ("Latest: portrait newest-first") only read as a log line that had escaped
    # onto the screen.  The count and the side stay in the log, where they are of
    # use.
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: %s (%d clips)", label, funestra_name, result.count)
    return state, [WindowOp(op="notice", key=label, source=satellite_source(funestra))]


def _browse_at_defaults(side: SatelliteState) -> bool:
    fresh = SatelliteState()
    return replace(side, nav_anchor="", speed=fresh.speed, volume=fresh.volume,
                   muted=fresh.muted) == fresh


def satellite_at_defaults(side: SatelliteState, status: SatelliteStatus) -> bool:
    return _browse_at_defaults(side) and status.speed == 1.0


def _dispatch_reset(
    funestras: tuple[RoomFunestra, ...], state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Put a satellite (or both) back to every default, not just its filter.

    The lock is released, the filter cleared, F-mode dropped, the order returned to
    shuffled, any group loop and widened row and frozen map dropped, and the side
    starts from the top of a freshly-built browse — one clip per subject.  "no
    filter" is the narrow gesture; this one is "put it back how it started".

    F-mode is one of those defaults, and is cleared here rather than left standing:
    a side reset while it was narrowed to the favorites came back still narrowed to
    them, which is a browse of a few dozen clips wearing the label of the whole
    library.  The flag goes into the state before the rebuild, because the rebuild
    reads it back to decide how wide to build.

    A side already sitting at those defaults is left alone entirely.  The rebuild
    reshuffles and starts at the top, so a second press landed on a different clip
    than the first, and a third on another again: a button that says "put it back"
    was the fastest way to keep changing what was playing.  Nothing is narrowed by
    then, so there is nothing the press could put back.
    """
    ops: list[WindowOp] = []
    for funestra in funestras:
        # Every default is the empty value of its field, so "already reset" is
        # the side's whole SatelliteState sitting at the default — a narrowing the
        # reset clears cannot be one this test forgets.  (The nav anchor is
        # already gone: every side command that is not itself a nav step clears
        # it on the way in, so no reset has ever seen one set.)
        if satellite_at_defaults(state.satellite(funestra),
                                 read_satellite_status(config.satellite(funestra).status_file)):
            logger.info("Reset %s: already at its defaults", satellite_source(funestra))
            continue
        if not _browse_at_defaults(state.satellite(funestra)):
            state = cancel_lock(funestra, state, config)
            state = state.with_satellite(
                funestra, latest=False, filter="", favorites_filter=False,
                enhancement=EnhancementLevel.BOTH, nav_anchor="")
            state = clear_side_grouping(state, funestra)
            result = _rebuild_satellite(funestra, "", state, config, start_at_top=True)
            logger.info("Reset %s: %s", satellite_source(funestra), result.log_message)
        send_satellite(config, funestra, NORMAL_SPEED)
        ops.append(WindowOp(op="notice", key="Reset", source=satellite_source(funestra)))
    return state, ops


def _rebuild_satellite(
    funestra: RoomFunestra, query: str, state: BridgeState, config: BridgeConfig,
    *, start_at_top: bool = False,
) -> SatelliteFilterFlowResult:
    """Rebuild one satellite's browse under *query* and its own current ordering.

    The single place a satellite's playlist is rebuilt from its sources, so a
    filter and a reorder cannot drift apart in how they read the side's state.
    ``start_at_top`` is for the callers that mean "start over" — a reorder or a
    reset — since the reload otherwise keeps the clip on screen and carries on from
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
        enhancement=satellite.enhancement,
        regen_metadata_root=config.regen_metadata_root,
        regen_media_root=config.regen_media_root,
    )


# Which of what a satellite plays gets through, by whether an enhanced version
# exists: one verb per level, as the Main Funestra's shapes have one per state.
_ENHANCEMENT_COMMANDS: dict[str, tuple[RoomFunestra, EnhancementLevel]] = {
    f"{funestra.label}_enhancement_{level}": (funestra, level)
    for funestra in RoomFunestra.SATELLITES
    for level in EnhancementLevel
}


def _dispatch_enhancement(
    funestra: RoomFunestra, level: EnhancementLevel, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Narrow what *funestra* plays to the clips that have an enhanced version,
    to the ones that have none, or widen it back; a level that keeps nothing
    leaves the playlist, and the level, as they were."""
    if hosts(funestra, state, config):
        return _dispatch_slideshows_enhancement(funestra, level, state, config)
    if state.satellite(funestra).enhancement is level:
        return state, []
    narrowed = state.with_satellite(funestra, enhancement=level)
    result = _rebuild_satellite(funestra, narrowed.satellite(funestra).filter, narrowed, config)
    logger.info(result.log_message)
    if not result.applied:
        return state, [WindowOp(op="notice", key=result.log_message,
                                source=satellite_source(funestra), level=logging.WARNING)]
    return narrowed, [WindowOp(op="notice", key=level.flash, source=satellite_source(funestra),
                               level=NOTICE)]


def _dispatch_slideshows_enhancement(
    funestra: RoomFunestra, level: EnhancementLevel, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Said to a side in Origenerator mode: the slideshow's own enhanced-only
    switch, which the hosted app hears as its words or as the switch pressed
    off; a slideshow has no originals-only to offer."""
    if level is EnhancementLevel.ORIGINAL:
        return ignored(state, IN_ORIGENERATOR_MODE)
    if level is EnhancementLevel.ENHANCED:
        append_command(config.origenerator_cmd_file,
                       said_to_the_hosted_app(funestra.label, ENHANCED_ONLY))
    elif show_enhanced_filter(config.satellite(funestra)):
        append_command(config.origenerator_cmd_file, f"{funestra.label}_enhanced")
    return state, []


def _dispatch_set_filter(
    funestras: tuple[RoomFunestra, ...], query: str, state: BridgeState, config: BridgeConfig
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
            state = clear_side_grouping(state, funestra)
        logger.info(result.log_message)
        # A filter that selected nothing left the playlist untouched — a dead end,
        # so a warning like the other no-effect notices.
        level = NOTICE if result.applied else logging.WARNING
        ops.append(WindowOp(op="notice", key=result.log_message, source=satellite_source(funestra), level=level))
    return state, ops


# Plain playlist navigation, per side.
_TRANSPORT_COMMANDS: dict[str, tuple[RoomFunestra, str]] = {
    "portrait_prev": (RoomFunestra.PORTRAIT, PREV),
    "portrait_next": (RoomFunestra.PORTRAIT, NEXT),
    "landscape_prev": (RoomFunestra.LANDSCAPE, PREV),
    "landscape_next": (RoomFunestra.LANDSCAPE, NEXT),
}


def _transport(funestra: RoomFunestra, verb: str, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state = cancel_lock(funestra, state, config)
    if verb == NEXT and is_single_video_loop(funestra, state, config):
        state, _loop_off = no_loop(funestra, state, config)
    send_satellite(config, funestra, verb)
    return state, []


# A satellite's own playback rate, and its funscript moves: each verb goes to
# that satellite as it is.
_SENT_TO_A_SATELLITE: dict[str, tuple[RoomFunestra, str]] = {
    f"{funestra.label}_{act}": (funestra, verb)
    for funestra in RoomFunestra.SATELLITES
    for act, verb in (*PLAYBACK_RATE_ACTS.items(), *FUNSCRIPT_MOVES.items())
}


def _send_to_satellite(funestra: RoomFunestra, verb: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if verb == JUMP_TO_FUNSCRIPT and (refused := skip_ahead_refused(funestra, state)):
        return state, refused
    send_satellite(config, funestra, verb)
    return state, []


def _no_loop(funestra: RoomFunestra, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return no_loop(funestra, state, config)


def _reset(funestras: tuple[RoomFunestra, ...], state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_reset(funestras, state, config)


def _set_filter(funestras: tuple[RoomFunestra, ...], query: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_set_filter(funestras, query, state, config)


def _parsed_nav(command: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """Keyboard navigation of the HUD map: "<side>_nav_<dir>" moves the
    selection and switches the satellite to it."""
    nav = _parse_nav(command)
    if nav is None:
        return None
    return navigate_hud(*nav, state, config)


def _parsed_play_video(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """A HUD thumbnail click: "<side>_play_video|<path>" switches straight to
    that clip.  The path rides after the "|" ("|" is illegal in a Windows path,
    so it is an unambiguous delimiter)."""
    if "_play_video|" not in command:
        return None
    head, _, path = command.partition("|")
    return switch_to_video(
        RoomFunestra.PORTRAIT if head.startswith("portrait_") else RoomFunestra.LANDSCAPE,
        path, state, config)


def _parsed_lock_video(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """Double-click of a HUD thumbnail: "<side>_lock_video|<path>" — switch and lock."""
    if "_lock_video|" not in command:
        return None
    head, _, path = command.partition("|")
    return _dispatch_lock_video(
        RoomFunestra.PORTRAIT if head.startswith("portrait_") else RoomFunestra.LANDSCAPE,
        path, state, config)


def _parsed_filter(command: str, state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    filter_target = decode_filter_command(command)
    if filter_target is None:
        return None
    scope, query = filter_target
    return _dispatch_set_filter(RoomFunestra.for_scope(scope), query, state, config)


def _parsed_satellite_speed(command: str, state: BridgeState, config: BridgeConfig,
                            _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    for funestra in RoomFunestra.SATELLITES:
        verb = percent_rate(command, f"{funestra.label}_speed_")
        if verb is not None:
            send_satellite(config, funestra, verb)
            return state, []
    return None


# The two browse orderings, per satellite: Latest reloads newest-first, Shuffle
# reshuffles; the Main Funestra's pair is main_list_commands'.
_REORDER_COMMANDS: dict[str, tuple[RoomFunestra, bool]] = {
    "portrait_latest": (RoomFunestra.PORTRAIT, True),
    "landscape_latest": (RoomFunestra.LANDSCAPE, True),
    "portrait_shuffle": (RoomFunestra.PORTRAIT, False),
    "landscape_shuffle": (RoomFunestra.LANDSCAPE, False),
}


def _reorder(funestra: RoomFunestra, recent: bool, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_reorder(funestra, recent, state, config)


def _enhancement(funestra: RoomFunestra, level: EnhancementLevel, state: BridgeState,
                 config: BridgeConfig, _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_enhancement(funestra, level, state, config)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_enhancement, funestra, level)
       for cmd, (funestra, level) in _ENHANCEMENT_COMMANDS.items()},
    **{cmd: partial(_transport, funestra, verb)
       for cmd, (funestra, verb) in _TRANSPORT_COMMANDS.items()},
    **{cmd: partial(_send_to_satellite, funestra, verb)
       for cmd, (funestra, verb) in _SENT_TO_A_SATELLITE.items()},
    **{cmd: partial(_toggle_lock, funestra) for cmd, funestra in LOCK_TOGGLES.items()},
    **{cmd: partial(_lock_as_said, funestra, locked)
       for cmd, (funestra, locked) in LOCK_STATE_COMMANDS.items()},
    "portrait_trash": partial(_discard, RoomFunestra.PORTRAIT),
    "landscape_trash": partial(_discard, RoomFunestra.LANDSCAPE),
    **{cmd: partial(cycle_variant, funestra, kind)
       for cmd, (funestra, kind) in _CYCLE_COMMANDS.items()},
    **{cmd: partial(cycle_version, funestra, delta)
       for cmd, (funestra, delta) in _VERSION_COMMANDS.items()},
    **{cmd: partial(more_seeds, funestra) for cmd, funestra in _MORE_SEEDS_SIDES.items()},
    **{cmd: partial(wrong_action, funestra) for cmd, funestra in _WRONG_ACTION_SIDES.items()},
    **{cmd: partial(group_loop, funestra, axis)
       for cmd, (funestra, axis) in _LOOP_COMMANDS.items()},
    **{cmd: partial(loop_cycle, funestra) for cmd, funestra in _LOOP_CYCLE_SIDES.items()},
    **{cmd: partial(_no_loop, funestra) for cmd, funestra in _NO_LOOP_SIDES.items()},
    **{cmd: partial(_dispatch_lock_action, funestra)
       for cmd, funestra in _LOCK_ACTION_SIDES.items()},
    **{cmd: partial(_reorder, funestra, recent)
       for cmd, (funestra, recent) in _REORDER_COMMANDS.items()},
    **{cmd: partial(_reset, funestras) for cmd, funestras in _RESET_SIDES.items()},
    **{cmd: partial(_set_filter, funestras, "") for cmd, funestras in _NO_FILTER_SIDES.items()},
}

PARSED_FORMS = (_parsed_nav, _parsed_play_video, _parsed_lock_video, _parsed_filter,
                _parsed_satellite_speed)

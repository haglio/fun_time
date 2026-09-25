"""Python-side command dispatcher for the Windows bridge: one dispatch
command in, an updated state and a list of window ops out.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from player_core.console import (
    OSR2_CONTROL_BUTTONS,
    OSR2_CONTROL_OFF,
    OSR2_DRIVING,
    OSR2_PARKED,
    OSR2_RETRACTED,
)
from player_core.file_channel import append_command
from player_core.hud_status import F_MODE_LABEL, LATEST_LABEL, SHUFFLE_LABEL
from player_core.modes import LengthMode, LoopState
from player_core.player_verbs import (
    LOCK_OFF,
    LOCK_ON,
    NEXT,
    PREV,
    SET_SPEED,
    SPEED_DOWN,
    SPEED_UP,
    TOGGLE_LOCK,
    TRASH,
)

from .audio_volume import MAX_VOLUME, MIN_VOLUME, VOLUME_STEP, publish_audio_level
from .bridge_records import BridgeConfig, WindowOp
from .broker_control import PARK_CMD, RESUME_CMD, RETRACT_CMD, write_broker_command
from .content import load_web_providers
from .event_log import (
    FAVORITE,
    NOTICE,
    SOURCE_LANDSCAPE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
)
from .filter_vocab import decode_filter_command, set_command
from .lock import build_discard_plan, build_lock_toggle_plan
from .media_actions import ensure_in_favs, make_web_url_from_path, move_to_weird, remove_from_favs
from .media_metadata import forget_indexed_clip
from .mode_plan import MAIN_GENAU_MODE, MAIN_VIDEO_MODE, main_player_displays
from .modes import VideoShapes, is_favorite_path, read_favs_content
from .omnipause import build_omnipause_plan
from .player_status import MainPlayerStatus, read_genau_status, read_main_player_status
from .players import Player
from .random_favs_browser import FavEntry, target_for_fav
from .rfb_tab_page import tabs_dir, write_lock_tab_page
from .runtime_flow import (
    FMODE_PLAYERS,
    SatelliteFilterFlowResult,
    SatelliteFmodeInputs,
    apply_enter_omnipause,
    apply_fmode,
    apply_leave_omnipause,
    apply_main_fmode,
    apply_mode_switch,
    apply_satellite_filter,
    apply_satellites_switch,
)
from .satellite_groups import (
    cancel_lock,
    clear_side_grouping,
    cycle_variant,
    cycle_version,
    group_loop,
    is_single_video_loop,
    lock_a_loop_left_with_one_clip,
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
from .satellites_mode import (
    ORIGENERATOR_MODE,
    VIDEO_MODE,
    origenerator_shows,
    toggled_satellites_mode,
)
from .shared_state import BridgeState, SatelliteState
from .voice_commands import ORIGENERATOR_PHRASES
from .watch_stats import record_watch_event, watch_stats_path
from .window_roles import visible_main_slot_roles

logger = logging.getLogger(__name__)


_GENAU_CMD_MAP = {
    "robot_hand_amplitude_down": "AMPLITUDE_DOWN",
    "robot_hand_amplitude_up": "AMPLITUDE_UP",
    "robot_hand_center_down": "CENTER_DOWN",
    "robot_hand_center_up": "CENTER_UP",
    "robot_hand_cycle_shape": "CYCLE_SHAPE",
    "robot_hand_cycle_shape_prev": "CYCLE_SHAPE_PREV",
    # Cruise varies the motion, never which clip plays — moving on from a clip is
    # what an unlocked Genau does by itself, at the pace below.
    "robot_hand_toggle_cruise": "TOGGLE_CRUISE",
    "robot_hand_cruise_on": "CRUISE_ON",
    "robot_hand_cruise_off": "CRUISE_OFF",
    "robot_hand_toggle_learned": "TOGGLE_LEARNED",
    "robot_hand_learned_on": "LEARNED_ON",
    "robot_hand_learned_off": "LEARNED_OFF",
    # How long an unlocked Genau leaves each clip on screen, a second at a
    # time; the padlock (_MAIN_LOCK_COMMANDS) is the switch, this is its pace.
    "genau_clip_seconds_down": "CLIP_SECONDS_DOWN",
    "genau_clip_seconds_up": "CLIP_SECONDS_UP",
    # Condemning a clip outright — Genau's counterpart of a satellite's weird.
    "genau_weird_clip": "WEIRD",
    "genau_prev_clip": PREV,
    "genau_next_clip": NEXT,
    # The motion's rate as the console's own ± marks beside the wave send it —
    # Genau's alone; the unqualified pair is _SPEED_BY_DRIVER below.
    "robot_hand_speed_down": "SPEED_DOWN",
    "robot_hand_speed_up": "SPEED_UP",
}


# Speed control splits by which control said it: the console's ± marks move the
# engine they sit next to (_GENAU_CMD_MAP, _PLAYBACK_RATE_ACTS), while this bare
# pair — spoken, or J/L — carries no label and follows whichever engine holds
# the OSR2 (see :func:`_speed_target` for the whole routing).
_SPEED_BY_DRIVER = {
    "speed_down": SPEED_DOWN,
    "speed_up": SPEED_UP,
}
# A video's own playback rate, as opposed to the motion's, said of one player.
_PLAYBACK_RATE_ACTS = {
    "speed_down": SPEED_DOWN,
    "speed_up": SPEED_UP,
    "speed_min": f"{SET_SPEED} min",
    "speed_max": f"{SET_SPEED} max",
}
# Where Genau is what shows, either end of the main player's range reaches its motion.
_GENAU_RATE_ENDS = {
    "speed_min": "SPEED 0",
    "speed_max": "SPEED 100",
}


def _percent_rate(command: str, prefix: str) -> str | None:
    """'main_player_speed_150' -> 'SET_SPEED 1.5' (percent-of-normal -> multiplier)."""
    if not command.startswith(prefix):
        return None
    try:
        pct = int(command[len(prefix):])
    except ValueError:
        return None
    return f"{SET_SPEED} {pct / 100:g}"


def _speed_target(state: BridgeState, config: BridgeConfig, *, by_driver: bool) -> str:
    """Which engine a speed command drives.

    genau mode -> 'genau'.  Video mode runs both, so an engine-named command
    goes where its name says — the video's rate to the main player, the one on screen —
    while the unqualified nudge follows the OSR2: the main player's funscript while it is
    driving, else the Robot Hand.  The hand is paused for the whole of a
    scripted stretch, so a nudge sent there then reaches an engine that cannot
    move — and a device nobody is driving, held at either end or let go, has no
    motion to speed up either, so the nudge reaches the video then as well.
    """
    if not main_player_displays(state.main_mode):
        return "genau"
    if not by_driver or state.osr2_control != OSR2_DRIVING:
        return "main_player"
    if read_main_player_status(config.main_player_status_file).funscript_driving:
        return "main_player"
    return "genau"


_DEFAULT_LENGTH_MODE = LengthMode.MIXED

_MAIN_PLAYER_CMD_MAP = {
    "main_player_record_down": "RECORD_DOWN",
    "main_player_record_up": "RECORD_UP",
    "main_player_record_tap": "RECORD_TAP",
    "main_player_loop_cancel": "LOOP_CANCEL",
    "main_player_cycle_version": "CYCLE_VERSION",
    "main_player_cycle_version_back": "CYCLE_VERSION_BACK",
    "main_player_toggle_length": "TOGGLE_LENGTH_MODE",
    "main_player_length_shorts": "SET_LENGTH_MODE shorts",
    "main_player_length_full": "SET_LENGTH_MODE full",
    "main_player_length_mixed": f"SET_LENGTH_MODE {_DEFAULT_LENGTH_MODE}",
    "main_player_length_none": "SET_LENGTH_MODE none",
    "main_player_compilation": "PLAY_COMPILATION",
    "main_player_end_compilation": "END_COMPILATION",
    "main_player_full_vid": "PLAY_FULL_VID",
    "main_player_clip_jump": "PLAY_CLIP_JUMP",
    # Funscript navigation: past this video's quiet stretch, or on to the next
    # video in the playlist that has a script at all (landing on its action).
    # Only the main player can answer either — it holds the playlist's funscript column and
    # the parsed script of what is playing.
    "main_player_funscript_jump": "JUMP_TO_FUNSCRIPT",
    "main_player_next_funscripted": "NEXT_FUNSCRIPTED",
}


_NUMERIC_PREFIXES = {
    "robot_hand_amp_": "AMP",
    "robot_hand_center_": "CENTER",
    "robot_hand_speed_": "SPEED",
    # Seconds a clip holds the screen, unlike the 0-100 axes above.
    "genau_clip_seconds_": "CLIP_SECONDS",
}


def _parse_numeric_command(command: str) -> str | None:
    """Parse 'robot_hand_amp_50' -> 'AMP 50', etc."""
    for prefix, keyword in _NUMERIC_PREFIXES.items():
        if command.startswith(prefix):
            value_str = command[len(prefix):]
            try:
                int(value_str)
            except ValueError:
                return None
            return f"{keyword} {value_str}"
    return None


def _toggle_lock(
    player: Player, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    locked = state.satellite(player).locked
    current_path = satellite_current(config, player)
    # "Lock" names the video the speaker had in front of them.  If the satellite
    # auto-advanced while the phrase was being recognized, bring that video back
    # and lock it — the whole point of the lock is to keep watching *it*.  An
    # unlock needs no such rescue: a locked satellite repeats one video and
    # cannot have advanced.
    if not locked and target_path and not same_video(target_path, current_path):
        play_video(config, player, target_path)
        logger.info("Lock back-dated to %s (player %d had advanced)", target_path, player)
        current_path = target_path
    plan = build_lock_toggle_plan(player=player, locked=locked, current_path=current_path)
    send_satellite(config, player, LOCK_ON if plan.next_locked else LOCK_OFF)
    if plan.ensure_in_favs and current_path:
        ensure_in_favs(config.favs_file, current_path)
        # Locking is the strongest positive watch signal ("breeding" weight).
        record_watch_event(watch_stats_path(config.state_dir), current_path, "lock")
    if plan.advance_playlist:
        # Unlocking moves on from the clip you were dwelling on, rather than
        # replaying it once more before the auto-advance.
        send_satellite(config, player, NEXT)
    if plan.log_message:
        logger.info(plan.log_message)
    lock_ops: list[WindowOp] = []
    if plan.open_rfb_tab and current_path:
        # Resolved exactly like an RFB startup tab, and deferred after the same
        # Ctrl+R landing page, so a lock never drops a heavy generate page on you.
        providers = load_web_providers()
        target = target_for_fav(
            FavEntry(local_path=current_path, web_url=make_web_url_from_path(current_path, providers)),
            config.regen,
            providers,
        )
        if target.url:
            uri = write_lock_tab_page(
                tabs_dir(config.state_dir), target, loopback_port=config.loopback_port)
            lock_ops.append(WindowOp(op="open_rfb_tab", key=uri))
    # A lock does not end a group loop: it holds one clip (mpv ``loop_file``) and
    # leaves the loop's queue exactly as it was, so unlocking drops the side straight
    # back into cycling that group.  The loop therefore stays in state — a lock is a
    # pause at one position *inside* the loop, and the HUD goes on drawing the loop
    # (lit button, group rectangle, frozen map) with the held clip ringed.
    return state.with_satellite(player, locked=plan.next_locked), lock_ops


def _discard(
    player: Player, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    locked = state.satellite(player).locked
    current_path = satellite_current(config, player)
    # "Weird" judges the video the speaker saw.  When the satellite advanced
    # while the phrase was being recognized, jump back to the condemned clip
    # before trashing it, so the wrong (innocent) clip is never the one dropped.
    condemned = target_path or current_path
    if not condemned:
        logger.info("Nothing discarded on player %d: it has not said which clip it is showing", player)
        return state, []
    already_moved_on = bool(target_path) and not same_video(target_path, current_path)
    # Whether this is a demotion or a condemnation is read from the same favs
    # file that lights the HUD's ★ for this clip, so the key does what the badge
    # on screen implies: a starred clip loses the star, an unstarred one goes.
    is_favorite = is_favorite_path(condemned, read_favs_content(config.favs_file))
    plan = build_discard_plan(player=player, current_path=condemned, is_favorite=is_favorite)
    if locked:
        # A locked satellite is repeat-one; drop the lock so TRASH advances into
        # the playlist instead of looping the clip that replaced the discarded one.
        send_satellite(config, player, LOCK_OFF)
    if plan.remove_from_favs:
        remove_from_favs(config.favs_file, condemned)
    if plan.advance_playlist:
        if plan.drop_from_playlist:
            if already_moved_on:
                play_video(config, player, condemned)
            # TRASH drops the current clip from the playlist and plays the next.
            send_satellite(config, player, TRASH)
        elif not already_moved_on:
            # A demotion leaves the clip in the playlist, so this is a plain
            # advance (NEXT) and PREV comes straight back to it.  Nothing has to
            # be done to the clip itself, so a satellite that already moved on is
            # left alone rather than dragged back to a clip it would leave again.
            send_satellite(config, player, NEXT)
    state = state.with_satellite(player, locked=False)
    lock_ops: list[WindowOp] = []
    if plan.move_to_weird:
        move_to_weird(config.weird_dir, Path(condemned))
        state, lock_ops = lock_a_loop_left_with_one_clip(player, state, config, condemned)
        forget_indexed_clip(condemned)
    if plan.log_message:
        logger.info(plan.log_message)
    # Which of the two things this key does is invisible otherwise: both look
    # like "the clip went away", and the ★ that tells them apart is gone by the
    # time you could read it.  The color says it too — undoing a favoriting is
    # green, condemning a clip that was never one is not.
    discard_ops = (
        [WindowOp(
            op="notice", key=plan.notice_message, source=satellite_source(player),
            level=FAVORITE if plan.notice_about_favorites else NOTICE,
        )]
        if plan.notice_message
        else []
    )
    return state, [*discard_ops, *lock_ops]


# The satellite and the variation axis each cycle command reaches.
_CYCLE_COMMANDS = {
    "portrait_cycle_action": (Player.PORTRAIT, "action"),
    "portrait_cycle_seed": (Player.PORTRAIT, "seed"),
    "landscape_cycle_action": (Player.LANDSCAPE, "action"),
    "landscape_cycle_seed": (Player.LANDSCAPE, "seed"),
}

_VERSION_COMMANDS: dict[str, tuple[Player, int]] = {
    "portrait_cycle_version": (Player.PORTRAIT, 1),
    "portrait_cycle_version_back": (Player.PORTRAIT, -1),
    "landscape_cycle_version": (Player.LANDSCAPE, 1),
    "landscape_cycle_version_back": (Player.LANDSCAPE, -1),
}

# "more seeds" — see :func:`satellite_groups.more_seeds`.
_MORE_SEEDS_SIDES = {"portrait_more_seeds": Player.PORTRAIT,
                     "landscape_more_seeds": Player.LANDSCAPE}


# Slot and axis per group-loop command — see :func:`satellite_groups.group_loop`.
_LOOP_COMMANDS: dict[str, tuple[Player, str]] = {
    "portrait_action_loop": (Player.PORTRAIT, "action"),
    "portrait_seed_loop": (Player.PORTRAIT, "seed"),
    "landscape_action_loop": (Player.LANDSCAPE, "action"),
    "landscape_seed_loop": (Player.LANDSCAPE, "seed"),
}

_LOCK_ACTION_SIDES: dict[str, Player] = {
    "portrait_lock_action": Player.PORTRAIT,
    "landscape_lock_action": Player.LANDSCAPE,
}

# "reset" clears a satellite's filter and reshuffles it back to the default
# browse; "both reset" expands to these two before dispatch.
_RESET_SIDES: dict[str, tuple[Player, ...]] = {
    "portrait_reset": (Player.PORTRAIT,),
    "landscape_reset": (Player.LANDSCAPE,),
}

MAIN_RESET = "main_reset"

# "no loop" ends a group loop but, unlike reset, keeps the satellite's filter.
_NO_LOOP_SIDES: dict[str, Player] = {
    "portrait_no_loop": Player.PORTRAIT,
    "landscape_no_loop": Player.LANDSCAPE,
}

# The key command that steps a side's loop cycle (seed, action, off — see
# :func:`satellite_groups.loop_cycle`), against the explicit <side>_seed_loop /
# <side>_action_loop / <side>_no_loop the voice commands reach.
_LOOP_CYCLE_SIDES: dict[str, Player] = {
    "portrait_loop": Player.PORTRAIT,
    "landscape_loop": Player.LANDSCAPE,
}

# "no filter" drops just the filter — the narrow counterpart of reset, which puts
# the whole side back to its defaults.
_NO_FILTER_SIDES: dict[str, tuple[Player, ...]] = {
    "portrait_no_filter": (Player.PORTRAIT,),
    "landscape_no_filter": (Player.LANDSCAPE,),
}

# A satellite's own minimize button (``fun_time.satellite_buttons``), by the window
# role the dispatch loop resolves it to.  Every player's window here is
# borderless, so none of them carries a minimize button of its own, and the only
# other way to park one was the dashboard's minimize — which takes the whole room
# down together.
_MINIMIZE_ROLES: dict[str, str] = {
    "portrait_minimize": "portrait",
    "landscape_minimize": "landscape",
}

# The main player's own console button (``fun_time.console_buttons``).  It names the *slot*
# rather than a window, because two players share that rect.
MAIN_MINIMIZE = "main_minimize"


def _minimize_ops(command: str, main_mode: str) -> list[WindowOp] | None:
    """The windows *command* asks to have parked, or None when it asks for none.

    A satellite names its own window.  The main player names its slot, which the main player
    and Genau share — so its button parks whichever of the pair the mode has on
    screen, and never the one the mode has already put away: minimizing a hidden
    window is what drags it back into view.
    """
    role = _MINIMIZE_ROLES.get(command)
    if role is not None:
        return [WindowOp(op="minimize_role", key=role)]
    if command == MAIN_MINIMIZE:
        return [WindowOp(op="minimize_role", key=slot_role)
                for slot_role in visible_main_slot_roles(main_mode)]
    return None

# The two browse orderings, per player: Latest reloads newest-first, Shuffle
# reshuffles.  The main player is 1 and reloads through the main player rather than through
# a satellite rebuild — the table only says which player and which order.
_REORDER_COMMANDS: dict[str, tuple[Player, bool]] = {
    "main_latest": (Player.MAIN, True),
    "portrait_latest": (Player.PORTRAIT, True),
    "landscape_latest": (Player.LANDSCAPE, True),
    "main_shuffle": (Player.MAIN, False),
    "portrait_shuffle": (Player.PORTRAIT, False),
    "landscape_shuffle": (Player.LANDSCAPE, False),
}

# Which shapes of video the main player's browse may reach.  One verb per state
# rather than a toggle each: the console's pair of buttons has four states
# between them, and a press names the state it asks for.
_PROJECTION_COMMANDS: dict[str, tuple[bool, bool]] = {
    "main_projection_both": (True, True),
    "main_projection_vr": (True, False),
    "main_projection_flat": (False, True),
    "main_projection_none": (False, False),
}

# What each is called where it is flashed.
_PROJECTION_LABELS: dict[tuple[bool, bool], str] = {
    (True, True): "2D + VR",
    (True, False): "VR only",
    (False, True): "2D only",
    (False, False): "No videos left",
}


# The same two orders as Genau answers to, keyed by ``recent``.  Every other
# player is handed a rewritten playlist file; Genau owns its own sequence and
# rescans its clips folder for itself, so its order crosses as a verb.
_GENAU_ORDER_CMD: dict[bool, str] = {True: "LATEST", False: "SHUFFLE"}


# "Wrong action" — the clip is fine, its label is not.  Per side, like every
# other judgement of the clip on screen.
_WRONG_ACTION_SIDES: dict[str, Player] = {"portrait_wrong_action": Player.PORTRAIT,
                                          "landscape_wrong_action": Player.LANDSCAPE}


def _dispatch_lock_action(
    player: Player, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Filter the satellite to the current clip's action — "portrait [act]",
    with the act read off the clip instead of spoken."""
    current = target_path or satellite_current(config, player)
    if not current:
        return state, []
    action = video_action_label(current, config)
    if not action:
        return state, [WindowOp(op="notice", key="No action metadata", source=satellite_source(player), level=logging.WARNING)]
    return _dispatch_set_filter((player,), action.lower(), state, config)


def _dispatch_lock_video(
    player: Player, path: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Double-click a HUD thumbnail: switch to *path* and lock it (repeat-one).

    When already locked the satellite is repeat-one, so playing the picked clip
    just moves the lock onto it; when unlocked, toggling the lock with the target
    both switches to it and locks it (the same back-dating a spoken "lock" uses).
    """
    if state.satellite(player).locked:
        return switch_to_video(player, path, state, config)
    return _toggle_lock(player, state, config, target_path=path)


_NAV_DIRECTIONS = ("up", "down")


def _parse_nav(command: str) -> tuple[int, str] | None:
    """``(slot, direction)`` for a ``<side>_nav_<dir>`` command, else None."""
    for prefix, player in (("portrait_nav_", Player.PORTRAIT),
                          ("landscape_nav_", Player.LANDSCAPE)):
        if command.startswith(prefix):
            direction = command[len(prefix):]
            if direction in _NAV_DIRECTIONS:
                return player, direction
    return None


def _is_hud_nav_command(command: str) -> bool:
    """Whether *command* drives HUD keyboard navigation — a direction step.  Those
    manage the side's nav anchor themselves, so the generic "any other side
    command re-homes the map" rule leaves them alone."""
    return _parse_nav(command) is not None


# The main slot's lock: repeat what is on screen, or let it move on — the main player's
# video into the next playlist entry, Genau's clip into the next clip after its
# interval.  Both players answer these three verbs and both open locked (the
# routing is :func:`_main_lock`'s).  The toggle is the key and the button; the
# absolute pair is what the spoken forms send, since a speaker asks for the
# state they want.
_MAIN_LOCK_COMMANDS = {
    "main_lock": TOGGLE_LOCK,
    "main_lock_on": LOCK_ON,
    "main_lock_off": LOCK_OFF,
}

_MAIN_PLAYER_RESET_VERBS = (
    _MAIN_PLAYER_CMD_MAP["main_player_length_mixed"],
    _MAIN_PLAYER_CMD_MAP["main_player_loop_cancel"],
    _MAIN_LOCK_COMMANDS["main_lock_off"],
    _percent_rate("main_player_speed_100", "main_player_speed_"),
)

# What makes the main player the one a later bare command reaches: navigating it,
# locking it, or naming its F-mode.  The satellites' own keys select a side the
# same way, and every ``portrait_``/``landscape_`` command does it by prefix — so
# without the F-mode forms here, "main f mode" would be the one way of
# addressing a player that did not leave it addressed.
_MAIN_SELECTING_COMMANDS = frozenset(
    {"main_next", "main_prev", MAIN_RESET}
    | set(_MAIN_LOCK_COMMANDS)
    | {f"main_fmode{suffix}" for suffix in ("", "_on", "_off")}
)


def command_player(command: str) -> Player | None:
    """The player a command addresses, or None if it addresses no player.

    The main (the main player) player is selected by its own next/prev navigation, by its
    lock, and by naming its F-mode or its reset — everything it shares with a
    satellite.  It has no weird/cycle, so nothing else selects it.
    """
    if command.startswith("portrait_"):
        return Player.PORTRAIT
    if command.startswith("landscape_"):
        return Player.LANDSCAPE
    if command in _MAIN_SELECTING_COMMANDS or command.startswith("main_player_speed_"):
        return Player.MAIN
    return None


def dispatch_command(
    command: str,
    state: BridgeState,
    config: BridgeConfig,
    *,
    target_path: str = "",
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
    # Parking a player, before the active-side bookkeeping below: this is the one
    # side command that is not about that side's video, and a player just taken
    # off the screen must not become the one a bare "lock" or "next" reaches —
    # that would send the next spoken word to a window nobody can see.
    minimize_ops = _minimize_ops(command, state.main_mode)
    if minimize_ops is not None:
        return state, minimize_ops

    # Any command naming a player (voice or keyboard nav) makes it the active
    # player, so a later player-agnostic "active_*" command knows which to drive.
    player = command_player(command)
    if player is not None:
        state = replace(state, active_player=player)
        # Every player command except a navigation step ends keyboard navigation on
        # that player, so its map re-homes on the live clip; nav commands manage
        # their own anchor.  The main player has none, so its commands end
        # nobody's.
        if not _is_hud_nav_command(command) and player is not Player.MAIN:
            state = state.with_satellite(player, nav_anchor="")

    # In origenerator mode what is said to a side is the hosted app's to
    # answer -- ahead of the handler lookup, whose player handlers it shadows.
    routed = _origenerator_transport(command, state, config)
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


_VOLUME_STEPS = {"audio_volume_down": -VOLUME_STEP, "audio_volume_up": VOLUME_STEP}

# ``audio_set_volume|<0-100>`` — an absolute level, which is what a slider asks
# for where every other audio command asks for a step or a state.  The main player's volume
# control is the one that sends it.
SET_VOLUME_COMMAND = "audio_set_volume"


def _dispatch_set_volume(
    argument: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Set the sound level outright, lifting any mute.

    The level crosses a text channel from another process, so it is not trusted:
    out of range is clamped and unparseable is ignored, rather than raising into
    the dispatch loop over a dragged slider.  It lifts a mute for the reason a
    step does — reaching for the volume is asking to hear something.
    """
    try:
        level = int(argument)
    except ValueError:
        return state, []
    volume = max(MIN_VOLUME, min(MAX_VOLUME, level))
    return _dispatch_audio(replace(state, volume=volume, muted=False), config)

# Mute and unmute each assert a state rather than toggling one, so a phrase
# misheard twice cannot leave the sound the opposite of what was asked for.
_MUTE_COMMANDS = {"audio_mute": True, "audio_unmute": False}


def _step_volume(state: BridgeState, step: int) -> BridgeState:
    """Move the sound level by *step*, staying within the silent/full bounds.

    Asking for a loudness lifts a mute, as reaching for the volume does in the
    Windows mixer: a "louder" that left the room silent would read as the
    command having been missed.
    """
    volume = max(MIN_VOLUME, min(MAX_VOLUME, state.volume + step))
    return replace(state, volume=volume, muted=False)


def _dispatch_audio(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Publish *state*'s sound level to both of the main player's audio sinks,
    and say on screen what it now is."""
    publish_audio_level(
        main_player_cmd_file=config.main_player_cmd_file,
        genau_cmd_file=config.genau_cmd_file,
        audio_volume_file=config.audio_volume_file,
        volume=state.volume,
        muted=state.muted,
    )
    message = "Muted" if state.muted else f"Volume {state.volume}%"
    return state, [WindowOp(op="notice", key=message, source=SOURCE_MAIN)]


def _dispatch_omnipause_toggle(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Esc: whichever of enter and leave the current state calls for."""
    plan = build_omnipause_plan(
        "toggle",
        omni_paused=state.omni_paused,
        main_mode=state.main_mode,
    )
    if plan.action == "enter":
        return _dispatch_enter_omnipause(state, config)
    return _dispatch_leave_omnipause(state, config)


def _dispatch_enter_omnipause(
    state: BridgeState, config: BridgeConfig, *, relief: bool = False
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_enter_omnipause(
        omni_paused=state.omni_paused,
        main_mode=state.main_mode,
        portrait_paused_file=config.portrait_paused_file,
        landscape_paused_file=config.landscape_paused_file,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        main_player_paused_file=config.main_player_paused_file,
        broker_cmd_file=config.broker_cmd_file,
        origenerator_paused_file=config.origenerator_paused_file,
        relief=relief,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    ops = [WindowOp(op="disable_all_topmost"), WindowOp(op="suspend_hotkeys")]
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_leave_omnipause(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_leave_omnipause(
        omni_paused=state.omni_paused,
        main_mode=state.main_mode,
        portrait_paused_file=config.portrait_paused_file,
        landscape_paused_file=config.landscape_paused_file,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        main_player_paused_file=config.main_player_paused_file,
        broker_cmd_file=config.broker_cmd_file,
        origenerator_paused_file=config.origenerator_paused_file,
        osr2_control=state.osr2_control,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    # Un-minimize first, then re-band, then focus: leaving OmniPause is the room
    # coming back, so a player put away with its own minimize button comes back
    # with it — it has no panel left to ask with, its HUD having gone down with the
    # window.  Before the bands, so the re-stack and the activate below land on
    # windows that are actually on screen.
    ops = [WindowOp(op="restore_parked"), WindowOp(op="restore_all_topmost"),
           WindowOp(op="unsuspend_hotkeys")]
    ops.extend(_main_focus_ops())
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _main_focus_ops() -> list[WindowOp]:
    """Re-activate the window on top of the main player (omnipause leave):
    Genau's in both modes — the display in genau mode, the HUD layer over
    The main player's video in video mode."""
    return [WindowOp(op="activate_role", key="genau")]


def _main_slot_ops(main_mode: str) -> list[WindowOp]:
    """Visibility + z-order ops for the main player-slot windows on a mode switch.

    The two players (the main player and Genau) share one screen rect; exactly the mode's
    player(s) are shown and the inactive slot-mate hidden.  The new window is
    shown and activated BEFORE the old one hides so focus never falls through
    to another application.  Finally the pair is re-stacked for the new mode
    (``restack_main``): the main player topmost, with Genau's HUD above it in video mode.
    The main player and Genau overlap, so their z-order is explicit — unlike every other
    window, a plain topmost flag can't say "Genau above the main player, both on top."
    """
    restack = WindowOp(op="restack_main")
    if main_mode == MAIN_GENAU_MODE:
        return [
            WindowOp(op="show_role", key="genau"),
            WindowOp(op="activate_role", key="genau"),
            WindowOp(op="hide_role", key="main_player"),
            restack,
        ]
    return [
        WindowOp(op="show_role", key="main_player"),
        WindowOp(op="show_role", key="genau"),
        WindowOp(op="activate_role", key="genau"),
        restack,
    ]


# Which players each F-mode command reaches, and what it sets them to — None for
# the toggles, True/False for the forms that assert a state and so cannot land on
# the opposite of what was asked when a phrase is misheard twice.  The bare
# command — the F key, the spoken "f mode" — still means all three at once; naming
# a player reaches that one alone, off its own HUD button or its own spoken
# phrase.  ``both_fmode`` never arrives here: the dispatch loop expands every
# both_* into its portrait/landscape pair first.
_FMODE_COMMANDS: dict[str, tuple[tuple[Player, ...], bool | None]] = {
    "fmode_toggle": (FMODE_PLAYERS, None),
    "fmode_on": (FMODE_PLAYERS, True),
    "fmode_off": (FMODE_PLAYERS, False),
    "main_fmode": ((Player.MAIN,), None),
    "main_fmode_on": ((Player.MAIN,), True),
    "main_fmode_off": ((Player.MAIN,), False),
    "portrait_fmode": ((Player.PORTRAIT,), None),
    "portrait_fmode_on": ((Player.PORTRAIT,), True),
    "portrait_fmode_off": ((Player.PORTRAIT,), False),
    "landscape_fmode": ((Player.LANDSCAPE,), None),
    "landscape_fmode_on": ((Player.LANDSCAPE,), True),
    "landscape_fmode_off": ((Player.LANDSCAPE,), False),
}

# Where each player's flash goes, so a sided F-mode reports on that player's own
# display and the all-players one reports to the room.
_FMODE_NOTICE_SOURCE = {
    Player.MAIN: SOURCE_MAIN,
    Player.PORTRAIT: SOURCE_PORTRAIT,
    Player.LANDSCAPE: SOURCE_LANDSCAPE,
}


def _player_f_mode(state: BridgeState, player: Player) -> bool:
    """Whether *player* is in F-mode — the main slot's own flag, or its side's."""
    return state.main_scripted_filter if player is Player.MAIN else state.satellite(player).favorites_filter


def _with_f_mode(state: BridgeState, players: tuple[Player, ...], enabled: bool) -> BridgeState:
    """*state* with each of *players* put into F-mode, or out of it."""
    for player in players:
        state = (replace(state, main_scripted_filter=enabled) if player is Player.MAIN
                 else state.with_satellite(player, favorites_filter=enabled))
    return state


def _next_f_mode(state: BridgeState, players: tuple[Player, ...]) -> bool:
    """What a toggle over *players* should set them all to.

    One player is an ordinary flip.  Several — the F key, or a spoken "f mode" —
    turn on unless every one of them is already on, so the key that means "narrow
    everything" can never leave half the room narrowed and half not: it either
    completes the narrowing or lifts it.
    """
    return not all(_player_f_mode(state, player) for player in players)


def _dispatch_fmode(
    players: tuple[Player, ...], target: bool | None,
    state: BridgeState, config: BridgeConfig,
) -> tuple[BridgeState, list[WindowOp]]:
    """Put *players* into F-mode or out of it, rebuilding only what moves.

    *target* is the state to assert, or None to toggle.  A player already in the
    asked-for state is left out of the rebuild entirely: its playlist file is not
    rewritten, so "portrait f mode on" said twice does not reshuffle the queue the
    first one built.
    """
    # The hosted app's players are not the session's to narrow while it has
    # them: their lists are its own, and a rebuild would write over them.
    if hosting_origenerator(state, config):
        players = tuple(player for player in players if player not in Player.SATELLITES)
    enabled = _next_f_mode(state, players) if target is None else target
    changed = tuple(player for player in players if _player_f_mode(state, player) != enabled)
    result = apply_fmode(
        players=changed,
        enabled=enabled,
        main_recent=state.main_latest,
        main_shapes=main_video_shapes(state, config),
        main_sources=config.main_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        main_player_cmd_file=config.main_player_cmd_file,
        satellites={
            player: SatelliteFmodeInputs(
                sources=config.satellite(player).sources,
                cmd_file=config.satellite(player).cmd_file,
                recent=state.satellite(player).latest,
                filter_query=state.satellite(player).filter,
            )
            for player in Player.SATELLITES
        },
        regen_metadata_root=config.regen_metadata_root,
    )
    if result.players:
        logger.info(result.log_message)
    state = _with_f_mode(state, result.players, enabled)
    # A rebuilt satellite got a new queue, which drops its lock, its group loop and
    # the widened seed row that rode on the loop — the same as any other rebuild.
    for player in Player.SATELLITES:
        if player in result.players:
            state = cancel_lock(player, state, config)
            state = clear_side_grouping(state, player)
    # Flash which way it went, on the display it went on — a sided F-mode reports
    # from that player, the all-players one from the room.  It goes off the players
    # *asked for*, not the ones that moved: "f mode" is a gesture at the room even
    # on the press where only one player was left to narrow.  The dispatch owns
    # this rather than the voice echo, so the F key and the HUD buttons flash it
    # too, not just a spoken "F mode" (which is why fmode is self-reporting — see
    # SELF_REPORTING_COMMANDS).  Enabling is green, since what it narrows to is the
    # favorites and the funscripts; disabling is an ordinary white notice.
    source = _FMODE_NOTICE_SOURCE[players[0]] if len(players) == 1 else SOURCE_SYSTEM
    notice_op = WindowOp(
        op="notice",
        key=f"{F_MODE_LABEL} enabled" if enabled else f"{F_MODE_LABEL} disabled",
        source=source,
        level=FAVORITE if enabled else NOTICE,
    )
    return state, [notice_op]


def _dispatch_main_reorder(
    recent: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Reload the main player in a fresh order — Latest (newest-first) or Shuffle.

    To whichever player owns the main slot's screen, the split the lock makes (see
    ``_MAIN_LOCK_COMMANDS``) and for the same reason: a browse order is about
    what you are looking at.  Sent to the main player regardless, "main latest" said in genau
    mode rewrote a playlist for a player that was neither on screen nor playing,
    and Genau — the one actually showing — went on with the order it launched in.

    Both branches rescan as they go, which is most of what "latest" is for: a clip
    that arrived since is in no list until something looks again.

    The main player's playlist is ours to write, so that branch rewrites the file and hands
    The main player the same RELOAD_PLAYLIST an F-mode change gets, from the top of the new
    order — a reorder filters nothing out, so the main player would otherwise keep the video
    on screen and carry on from wherever it now sits, the newest-first list
    applying only after it and the arrivals never coming up.  Genau has no
    playlist file at all; it owns its own sequence, so it is told the order and
    rescans its clips folder itself.

    Each player's order is remembered under its own flag.  ``main_latest``
    describes the playlist file we built for the main player — a later F-mode rebuild reads it
    to reload the same way round — so recording a Genau reorder there would light
    "Latest" over a main player playlist nobody reordered.  ``genau_latest`` is Genau's,
    and both reach the console, which draws whichever player is showing.
    """
    on_main_player = main_player_displays(state.main_mode)
    if on_main_player:
        state = replace(state, main_latest=recent)
        apply_main_fmode(
            enabled=state.main_scripted_filter,
            main_sources=config.main_sources,
            recent=recent,
            state_dir=config.state_dir,
            main_player_cmd_file=config.main_player_cmd_file,
            start_at_top=True,
            shapes=main_video_shapes(state, config),
            metadata_root=config.regen_metadata_root,
        )
    else:
        state = replace(state, genau_latest=recent)
        append_command(config.genau_cmd_file, _GENAU_ORDER_CMD[recent])
    # The order's own word alone, and self-reported — see _dispatch_reorder.
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: the main player (%s)", label, "main_player" if on_main_player else "genau")
    return state, [WindowOp(op="notice", key=label, source=SOURCE_MAIN)]


def main_video_shapes(state: BridgeState, config: BridgeConfig) -> VideoShapes:
    """The shape filter the main player's next rebuild runs under."""
    return VideoShapes(vr_dirs=config.vr_library_dirs,
                       plays_vr=state.main_plays_vr,
                       plays_flat=state.main_plays_flat)


def _dispatch_main_projection(
    plays_vr: bool, plays_flat: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Narrow the main player's browse to a shape of video, or widen it back.

    A rebuild, so it goes the way F-mode's does.  Asking for the state already
    running rebuilds nothing: a reorder is what reshuffles, and this must not
    become a second way to do it.  Ignored where the rotation holds one shape.
    """
    shapes = main_video_shapes(state, config)
    if not shapes.offered:
        logger.info("No VR library in this session; shape filter ignored")
        return state, []
    if (shapes.plays_vr, shapes.plays_flat) == (plays_vr, plays_flat):
        return state, []
    state = replace(state, main_plays_vr=plays_vr, main_plays_flat=plays_flat)
    if not (plays_vr or plays_flat):
        # Nothing to rebuild from, so the video on screen is held instead: a
        # state you can see, where an empty list would look like a no-op.
        append_command(config.main_player_cmd_file, _MAIN_LOCK_COMMANDS["main_lock_on"])
    else:
        apply_main_fmode(
            enabled=state.main_scripted_filter,
            main_sources=config.main_sources,
            recent=state.main_latest,
            state_dir=config.state_dir,
            main_player_cmd_file=config.main_player_cmd_file,
            shapes=main_video_shapes(state, config),
            metadata_root=config.regen_metadata_root,
        )
    label = _PROJECTION_LABELS[(plays_vr, plays_flat)]
    logger.info("Main player shapes: %s", label)
    return state, [WindowOp(
        op="notice", key=label, source=SOURCE_MAIN,
        level=logging.WARNING if not (plays_vr or plays_flat) else NOTICE)]


def main_player_at_defaults(
    state: BridgeState, config: BridgeConfig, status: MainPlayerStatus
) -> bool:
    if state.main_scripted_filter or state.main_latest or main_video_shapes(state, config).narrows:
        return False
    if not main_player_displays(state.main_mode):
        return True
    return (not status.locked and status.loop_state is LoopState.NORMAL and status.speed == 1.0
            and status.length_mode in (None, _DEFAULT_LENGTH_MODE) and not status.compilation)


def _dispatch_main_reset(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Put the main player back to its defaults, as a satellite's reset does."""
    if main_player_at_defaults(
            state, config, read_main_player_status(config.main_player_status_file)):
        logger.info("Reset main player: already at its defaults")
        return state, []
    if state.main_scripted_filter or state.main_latest or main_video_shapes(state, config).narrows:
        state = replace(
            state, main_scripted_filter=False, main_latest=False, main_plays_vr=True, main_plays_flat=True)
        apply_main_fmode(
            enabled=state.main_scripted_filter,
            main_sources=config.main_sources,
            recent=state.main_latest,
            state_dir=config.state_dir,
            main_player_cmd_file=config.main_player_cmd_file,
            start_at_top=True,
            shapes=main_video_shapes(state, config),
            metadata_root=config.regen_metadata_root,
        )
    if main_player_displays(state.main_mode):
        for verb in _MAIN_PLAYER_RESET_VERBS:
            append_command(config.main_player_cmd_file, verb)
    logger.info("Reset main player")
    return state, [WindowOp(op="notice", key="Reset", source=SOURCE_MAIN)]


def _dispatch_reorder(
    player: Player, recent: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Reload one satellite in a fresh order — Latest (newest-first) or Shuffle.

    Either rescans that side's sources, so clips that have arrived since are picked
    up, and keeps its filter.  The order is remembered per side — the two satellites
    can be in different ones — so a later filter or F-mode rebuild reloads it the
    same way.  The rebuild replaces the queue, which drops the side's lock and any
    group loop (with the widened row that rode on it).
    """
    state = state.with_satellite(player, latest=recent)
    # From the top of the new order: asking for the latest is asking to see what has
    # just arrived, and the reload alone would leave the clip on screen playing with
    # the new order applying only after it.
    result = _rebuild_satellite(player, state.satellite(player).filter, state, config, start_at_top=True)
    state = state.with_satellite(player, locked=False)
    state = clear_side_grouping(state, player)
    player_name = Player(player).label
    # The order's own word and nothing else.  The notice flashes on the player it
    # was said to, and this is what that player's HUD calls the order it is now
    # in, so naming the player and then spelling the order out a second time
    # ("Latest: portrait newest-first") only read as a log line that had escaped
    # onto the screen.  The count and the side stay in the log, where they are of
    # use.  The dispatch owns the notice the way it owns F-mode's, so a spoken
    # reorder is not echoed on top of it (see SELF_REPORTING_COMMANDS).
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: %s (%d clips)", label, player_name, result.count)
    return state, [WindowOp(op="notice", key=label, source=satellite_source(player))]


def satellite_at_defaults(side: SatelliteState) -> bool:
    return replace(side, nav_anchor="") == SatelliteState()


def room_at_defaults(state: BridgeState, config: BridgeConfig, status: MainPlayerStatus) -> bool:
    return (main_player_at_defaults(state, config, status)
            and not hosting_origenerator(state, config)
            and all(satellite_at_defaults(state.satellite(player)) for player in Player.SATELLITES))


def _dispatch_reset(
    players: tuple[Player, ...], state: BridgeState, config: BridgeConfig
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
    for player in players:
        # Every default is the empty value of its field, so "already reset" is
        # the side's whole SatelliteState sitting at the default — a narrowing the
        # reset clears cannot be one this test forgets.  (The nav anchor is
        # already gone: every side command that is not itself a nav step clears
        # it on the way in, so no reset has ever seen one set.)
        if satellite_at_defaults(state.satellite(player)):
            logger.info("Reset %s: already at its defaults", satellite_source(player))
            continue
        state = cancel_lock(player, state, config)
        state = state.with_satellite(
            player, latest=False, filter="", favorites_filter=False, nav_anchor="")
        state = clear_side_grouping(state, player)
        result = _rebuild_satellite(player, "", state, config, start_at_top=True)
        logger.info("Reset %s: %s", satellite_source(player), result.log_message)
        ops.append(WindowOp(op="notice", key="Reset", source=satellite_source(player)))
    return state, ops


def _rebuild_satellite(
    player: Player, query: str, state: BridgeState, config: BridgeConfig,
    *, start_at_top: bool = False,
) -> SatelliteFilterFlowResult:
    """Rebuild one satellite's browse under *query* and its own current ordering.

    The single place a satellite's playlist is rebuilt from its sources, so a
    filter and a reorder cannot drift apart in how they read the side's state.
    ``start_at_top`` is for the callers that mean "start over" — a reorder or a
    reset — since the reload otherwise keeps the clip on screen and carries on from
    where it sat, leaving the new order to apply only after it.
    """
    satellite = state.satellite(player)
    return apply_satellite_filter(
        player=player,
        query=query,
        favorites_filter=satellite.favorites_filter,
        recent=satellite.latest,
        sources=config.satellite(player).sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        cmd_file=config.satellite(player).cmd_file,
        start_at_top=start_at_top,
        regen_metadata_root=config.regen_metadata_root,
    )


def _dispatch_set_filter(
    players: tuple[Player, ...], query: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Apply a metadata filter to *players* and rebuild them.

    ``query`` is the substring to match ("" clears).  Each targeted satellite
    records its own filter in the state so later F-mode / reorder rebuilds keep
    it, then reloads under its own ordering.
    """
    ops: list[WindowOp] = []
    for player in players:
        result = _rebuild_satellite(player, query, state, config)
        # Only remember a filter that actually selected videos: a zero-match
        # filter left the current playlist alone, so recording it would let the
        # next F-mode/reorder rebuild blank the satellite.  A filter that *did* rebuild
        # also replaced any loop's sub-playlist, so the loop (and its widened row)
        # is gone; a zero-match one touched nothing, so a running loop survives it.
        if result.applied:
            state = state.with_satellite(player, filter=query)
            state = clear_side_grouping(state, player)
        logger.info(result.log_message)
        # A filter that selected nothing left the playlist untouched — a dead end,
        # so a warning like the other no-effect notices.
        level = NOTICE if result.applied else logging.WARNING
        ops.append(WindowOp(op="notice", key=result.log_message, source=satellite_source(player), level=level))
    return state, ops


# In origenerator mode each satellite player is the hosted app's: it hands the
# player what to play and answers every press on the buttons it drew.  So what
# is said to a side goes there as it was said, and the session keeps only what
# is about the player itself -- parking its window, and the rate it plays at.
_THE_PLAYERS_OWN = ("_minimize", "_speed_")


def _about_a_satellite(command: str) -> bool:
    """Whether *command* is said to one satellite side, about what it plays."""
    head = command.removeprefix("filter_")
    if not head.startswith(("portrait_", "landscape_")):
        return False
    verb = head.partition("|")[0]
    return not any(own in verb for own in _THE_PLAYERS_OWN)


def _said_to_each_side(command: str) -> tuple[str, ...]:
    target = decode_filter_command(command)
    if target is None or target[0] != "both":
        return (command,)
    return tuple(set_command(player.label, target[1]) for player in Player.SATELLITES)


# The hosted app's own spoken vocabulary, one command per phrase.  The session
# hears them (it owns the room's microphone) and posts the WORDS on the hosted
# app's channel; matching them is the hosted app's own business, since only it
# knows which shelves its tree has and which detail parts have detectors.
_ORIGENERATOR_SPEECH: dict[str, tuple[str, str]] = {
    **{
        f"{side}_say_{phrase.replace(' ', '_')}": (side, phrase)
        for side in ("portrait", "landscape")
        for phrase in ORIGENERATOR_PHRASES
    },
    # One the session already says to a player, which means the same thing to
    # the hosted app's show on that side.
    "portrait_no_filter": ("portrait", "clear filter"),
    "landscape_no_filter": ("landscape", "clear filter"),
}


def routes_to_origenerator(command: str, state: BridgeState, config: BridgeConfig) -> bool:
    """Whether *command* is bound for the hosted app rather than the session.

    In origenerator mode everything said to a side about what it plays goes
    there (:func:`_about_a_satellite`), and so does the app's own spoken vocabulary,
    as the words themselves: the session hears them for the whole room and the
    hosted app matches them.  Public because the dispatch loop asks the same
    question -- its watch tracking must not book a show's step or cull against
    a library video.
    """
    if command not in _ORIGENERATOR_SPEECH and not all(
            _about_a_satellite(said) for said in _said_to_each_side(command)):
        return False
    return hosting_origenerator(state, config)


def hosting_origenerator(state: BridgeState, config: BridgeConfig) -> bool:
    return (config.origenerator_enabled
            and origenerator_shows(state.satellites_mode)
            and config.origenerator_cmd_file is not None)


def _origenerator_transport(
    command: str, state: BridgeState, config: BridgeConfig
) -> list[WindowOp] | None:
    """Hand a side's press to the hosted app, or ``None`` to fall through to
    the session's own handling.

    None of the player-side bookkeeping (lock flags, favorites, RFB tabs)
    applies to a show, and a press goes over as it was posted: the app declared
    the button, so it is what knows the verb.
    """
    if not routes_to_origenerator(command, state, config):
        return None
    spoken = _ORIGENERATOR_SPEECH.get(command)
    if spoken is not None:
        player_name, phrase = spoken
        append_command(config.origenerator_cmd_file, f"{player_name.upper()}_SAY:{phrase}")
        return []
    for said in _said_to_each_side(command):
        append_command(config.origenerator_cmd_file, said)
    return []


def _satellites_slot_ops(satellites_mode: str) -> list[WindowOp]:
    """The window work of a satellites-mode switch -- the RFB-slot counterpart
    of :func:`_main_slot_ops`.

    Entering origenerator mode restores its main window over the RFB and
    promotes it above the fixed roles (``restack_origenerator``); the players
    stay where they are, since they are what shows its slideshows.  Leaving
    parks that window and takes the players back, which the loop does once the
    app has let go of them (``take_back_players``).
    """
    if satellites_mode == ORIGENERATOR_MODE:
        return [
            WindowOp(op="show_role", key="origenerator"),
            WindowOp(op="activate_role", key="origenerator"),
            WindowOp(op="restack_origenerator"),
        ]
    return [
        WindowOp(op="hide_role", key="origenerator"),
        WindowOp(op="take_back_players"),
    ]


def _dispatch_satellites_switch(
    command: str, state: BridgeState, config: BridgeConfig, ops: list[WindowOp]
) -> tuple[BridgeState, list[WindowOp]]:
    if not config.origenerator_enabled:
        return state, [WindowOp(
            op="notice", key="No Origenerator configured",
            level=logging.WARNING)]
    if not state.origenerator_ready:
        # The HUDs draw their Origenerator button dim over the same stretch and
        # post nothing; this answers the key and the spoken word, which have no
        # dim button to look at.
        return state, [WindowOp(
            op="notice", key="Origenerator is still starting",
            level=logging.WARNING)]
    target = {
        "origenerator_activate": ORIGENERATOR_MODE,
        "satellites_video_activate": VIDEO_MODE,
        "satellites_toggle": toggled_satellites_mode(state.satellites_mode),
    }[command]
    result = apply_satellites_switch(
        current_mode=state.satellites_mode,
        target_mode=target,
        omni_paused=state.omni_paused,
        origenerator_cmd_file=config.origenerator_cmd_file,
        channels=[config.satellite(player) for player in Player.SATELLITES],
    )
    state = replace(state, satellites_mode=result.next_mode)
    if result.is_transition:
        ops.extend(_satellites_slot_ops(result.next_mode))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_mode_switch(
    target_mode: str, state: BridgeState, config: BridgeConfig, ops: list[WindowOp]
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_mode_switch(
        current_mode=state.main_mode,
        target_mode=target_mode,
        omni_paused=state.omni_paused,
        genau_cmd_file=config.genau_cmd_file,
        main_player_paused_file=config.main_player_paused_file,
        main_player_cmd_file=config.main_player_cmd_file,
    )
    state = replace(state, main_mode=result.next_mode)
    if result.is_transition:
        ops.extend(_main_slot_ops(result.next_mode))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _video_activate(state: BridgeState, config: BridgeConfig,
                    _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """"video mode", said of no side: the main slot's video AND the satellites'
    players, each through its own switch — a session hosting no Origenerator
    has only the main slot's to make."""
    state, ops = _dispatch_mode_switch(MAIN_VIDEO_MODE, state, config, [])
    if config.origenerator_enabled:
        state, ops = _dispatch_satellites_switch(
            "satellites_video_activate", state, config, ops)
    return state, ops


# --- the handler map ---------------------------------------------------------
# One uniform shape — handler(state, config, target_path) -> (state, ops) —
# assembled from the tables above; ``_target_path`` marks the video-less ones.

Handler = Callable[[BridgeState, BridgeConfig, str], tuple[BridgeState, list[WindowOp]]]

# Plain playlist navigation, per side.
_TRANSPORT_COMMANDS: dict[str, tuple[Player, str]] = {
    "portrait_prev": (Player.PORTRAIT, PREV),
    "portrait_next": (Player.PORTRAIT, NEXT),
    "landscape_prev": (Player.LANDSCAPE, PREV),
    "landscape_next": (Player.LANDSCAPE, NEXT),
}

# The main slot's two mode switches, by their target mode.
_MODE_SWITCH_COMMANDS: dict[str, str] = {
    "genau_activate": MAIN_GENAU_MODE,
    "main_video_activate": MAIN_VIDEO_MODE,
}


def _transport(player: Player, verb: str, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state = cancel_lock(player, state, config)
    if verb == NEXT and is_single_video_loop(player, state, config):
        state, _loop_off = no_loop(player, state, config)
    send_satellite(config, player, verb)
    return state, []


_SATELLITE_SPEEDS: dict[str, tuple[Player, str]] = {
    f"{player.label}_{act}": (player, verb)
    for player in Player.SATELLITES
    for act, verb in _PLAYBACK_RATE_ACTS.items()
}


def _satellite_speed(player: Player, verb: str, state: BridgeState, config: BridgeConfig,
                     _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    send_satellite(config, player, verb)
    return state, []


def _no_loop(player: Player, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return no_loop(player, state, config)


def _forward_to_main_player(verb: str, state: BridgeState, config: BridgeConfig,
                    _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """The main player owns the main slot in video mode; in genau mode the paused main player
    still navigates in the background, and its SEEK commands apply to a live
    local clock, so rapid nudges stack naturally."""
    append_command(config.main_player_cmd_file, verb)
    return state, []


def _forward_to_main_player_on_screen(verb: str, state: BridgeState, config: BridgeConfig,
                              _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """Loop recording, versions and length only make sense while the main player owns the
    main slot — video mode, not genau."""
    if main_player_displays(state.main_mode):
        append_command(config.main_player_cmd_file, verb)
    return state, []


def _forward_to_the_vr_main_player(verb: str, state: BridgeState, config: BridgeConfig,
                                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if config.vr_main_player:
        append_command(config.main_player_cmd_file, verb)
    return state, []


def _main_lock(verb: str, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if main_player_displays(state.main_mode):
        append_command(config.main_player_cmd_file, verb)
        return state, []
    append_command(config.genau_cmd_file, verb)
    if hosting_origenerator(state, config) and _locks_genau(verb, config):
        return state, [WindowOp(op="follow_genaus_lock")]
    return state, []


def _locks_genau(verb: str, config: BridgeConfig) -> bool:
    return verb == LOCK_ON or (
        verb == TOGGLE_LOCK and not read_genau_status(config.genau_status_file).locked)


def _forward_to_genau(verb: str, state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    append_command(config.genau_cmd_file, verb)
    return state, []


_BROKER_BY_HOLD = {OSR2_PARKED: PARK_CMD, OSR2_RETRACTED: RETRACT_CMD}


def _robot_hand_hold(control: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """park / retract: the broker takes the device to that end and keeps it there,
    while the arbiter has both engines play on with nothing of them heard."""
    if config.broker_cmd_file is not None:
        write_broker_command(config.broker_cmd_file, _BROKER_BY_HOLD[control])
    return replace(state, osr2_control=control), []


def _robot_hand_release(state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """unpark / unretract / OSR2 resume: call the broker's hold off, and the
    arbiter hands the device back to whoever should have it."""
    if config.broker_cmd_file is not None:
        write_broker_command(config.broker_cmd_file, RESUME_CMD)
    return replace(state, osr2_control=OSR2_DRIVING), []  # also the way off "off"


def _osr2_control_off(state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    # No dials written down: letting go turns none, so none to put back.
    if config.broker_cmd_file is not None:
        write_broker_command(config.broker_cmd_file, PARK_CMD)
    return replace(state, osr2_control=OSR2_CONTROL_OFF), []


def _set_muted(muted: bool, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_audio(replace(state, muted=muted), config)


def _volume_step(step: int, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_audio(_step_volume(state, step), config)


def _omnipause_toggle(state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_omnipause_toggle(state, config)


def _enter_omnipause(state: BridgeState, config: BridgeConfig,
                     _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_enter_omnipause(state, config)


def _relief_omnipause(state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_enter_omnipause(state, config, relief=True)


def _fmode(players: tuple[str, ...], target: bool | None, state: BridgeState,
           config: BridgeConfig, _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_fmode(players, target, state, config)


def _reorder(player: Player, recent: bool, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if player is Player.MAIN:
        return _dispatch_main_reorder(recent, state, config)
    return _dispatch_reorder(player, recent, state, config)


def _reset(players: tuple[Player, ...], state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_reset(players, state, config)


def _main_projection(plays_vr: bool, plays_flat: bool, state: BridgeState,
                     config: BridgeConfig,
                     _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_main_projection(plays_vr, plays_flat, state, config)


def _main_reset(state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_main_reset(state, config)


def _set_filter(players: tuple[Player, ...], query: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_set_filter(players, query, state, config)


def _mode_switch(target: str, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_mode_switch(target, state, config, [])


def _satellites_switch(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_satellites_switch(command, state, config, [])


def _speed(main_player_cmd: str | None, genau_cmd: str | None, by_driver: bool,
           state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """Send a speed command to the engine it drives (see :func:`_speed_target`)."""
    target = _speed_target(state, config, by_driver=by_driver)
    if target == "main_player" and main_player_cmd is not None:
        append_command(config.main_player_cmd_file, main_player_cmd)
    elif target == "genau" and genau_cmd is not None:
        append_command(config.genau_cmd_file, genau_cmd)
    return state, []


def _flip_genaus_clip_on_screen(state: BridgeState, config: BridgeConfig,
                                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if main_player_displays(state.main_mode):
        return state, []
    return _forward_to_genau("FLIP_ENDS", state, config, _target_path)


def _save_clip(state: BridgeState, _config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """Ask the loop for a clipper save — asked for, not run: clipper boots a
    sibling repo's interpreter (up to its 10 s timeout) and this runs on the
    20 Hz tick, so the loop saves on a worker thread and flashes the result
    when it lands — the one notice that trails its keypress."""
    if state.main_mode == MAIN_GENAU_MODE:
        return state, []
    return state, [WindowOp(op="save_clip")]


def _filter_the_shows_enhanced(state: BridgeState, config: BridgeConfig,
                               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if not hosting_origenerator(state, config):
        return state, []
    append_command(config.origenerator_cmd_file, "FILTER_ENHANCED")
    return state, []


def _words_for_a_show_that_is_not_up(state: BridgeState, _config: BridgeConfig,
                                     _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """The hosted app's phrases arrive in video mode too (its vocabulary is
    always in the grammar); there they reach nothing, a known dead end."""
    return state, []


def _build_handlers() -> dict[str, Handler]:
    """Every exact command id and its handler — the map dispatch_command reads."""
    handlers: dict[str, Handler] = {}
    handlers.update({cmd: partial(_transport, player, verb)
                     for cmd, (player, verb) in _TRANSPORT_COMMANDS.items()})
    handlers.update({cmd: partial(_satellite_speed, player, verb)
                     for cmd, (player, verb) in _SATELLITE_SPEEDS.items()})
    handlers["portrait_lock"] = partial(_toggle_lock, Player.PORTRAIT)
    handlers["landscape_lock"] = partial(_toggle_lock, Player.LANDSCAPE)
    handlers["portrait_trash"] = partial(_discard, Player.PORTRAIT)
    handlers["landscape_trash"] = partial(_discard, Player.LANDSCAPE)
    handlers.update({cmd: partial(cycle_variant, player, kind)
                     for cmd, (player, kind) in _CYCLE_COMMANDS.items()})
    handlers.update({cmd: partial(cycle_version, player, delta)
                     for cmd, (player, delta) in _VERSION_COMMANDS.items()})
    handlers.update({cmd: partial(more_seeds, player)
                     for cmd, player in _MORE_SEEDS_SIDES.items()})
    handlers.update({cmd: partial(wrong_action, player)
                     for cmd, player in _WRONG_ACTION_SIDES.items()})
    handlers.update({cmd: partial(group_loop, player, axis)
                     for cmd, (player, axis) in _LOOP_COMMANDS.items()})
    handlers.update({cmd: partial(loop_cycle, player)
                     for cmd, player in _LOOP_CYCLE_SIDES.items()})
    handlers.update({cmd: partial(_no_loop, player)
                     for cmd, player in _NO_LOOP_SIDES.items()})
    handlers.update({cmd: partial(_dispatch_lock_action, player)
                     for cmd, player in _LOCK_ACTION_SIDES.items()})
    handlers["main_prev"] = partial(_forward_to_main_player, PREV)
    handlers["main_next"] = partial(_forward_to_main_player, NEXT)
    handlers["main_nudge_prev"] = partial(_forward_to_main_player, "SEEK_BACK")
    handlers["main_nudge_next"] = partial(_forward_to_main_player, "SEEK_FWD")
    handlers.update({cmd: partial(_main_lock, verb)
                     for cmd, verb in _MAIN_LOCK_COMMANDS.items()})
    handlers["projection_cycle"] = partial(_forward_to_the_vr_main_player, "CYCLE_PROJECTION")
    handlers["recenter_view"] = partial(_forward_to_the_vr_main_player, "RECENTER")
    handlers["tilt_up"] = partial(_forward_to_the_vr_main_player, "TILT_UP")
    handlers["tilt_down"] = partial(_forward_to_the_vr_main_player, "TILT_DOWN")
    handlers["tilt_reset"] = partial(_forward_to_the_vr_main_player, "TILT_RESET")
    handlers["main_scene_prev"] = partial(_forward_to_the_vr_main_player, "PREV_SCENE")
    handlers["main_scene_next"] = partial(_forward_to_the_vr_main_player, "NEXT_SCENE")
    handlers["vr_reset"] = partial(_forward_to_the_vr_main_player, "LAYOUT_RESET")
    handlers.update({cmd: partial(_forward_to_main_player_on_screen, verb)
                     for cmd, verb in _MAIN_PLAYER_CMD_MAP.items()})
    handlers.update({cmd: partial(_set_muted, muted)
                     for cmd, muted in _MUTE_COMMANDS.items()})
    handlers.update({cmd: partial(_volume_step, step)
                     for cmd, step in _VOLUME_STEPS.items()})
    handlers["quarter_button"] = partial(_forward_to_genau, "OFFSET_QUARTER_CYCLE")
    handlers["omnipause_toggle"] = _omnipause_toggle
    handlers["enter_omnipause"] = _enter_omnipause
    handlers["relief_omnipause"] = _relief_omnipause
    handlers.update({cmd: partial(_fmode, players, target)
                     for cmd, (players, target) in _FMODE_COMMANDS.items()})
    handlers.update({cmd: partial(_reorder, player, recent)
                     for cmd, (player, recent) in _REORDER_COMMANDS.items()})
    handlers.update({cmd: partial(_reset, players)
                     for cmd, players in _RESET_SIDES.items()})
    handlers.update({cmd: partial(_main_projection, plays_vr, plays_flat)
                     for cmd, (plays_vr, plays_flat) in _PROJECTION_COMMANDS.items()})
    handlers[MAIN_RESET] = _main_reset
    handlers.update({cmd: partial(_set_filter, players, "")
                     for cmd, players in _NO_FILTER_SIDES.items()})
    handlers.update({cmd: partial(_mode_switch, target)
                     for cmd, target in _MODE_SWITCH_COMMANDS.items()})
    handlers.update({cmd: partial(_satellites_switch, cmd)
                     for cmd in ("origenerator_activate", "satellites_video_activate", "satellites_toggle")})
    handlers["video_activate"] = _video_activate
    handlers.update({cmd: partial(_speed, verb, verb, True)
                     for cmd, verb in _SPEED_BY_DRIVER.items()})
    handlers.update({f"main_player_{act}": partial(_speed, verb, _GENAU_RATE_ENDS.get(act), False)
                     for act, verb in _PLAYBACK_RATE_ACTS.items()})
    handlers.update({cmd: partial(_forward_to_genau, verb)
                     for cmd, verb in _GENAU_CMD_MAP.items()})
    handlers.update({OSR2_CONTROL_BUTTONS[control]: partial(_robot_hand_hold, control)
                     for control in _BROKER_BY_HOLD})
    handlers[OSR2_CONTROL_BUTTONS[OSR2_DRIVING]] = _robot_hand_release
    handlers[OSR2_CONTROL_BUTTONS[OSR2_CONTROL_OFF]] = _osr2_control_off
    handlers["clipper_save"] = _save_clip
    handlers["genau_flip_ends"] = _flip_genaus_clip_on_screen
    handlers["genau_filter_enhanced"] = _filter_the_shows_enhanced
    handlers.update({cmd: _words_for_a_show_that_is_not_up
                     for cmd in _ORIGENERATOR_SPEECH if cmd not in handlers})
    return handlers


_HANDLERS: dict[str, Handler] = _build_handlers()


# --- the parsed forms --------------------------------------------------------
# Commands that carry an argument in their spelling; each parser answers None
# for a command that is not its form.

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
        Player.PORTRAIT if head.startswith("portrait_") else Player.LANDSCAPE,
        path, state, config)


def _parsed_lock_video(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """Double-click of a HUD thumbnail: "<side>_lock_video|<path>" — switch and lock."""
    if "_lock_video|" not in command:
        return None
    head, _, path = command.partition("|")
    return _dispatch_lock_video(
        Player.PORTRAIT if head.startswith("portrait_") else Player.LANDSCAPE,
        path, state, config)


def _parsed_set_volume(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if not command.startswith(f"{SET_VOLUME_COMMAND}|"):
        return None
    return _dispatch_set_volume(command.partition("|")[2], state, config)


def _parsed_filter(command: str, state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    filter_target = decode_filter_command(command)
    if filter_target is None:
        return None
    scope, query = filter_target
    return _dispatch_set_filter(Player.for_scope(scope), query, state, config)


def _parsed_main_player_speed(command: str, state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """"main_player_speed_<pct>" — an absolute video rate, the main player's alone."""
    main_player_cmd = _percent_rate(command, "main_player_speed_")
    if main_player_cmd is None:
        return None
    return _speed(main_player_cmd, None, False, state, config, _target_path)


def _parsed_satellite_speed(command: str, state: BridgeState, config: BridgeConfig,
                            _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    for player in Player.SATELLITES:
        verb = _percent_rate(command, f"{player.label}_speed_")
        if verb is not None:
            send_satellite(config, player, verb)
            return state, []
    return None


def _parsed_numeric(command: str, state: BridgeState, config: BridgeConfig,
                          _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    genau_cmd = _parse_numeric_command(command)
    if genau_cmd is None:
        return None
    return _forward_to_genau(genau_cmd, state, config, _target_path)


def _parsed_unresolved_active(command: str, state: BridgeState, _config: BridgeConfig,
                              _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """An ``active_*`` command the loop could not resolve — a satellite-only
    action ("weird", a cycle) spoken while the main player holds the floor: a
    designed dead end, dropped quietly rather than logged as a missing handler."""
    if not command.startswith("active_"):
        return None
    return state, []


_PARSED_FORMS = (
    _parsed_nav,
    _parsed_play_video,
    _parsed_lock_video,
    _parsed_set_volume,
    _parsed_filter,
    _parsed_main_player_speed,
    _parsed_satellite_speed,
    _parsed_numeric,
    _parsed_unresolved_active,
)

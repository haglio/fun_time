"""Python-side command dispatcher for the Windows bridge: one dispatch
command in, an updated state and a list of window ops out.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from app_support import osr2_device, osr2_hold
from funestra.console import (
    HOLD_VERB,
    OSR2_CONTROL_BUTTONS,
    OSR2_DRIVING,
    OSR2_PARKED,
    OSR2_RETRACTED,
)
from funestra.file_channel import append_command
from funestra.funestra_verbs import (
    LOCK_OFF,
    LOCK_ON,
    NEXT,
    PREV,
    SEEK_BACK,
    SEEK_FWD,
    SET_SPEED,
    SPEED_DOWN,
    SPEED_UP,
    TOGGLE_LOCK,
    TRASH,
)
from funestra.hud_placement import HudCorner, HudEdge
from funestra.hud_status import F_MODE_LABEL, LATEST_LABEL, SHUFFLE_LABEL
from funestra.modes import LengthMode, LoopState, SatellitesMode
from funestra.robot_hand import FULL_INTENSITY

from main_funestra.genau_verbs import genau_f_mode

from .audio_volume import MAX_VOLUME, MIN_VOLUME, VOLUME_STEP, publish_audio_level
from .bridge_records import BridgeConfig, Op, WindowOp
from .broker_control import write_broker_command
from .content import no_other_flicks
from .crown import CROWNS, Crown
from .dashboard_actions import ORIGENERATOR_ACTIVATE, RFB_ACTIVATE
from .event_log import (
    FAVORITE,
    NOTICE,
    SOURCE_LANDSCAPE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
)
from .filter_vocab import decode_filter_command, set_command
from .funestra_buttons import MAIN_FUNESTRA_NOUN, SATELLITE_NOUN
from .funestra_handover import show_f_mode
from .funestra_status import (
    MainFunestraStatus,
    read_genau_status,
    read_main_funestra_status,
)
from .lock import MARKED_WEIRD, build_discard_plan
from .main_list import MainListSettings, new_shuffle_seed, next_length_mode
from .max_intensity import (
    MAX_INTENSITY_COMMAND,
    funestras_that_can_drive_the_osr2,
    publish_max_intensity,
)
from .media_actions import ensure_in_favs, move_to_weird, remove_from_favs
from .media_metadata import forget_indexed_clip
from .mode_plan import MAIN_GENAU_MODE, MAIN_KINO_MODE, kino_shows
from .modes import VideoShapes, is_favorite_path, read_favs_content
from .omnipause import build_omnipause_plan
from .origenerator_switches import (
    BOTH_SATELLITES_MODE_COMMANDS,
    KINO_MODE,
    ORIGENERATOR_MODE,
    SATELLITE_MODE_COMMANDS,
    RfbSlot,
    origenerator_shows,
)
from .osr2_section import TAKE_OSR2_COMMANDS, funestra_with_the_osr2
from .rfb_slot import origenerator_refused, switch_the_rfb_slot
from .room_funestras import RoomFunestra
from .runtime_flow import (
    FMODE_FUNESTRAS,
    SatelliteFilterFlowResult,
    SatelliteFmodeInputs,
    apply_enter_omnipause,
    apply_fmode,
    apply_leave_omnipause,
    apply_mode_switch,
    apply_satellite_filter,
    apply_satellite_switch,
    rebuild_main_list,
)
from .satellite_control import SatelliteStatus, read_satellite_status
from .satellite_groups import (
    cancel_lock,
    clear_side_grouping,
    cycle_variant,
    cycle_version,
    group_loop,
    is_single_video_loop,
    lock_a_loop_left_with_one_clip,
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
from .ten_second_steps import why_it_cannot_step
from .voice_commands import FAVORITES_PHRASE, said_to_a_side, say_command
from .vr_videos import shapes_verb

logger = logging.getLogger(__name__)


_GENAU_CMD_MAP = {
    "robot_hand_amplitude_down": "AMPLITUDE_DOWN",
    "robot_hand_amplitude_up": "AMPLITUDE_UP",
    "robot_hand_center_down": "CENTER_DOWN",
    "robot_hand_center_up": "CENTER_UP",
    "robot_hand_cycle_shape": "CYCLE_SHAPE",
    "robot_hand_cycle_shape_prev": "CYCLE_SHAPE_PREV",
    "robot_hand_toggle_learned": "TOGGLE_LEARNED",
    "robot_hand_learned_on": "LEARNED_ON",
    "robot_hand_learned_off": "LEARNED_OFF",
    # How long an unlocked Genau leaves each flick on screen, a second at a
    # time; the padlock (_MAIN_LOCK_COMMANDS) is the switch, this is its pace.
    "genau_flick_seconds_down": "FLICK_SECONDS_DOWN",
    "genau_flick_seconds_up": "FLICK_SECONDS_UP",
    "genau_prev_flick": PREV,
    "genau_next_flick": NEXT,
    "genau_flip_ends": "FLIP_ENDS",
    # The motion's rate as the console's own ± marks beside the wave send it —
    # Genau's alone; the unqualified pair is _SPEED_BY_DRIVER below.
    "robot_hand_speed_down": "SPEED_DOWN",
    "robot_hand_speed_up": "SPEED_UP",
}

_GENAUS_FLICK_SECONDS = "genau_flick_seconds_"
_GENAUS_FLICK_COMMANDS = frozenset({
    "genau_prev_flick", "genau_next_flick", "genau_weird_flick", "genau_lock", "genau_flip_ends",
})


def _about_genaus_flick(command: str) -> bool:
    return command in _GENAUS_FLICK_COMMANDS or command.startswith(_GENAUS_FLICK_SECONDS)


# Speed control splits by which control said it: the console's ± marks move the
# engine they sit next to (_GENAU_CMD_MAP, _PLAYBACK_RATE_ACTS), while this bare
# pair — spoken, or J/L — carries no label and follows whichever engine holds
# the OSR2 (see :func:`_speed_target` for the whole routing).
_SPEED_BY_DRIVER = {
    "speed_down": SPEED_DOWN,
    "speed_up": SPEED_UP,
}
_PLAYHEAD_ACTS = {
    "nudge_prev": SEEK_BACK,
    "nudge_next": SEEK_FWD,
}
# A video's own playback rate, as opposed to the motion's, said of one Funestra.
_PLAYBACK_RATE_ACTS = {
    "speed_down": SPEED_DOWN,
    "speed_up": SPEED_UP,
    "speed_min": f"{SET_SPEED} min",
    "speed_max": f"{SET_SPEED} max",
}
# Where Genau is what shows, either end of the Main Funestra's range reaches its motion.
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


def _speed_target(state: BridgeState, config: BridgeConfig, *,
                  by_driver: bool) -> RoomFunestra | None:
    """Which engine a speed command drives: a Funestra, or None for the Robot Hand.

    An engine-named command goes where its name says — in kino mode the video's
    rate to the Main Funestra, the one on screen; elsewhere the hand.  The
    unqualified nudge follows the OSR2: the funscript of the Funestra that has it
    while that script is driving, in any mode, else the Robot Hand, which is
    paused for the whole of a scripted stretch.  A device nobody is driving, held
    at either end or let go, has no motion to speed up, so the nudge reaches the
    video then.
    """
    holder = funestra_with_the_osr2(state)
    driving = by_driver and state.osr2_control == OSR2_DRIVING
    if driving and holder is not RoomFunestra.MAIN:
        status = read_satellite_status(config.satellite(holder).status_file)
        return holder if status.funscript_driving else None
    if not kino_shows(state.main_mode):
        return None
    if not driving:
        return RoomFunestra.MAIN
    if (holder is RoomFunestra.MAIN
            and read_main_funestra_status(config.main_funestra_status_file).funscript_driving):
        return RoomFunestra.MAIN
    return None


_MAIN_FUNESTRA_CMD_MAP = {
    "main_player_record_down": "RECORD_DOWN",
    "main_player_record_up": "RECORD_UP",
    "main_player_record_tap": "RECORD_TAP",
    "main_player_loop_cancel": "LOOP_CANCEL",
    "main_player_cycle_version": "CYCLE_VERSION",
    "main_player_cycle_version_back": "CYCLE_VERSION_BACK",
    "main_player_compilation": "PLAY_COMPILATION",
    "main_player_end_compilation": "END_COMPILATION",
    "main_player_full_vid": "PLAY_FULL_VID",
    "main_player_clip_jump": "PLAY_CLIP_JUMP",
    # Funscript navigation: past this video's quiet stretch, or on to the next
    # video in the playlist that has a script at all (landing on its action).
    # Only the Main Funestra can answer either — it holds the playlist's funscript column and
    # the parsed script of what is playing.
    "main_player_funscript_jump": "JUMP_TO_FUNSCRIPT",
    "main_player_next_funscripted": "NEXT_FUNSCRIPTED",
}

_VERBS_THE_MAIN_FUNESTRA_ANSWERS = frozenset({
    "PLAY_COMPILATION", "PLAY_FULL_VID", "PLAY_CLIP_JUMP", "JUMP_TO_FUNSCRIPT", "NEXT_FUNSCRIPTED",
})


_NUMERIC_PREFIXES = {
    "robot_hand_amp_": "AMP",
    "robot_hand_center_": "CENTER",
    "robot_hand_speed_": "SPEED",
    # Seconds a clip holds the screen, unlike the 0-100 axes above.
    _GENAUS_FLICK_SECONDS: "FLICK_SECONDS",
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


def _lock_as_said(
    funestra: RoomFunestra, locked: bool, state: BridgeState, config: BridgeConfig,
    target_path: str = "",
) -> tuple[BridgeState, list[WindowOp]]:
    if state.satellite(funestra).locked == locked:
        return state, []
    return _toggle_lock(funestra, state, config, target_path)


def _toggle_lock(
    funestra: RoomFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    if not state.satellite(funestra).locked:
        return lock_satellite(funestra, state, config, target_path)
    send_satellite(config, funestra, LOCK_OFF)
    send_satellite(config, funestra, NEXT)
    logger.info("Unlocked %s satellite", RoomFunestra(funestra).label)
    return state.with_satellite(funestra, locked=False), []


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
    # Whether this is a demotion or a condemnation is read from the same favs
    # file that lights the HUD's ★ for this clip, so the key does what the badge
    # on screen implies: a starred clip loses the star, an unstarred one goes.
    is_favorite = is_favorite_path(condemned, read_favs_content(config.favs_file))
    plan = build_discard_plan(funestra=funestra, current_path=condemned, is_favorite=is_favorite)
    if locked:
        # A locked satellite is repeat-one; drop the lock so TRASH advances into
        # the playlist instead of looping the clip that replaced the discarded one.
        send_satellite(config, funestra, LOCK_OFF)
    remove_from_favs(config.favs_file, condemned)
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

_LOCK_STATE_COMMANDS: dict[str, tuple[RoomFunestra, bool]] = {
    f"{funestra.label}_lock_{'on' if locked else 'off'}": (funestra, locked)
    for funestra in RoomFunestra.SATELLITES
    for locked in (True, False)
}

# "reset" clears a satellite's filter and reshuffles it back to the default
# browse; "both reset" expands to these two before dispatch.
_RESET_SIDES: dict[str, tuple[RoomFunestra, ...]] = {
    "portrait_reset": (RoomFunestra.PORTRAIT,),
    "landscape_reset": (RoomFunestra.LANDSCAPE,),
}

MAIN_RESET = "main_reset"

# "no loop" ends a group loop but, unlike reset, keeps the satellite's filter.
_NO_LOOP_SIDES: dict[str, RoomFunestra] = {
    "portrait_no_loop": RoomFunestra.PORTRAIT,
    "landscape_no_loop": RoomFunestra.LANDSCAPE,
}

# The key command that steps a side's loop cycle (seed, action, off — see
# :func:`satellite_groups.loop_cycle`), against the explicit <side>_seed_loop /
# <side>_action_loop / <side>_no_loop the voice commands reach.
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

# A Funestra's own minimize button (``fun_time.satellite_buttons``, and the Main
# Funestra's on its console, ``fun_time.console_buttons``), by the window role the
# dispatch loop resolves it to.  Every Funestra's window here is borderless, so
# none of them carries a minimize button of its own, and the only other way to
# park one was the dashboard's minimize — which takes the whole room down
# together.
_MINIMIZE_ROLES: dict[str, str] = {
    "portrait_minimize": "portrait",
    "landscape_minimize": "landscape",
    "main_minimize": "main_player",
}


_HUD_MOVES = {f"{funestra.label}_hud_{direction}": (funestra, direction)
              for funestra in RoomFunestra.SATELLITES
              for direction in ("left", "right", "up", "down")}
MAIN_HUD_TURNS = {"main_hud_clockwise": True, "main_hud_counterclockwise": False}


_HUD_COLLAPSES = {f"{funestra.label}_hud_{verb}": (funestra, verb == "minimize")
                  for funestra in (*RoomFunestra.SATELLITES, RoomFunestra.MAIN)
                  for verb in ("minimize", "restore")}

_HUD_RESTORES_AT = {f"{funestra.label}_hud_restore_at": funestra
                    for funestra in (*RoomFunestra.SATELLITES, RoomFunestra.MAIN)}


def _restored_at(command: str, state: BridgeState,
                 config: BridgeConfig) -> BridgeState | None:
    head, _, place = command.partition("|")
    funestra = _HUD_RESTORES_AT.get(head)
    if funestra is None:
        return None
    if config.vr_main_funestra:
        return _hung_open_against(funestra, place, state)
    return _opened_in(funestra, place, state, config)


def _hung_open_against(funestra: RoomFunestra, side: str, state: BridgeState) -> BridgeState:
    if side not in HudEdge:
        return state
    if funestra is RoomFunestra.MAIN:
        return replace(state, main_hud_edge=HudEdge(side), main_hud_minimized=False)
    return state.with_satellite(funestra, hud_edge=HudEdge(side), hud_minimized=False)


def _opened_in(funestra: RoomFunestra, corner: str, state: BridgeState,
               config: BridgeConfig) -> BridgeState:
    if corner not in HudCorner:
        return state
    if funestra is RoomFunestra.MAIN:
        return replace(state, main_hud_corner=HudCorner(corner), main_hud_minimized=False)
    _tell_the_hosted_app(funestra, f"hud_corner|{corner}", state, config)
    _tell_the_hosted_app(funestra, "hud_minimized|0", state, config)
    return state.with_satellite(funestra, hud_corner=HudCorner(corner), hud_minimized=False)


def _collapsed_hud(command: str, state: BridgeState,
                   config: BridgeConfig) -> BridgeState | None:
    collapse = _HUD_COLLAPSES.get(command)
    if collapse is None:
        return None
    funestra, minimized = collapse
    if funestra is RoomFunestra.MAIN:
        return replace(state, main_hud_minimized=minimized)
    _tell_the_hosted_app(funestra, f"hud_minimized|{1 if minimized else 0}", state, config)
    return state.with_satellite(funestra, hud_minimized=minimized)


def _moved_hud(command: str, state: BridgeState,
               config: BridgeConfig) -> BridgeState | None:
    moved = _HUD_MOVES.get(command)
    if moved is not None:
        funestra, direction = moved
        return _satellite_hud_moved(funestra, state, config, direction=direction)
    if command in MAIN_HUD_TURNS:
        clockwise = MAIN_HUD_TURNS[command]
        if config.vr_main_funestra:
            return replace(state, main_hud_edge=state.main_hud_edge.turned(
                clockwise=clockwise))
        return replace(state, main_hud_corner=state.main_hud_corner.turned(
            clockwise=clockwise))
    return None


def _satellite_hud_moved(funestra: RoomFunestra, state: BridgeState, config: BridgeConfig, *,
                         direction: str) -> BridgeState:
    side = state.satellite(funestra)
    if config.vr_main_funestra:
        return state.with_satellite(funestra, hud_edge=side.hud_edge.toward(direction))
    corner = side.hud_corner.toward(direction)
    _tell_the_hosted_app(funestra, f"hud_corner|{corner}", state, config)
    return state.with_satellite(funestra, hud_corner=corner)


def _tell_the_hosted_app(funestra: RoomFunestra, action: str, state: BridgeState,
                         config: BridgeConfig) -> None:
    if hosts(funestra, state, config):
        append_command(config.origenerator_cmd_file, f"{funestra.label}_{action}")


def _minimize_ops(command: str) -> list[WindowOp] | None:
    """The window *command* asks to have parked, or None when it asks for none."""
    role = _MINIMIZE_ROLES.get(command)
    return None if role is None else [WindowOp(op="minimize_role", key=role)]

# The two browse orderings, per Funestra: Latest reloads newest-first, Shuffle
# reshuffles.  The Main Funestra is 1 and reloads through the Main Funestra rather than through
# a satellite rebuild — the table only says which Funestra and which order.
_REORDER_COMMANDS: dict[str, tuple[RoomFunestra, bool]] = {
    "main_latest": (RoomFunestra.MAIN, True),
    "portrait_latest": (RoomFunestra.PORTRAIT, True),
    "landscape_latest": (RoomFunestra.LANDSCAPE, True),
    "main_shuffle": (RoomFunestra.MAIN, False),
    "portrait_shuffle": (RoomFunestra.PORTRAIT, False),
    "landscape_shuffle": (RoomFunestra.LANDSCAPE, False),
}

# Which shapes of video the Main Funestra's browse may reach.  One verb per state
# rather than a toggle each: the console's pair of buttons has four states
# between them, and a press names the state it asks for.
_PROJECTION_COMMANDS: dict[str, tuple[bool, bool]] = {
    "main_projection_both": (True, True),
    "main_projection_vr": (True, False),
    "main_projection_flat": (False, True),
    "main_projection_none": (False, False),
}

_LENGTH_COMMANDS: dict[str, LengthMode] = {
    "main_player_length_clips": LengthMode.CLIPS,
    "main_player_length_full": LengthMode.FULL,
    "main_player_length_mixed": LengthMode.MIXED,
    "main_player_length_none": LengthMode.NONE,
}

_PROJECTION_LABELS: dict[tuple[bool, bool], str] = {
    (True, True): "2D + VR",
    (True, False): "VR only",
    (False, True): "2D only",
}


# The same two orders as Genau answers to, keyed by ``recent``.  Every other
# Funestra is handed a rewritten playlist file; Genau owns its own sequence and
# rescans its clips folder for itself, so its order crosses as a verb.
_GENAU_ORDER_CMD: dict[bool, str] = {True: "LATEST", False: "SHUFFLE"}


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


def _is_hud_nav_command(command: str) -> bool:
    """Whether *command* drives HUD keyboard navigation — a direction step.  Those
    manage the side's nav anchor themselves, so the generic "any other side
    command re-homes the map" rule leaves them alone."""
    return _parse_nav(command) is not None


# The main slot's lock: repeat what is on screen, or let it move on — the Main Funestra's
# video into the next playlist entry, Genau's flick into the next flick after its
# interval.  Both Funestras answer these three verbs and both open locked (the
# routing is :func:`_main_lock`'s).  The toggle is the key and the button; the
# absolute pair is what the spoken forms send, since a speaker asks for the
# state they want.
_MAIN_LOCK_COMMANDS = {
    "main_lock": TOGGLE_LOCK,
    "main_lock_on": LOCK_ON,
    "main_lock_off": LOCK_OFF,
}

_NORMAL_SPEED = f"{SET_SPEED} 1"

_MAIN_FUNESTRA_RESET_VERBS = (
    _MAIN_FUNESTRA_CMD_MAP["main_player_loop_cancel"],
    _MAIN_LOCK_COMMANDS["main_lock_off"],
    _NORMAL_SPEED,
)

_NAMED_BY = {"portrait_": RoomFunestra.PORTRAIT, "landscape_": RoomFunestra.LANDSCAPE,
             "main_": RoomFunestra.MAIN, "genau_": RoomFunestra.MAIN}


def command_funestra(command: str) -> RoomFunestra | None:
    """The Funestra a command addresses, or None if it addresses no Funestra."""
    return next((funestra for prefix, funestra in _NAMED_BY.items()
                 if command.startswith(prefix)), None)


def _addressed(funestra: RoomFunestra, command: str, state: BridgeState) -> BridgeState:
    state = replace(state, active_funestra=funestra)
    if funestra is RoomFunestra.MAIN or _is_hud_nav_command(command):
        return state
    return state.with_satellite(funestra, nav_anchor="")


def notice_source(command: str, active_funestra: int | None) -> str:
    funestra = active_funestra if command.startswith("active_") else command_funestra(command)
    return _FUNESTRA_NOTICE_SOURCE.get(funestra, SOURCE_SYSTEM)


def _answered_ahead_of_the_handlers(
    command: str, state: BridgeState, config: BridgeConfig,
) -> tuple[BridgeState, list[WindowOp]] | None:
    if _about_genaus_flick(command) and kino_shows(state.main_mode):
        return _ignored(state, _IN_KINO_MODE)
    minimize_ops = _minimize_ops(command)
    if minimize_ops is not None:
        return state, minimize_ops
    placed = (_moved_hud(command, state, config) or _collapsed_hud(command, state, config)
              or _restored_at(command, state, config))
    return None if placed is None else (placed, [])


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
    funestra = command_funestra(command)
    if funestra is not None:
        state = _addressed(funestra, command, state)
    answered = _answered_ahead_of_the_handlers(command, state, config)
    if answered is not None:
        return answered

    # Said to two sides in different modes, each side's half goes where its
    # own mode sends it.
    if halves := _said_to_sides_in_two_modes(command, state, config):
        ops: list[WindowOp] = []
        for half in halves:
            state, more = dispatch_command(half, state, config, target_path=target_path)
            ops.extend(more)
        return state, ops

    # In its Origenerator mode what is said to a side is the hosted app's to
    # answer -- ahead of the handler lookup, whose Funestra handlers it shadows.
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
# for where every other audio command asks for a step or a state.  The Main Funestra's volume
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
    """Publish *state*'s sound level to both of the Main Funestra's audio sinks,
    and say on screen what it now is."""
    publish_audio_level(
        main_funestra_cmd_file=config.main_funestra_cmd_file,
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
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_enter_omnipause(
        omni_paused=state.omni_paused,
        main_mode=state.main_mode,
        portrait_paused_file=config.portrait_paused_file,
        landscape_paused_file=config.landscape_paused_file,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        main_funestra_paused_file=config.main_funestra_paused_file,
        broker_cmd_file=config.broker_cmd_file,
        origenerator_paused_file=config.origenerator_paused_file,
        device_on=osr2_device.switched_on(config.broker_state),
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
        main_funestra_paused_file=config.main_funestra_paused_file,
        broker_cmd_file=config.broker_cmd_file,
        origenerator_paused_file=config.origenerator_paused_file,
        osr2_control=state.osr2_control,
        osr2_funestra=funestra_with_the_osr2(state),
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    # Un-minimize first, then re-band, then focus: leaving OmniPause is the room
    # coming back, so a Funestra put away with its own minimize button comes back
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
    """Re-activate the main slot's one window (omnipause leave): Kino and Genau
    both run on the Main Funestra's window, whichever of them has it."""
    return [WindowOp(op="activate_role", key="main_player")]


def _main_slot_ops() -> list[WindowOp]:
    """The main slot on a mode switch: Kino and Genau share the Main Funestra's
    window, which the switch has already told which of them to show, so what is
    left is to bring that window back if it was parked and hand it the focus,
    so focus never falls through to another application."""
    return [
        WindowOp(op="show_role", key="main_player"),
        WindowOp(op="activate_role", key="main_player"),
    ]


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

_FUNESTRA_NOTICE_SOURCE = {
    RoomFunestra.MAIN: SOURCE_MAIN,
    RoomFunestra.PORTRAIT: SOURCE_PORTRAIT,
    RoomFunestra.LANDSCAPE: SOURCE_LANDSCAPE,
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
        main_on_screen=_on_the_main_funestra(config),
        favs_file=config.favs_file,
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
    source = _FUNESTRA_NOTICE_SOURCE[funestras[0]] if len(funestras) == 1 else SOURCE_SYSTEM
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
    """Reload the Main Funestra in a fresh order — Latest (newest-first) or Shuffle.

    To whichever Funestra owns the main slot's screen, the split the lock makes (see
    ``_MAIN_LOCK_COMMANDS``) and for the same reason: a browse order is about what
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
    on_main_funestra = kino_shows(state.main_mode)
    if on_main_funestra:
        state = replace(state, main_latest=recent,
                        main_shuffle_seed=state.main_shuffle_seed if recent else new_shuffle_seed())
        _rebuild_the_main_list(state, config, start_at_top=True)
    else:
        state = replace(state, genau_latest=recent)
        append_command(config.genau_cmd_file, _GENAU_ORDER_CMD[recent])
    # The order's own word alone, and self-reported — see _dispatch_reorder.
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: the Main Funestra (%s)", label, "main_player" if on_main_funestra else "genau")
    return state, [WindowOp(op="notice", key=label, source=SOURCE_MAIN)]


def _rebuild_the_main_list(state: BridgeState, config: BridgeConfig, *,
                           start_at_top: bool = False) -> None:
    rebuild_main_list(config.main_list, MainListSettings.of(state),
                      main_funestra_cmd_file=config.main_funestra_cmd_file,
                      on_screen=_on_the_main_funestra(config),
                      start_at_top=start_at_top, builds=config.main_list_builds)


def _on_the_main_funestra(config: BridgeConfig) -> str:
    return read_main_funestra_status(config.main_funestra_status_file).video


def main_video_shapes(state: BridgeState, config: BridgeConfig) -> VideoShapes:
    return config.main_list.shapes(MainListSettings.of(state))


def genau_flick_shapes(state: BridgeState, config: BridgeConfig) -> VideoShapes:
    return VideoShapes(vr_dirs=config.genau_vr_flicks,
                       plays_vr=state.genau_plays_vr,
                       plays_flat=state.genau_plays_flat)


def _dispatch_main_projection(
    plays_vr: bool, plays_flat: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Narrow what the main slot's Funestra browses to a shape of video, or widen it back.

    Asking for the state already running changes nothing: a reorder is what
    reshuffles, and this must not become a second way to do it.  Ignored where
    that Funestra's library holds one shape.
    """
    on_main_funestra = kino_shows(state.main_mode)
    shapes = (main_video_shapes if on_main_funestra else genau_flick_shapes)(state, config)
    if not shapes.offered:
        logger.info("Nothing but flat %s in this session; shape filter ignored",
                    "videos" if on_main_funestra else "flicks")
        return state, []
    if (shapes.plays_vr, shapes.plays_flat) == (plays_vr, plays_flat):
        return state, []
    if on_main_funestra:
        state, ops = _narrow_the_main_funestra(plays_vr, plays_flat, state, config)
    else:
        state, ops = _narrow_genau(plays_vr, plays_flat, state, config)
    label = _PROJECTION_LABELS.get((plays_vr, plays_flat))
    logger.info("%s shapes: %s", "Main Funestra" if on_main_funestra else "Genau", label or "neither")
    if label:
        return state, [*ops, WindowOp(op="notice", key=label, source=SOURCE_MAIN, level=NOTICE)]
    if on_main_funestra:
        return state, ops
    return state, [*ops, WindowOp(
        op="notice", key=no_other_flicks(), source=SOURCE_MAIN, level=logging.WARNING)]


def _narrow_the_main_funestra(
    plays_vr: bool, plays_flat: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    state = replace(state, main_plays_vr=plays_vr, main_plays_flat=plays_flat)
    _rebuild_the_main_list(state, config)
    return state, []


def _narrow_genau(
    plays_vr: bool, plays_flat: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    state = replace(state, genau_plays_vr=plays_vr, genau_plays_flat=plays_flat)
    if not (plays_vr or plays_flat):
        return _lock_genau(LOCK_ON, state, config)
    append_command(config.genau_cmd_file, shapes_verb(plays_vr=plays_vr, plays_flat=plays_flat))
    return state, []


def _dispatch_main_length(
    length: LengthMode, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    if not kino_shows(state.main_mode):
        return _ignored(state, _IN_GENAU_MODE)
    if (length is state.main_length_mode
            and not read_main_funestra_status(config.main_funestra_status_file).compilation):
        return state, []
    state = replace(state, main_length_mode=length)
    _rebuild_the_main_list(state, config)
    logger.info("Main Funestra length: %s", length)
    return state, []


def _the_main_list_is_narrowed_or_ordered(state: BridgeState, config: BridgeConfig) -> bool:
    return (state.main_scripted_filter or state.main_latest
            or main_video_shapes(state, config).narrows
            or state.main_length_mode is not LengthMode.MIXED)


def main_funestra_at_defaults(
    state: BridgeState, config: BridgeConfig, status: MainFunestraStatus
) -> bool:
    if _the_main_list_is_narrowed_or_ordered(state, config):
        return False
    if not kino_shows(state.main_mode):
        return True
    return (not status.locked and status.loop_state is LoopState.NORMAL and status.speed == 1.0
            and not status.compilation)


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


def room_at_defaults(state: BridgeState, config: BridgeConfig, status: MainFunestraStatus) -> bool:
    return (main_funestra_at_defaults(state, config, status)
            and not hosts_a_satellite(state, config)
            and all(satellite_at_defaults(
                        state.satellite(funestra),
                        read_satellite_status(config.satellite(funestra).status_file))
                    for funestra in RoomFunestra.SATELLITES))


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
                funestra, latest=False, filter="", favorites_filter=False, nav_anchor="")
            state = clear_side_grouping(state, funestra)
            result = _rebuild_satellite(funestra, "", state, config, start_at_top=True)
            logger.info("Reset %s: %s", satellite_source(funestra), result.log_message)
        send_satellite(config, funestra, _NORMAL_SPEED)
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
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        cmd_file=config.satellite(funestra).cmd_file,
        start_at_top=start_at_top,
        regen_metadata_root=config.regen_metadata_root,
    )


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


# In its Origenerator mode a Satellite Funestra is the hosted app's: it hands the
# Funestra what to play and answers every press on the buttons it drew.  So what
# is said to that side goes there as it was said, and the session keeps only what
# is about the Funestra itself -- parking its window, its crown, its rate, taking
# the OSR2, and its mode.
_THE_FUNESTRAS_OWN = ("_minimize", "_crown", "_speed_", "_take_osr2", "_kino_activate",
                      "_origenerator_activate")


def _side_said_to(command: str) -> RoomFunestra | None:
    head = command.removeprefix("filter_")
    return next((funestra for funestra in RoomFunestra.SATELLITES
                 if head.startswith(f"{funestra.label}_")), None)


def _about_a_satellite(command: str) -> bool:
    """Whether *command* is said to one satellite side, about what it plays."""
    if _side_said_to(command) is None:
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


def said_to_the_hosted_app(side: str, words: str) -> str:
    return f"{side.upper()}_SAY:{words}"


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


def hosts(funestra: RoomFunestra | None, state: BridgeState, config: BridgeConfig) -> bool:
    """Whether the hosted app holds *funestra*: a Satellite Funestra in its
    Origenerator mode, in a session hosting the app."""
    return (funestra in RoomFunestra.SATELLITES and config.hosts_origenerator
            and origenerator_shows(state.satellite(funestra).mode))


def hosted_satellites(state: BridgeState, config: BridgeConfig) -> frozenset[str]:
    return frozenset(funestra.label for funestra in RoomFunestra.SATELLITES
                     if hosts(funestra, state, config))


def hosts_a_satellite(state: BridgeState, config: BridgeConfig) -> bool:
    return config.hosts_origenerator and state.origenerator_holds_a_satellite()


def origenerator_holds_the_rfb_slot(state: BridgeState, config: BridgeConfig) -> bool:
    return config.origenerator_enabled and state.rfb_slot is RfbSlot.ORIGENERATOR


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
    spoken = _words_for_the_hosted_app(command)
    if spoken is not None:
        append_command(config.origenerator_cmd_file, said_to_the_hosted_app(*spoken))
        return []
    for said in _said_to_each_side(command):
        append_command(config.origenerator_cmd_file, said)
    return []


def _switch_satellite(funestra: RoomFunestra, target: SatellitesMode, state: BridgeState,
                      config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_satellite_switch(
        funestra=funestra,
        current_mode=state.satellite(funestra).mode,
        target_mode=target,
        origenerator_cmd_file=config.origenerator_cmd_file,
        channel=config.satellite(funestra),
    )
    logger.info(result.log_message)
    state = state.with_satellite(funestra, mode=result.next_mode)
    if result.is_transition and not origenerator_shows(result.next_mode):
        return state, [WindowOp(op="take_back_funestras", key=funestra.label)]
    return state, []


def _dispatch_mode_switch(
    target_mode: str, state: BridgeState, config: BridgeConfig, ops: list[WindowOp]
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_mode_switch(
        current_mode=state.main_mode,
        target_mode=target_mode,
        omni_paused=state.omni_paused,
        genau_cmd_file=config.genau_cmd_file,
        main_funestra_paused_file=config.main_funestra_paused_file,
        main_funestra_cmd_file=config.main_funestra_cmd_file,
    )
    state = replace(state, main_mode=result.next_mode)
    if result.is_transition:
        ops.extend(_main_slot_ops())
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _kino_activate(state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """"kino mode", said of no side: the main slot's video AND the satellites'
    Funestras, each through its own switch — a session hosting no Origenerator
    has only the main slot's to make."""
    state, ops = _dispatch_mode_switch(MAIN_KINO_MODE, state, config, [])
    if config.origenerator_enabled:
        state, more = _both_satellites_switch(KINO_MODE, state, config, _target_path)
        ops.extend(more)
    return state, ops


def _crown(crown: Crown, state: BridgeState, _config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return replace(state, crowned=crown), []


# --- the handler map ---------------------------------------------------------
# One uniform shape — handler(state, config, target_path) -> (state, ops) —
# assembled from the tables above; ``_target_path`` marks the video-less ones.

Handler = Callable[[BridgeState, BridgeConfig, str], tuple[BridgeState, list[WindowOp]]]

# Plain playlist navigation, per side.
_TRANSPORT_COMMANDS: dict[str, tuple[RoomFunestra, str]] = {
    "portrait_prev": (RoomFunestra.PORTRAIT, PREV),
    "portrait_next": (RoomFunestra.PORTRAIT, NEXT),
    "landscape_prev": (RoomFunestra.LANDSCAPE, PREV),
    "landscape_next": (RoomFunestra.LANDSCAPE, NEXT),
}

# The main slot's two mode switches, by their target mode.
_MODE_SWITCH_COMMANDS: dict[str, str] = {
    "genau_activate": MAIN_GENAU_MODE,
    "main_kino_activate": MAIN_KINO_MODE,
}


def _transport(funestra: RoomFunestra, verb: str, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state = cancel_lock(funestra, state, config)
    if verb == NEXT and is_single_video_loop(funestra, state, config):
        state, _loop_off = no_loop(funestra, state, config)
    send_satellite(config, funestra, verb)
    return state, []


_SATELLITE_SPEEDS: dict[str, tuple[RoomFunestra, str]] = {
    f"{funestra.label}_{act}": (funestra, verb)
    for funestra in RoomFunestra.SATELLITES
    for act, verb in _PLAYBACK_RATE_ACTS.items()
}


def _satellite_speed(funestra: RoomFunestra, verb: str, state: BridgeState, config: BridgeConfig,
                     _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    send_satellite(config, funestra, verb)
    return state, []


_PLAYHEAD_COMMANDS: dict[str, tuple[RoomFunestra, str]] = {
    f"{funestra.label}_{act}": (funestra, verb)
    for funestra in RoomFunestra
    for act, verb in _PLAYHEAD_ACTS.items()
}


def _step_through(funestra: RoomFunestra, verb: str, state: BridgeState, config: BridgeConfig,
                  _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if funestra is RoomFunestra.MAIN:
        status = read_main_funestra_status(config.main_funestra_status_file)
        noun = MAIN_FUNESTRA_NOUN
    else:
        status = read_satellite_status(config.satellite(funestra).status_file)
        noun = SATELLITE_NOUN
    cannot = why_it_cannot_step(verb, status, noun)
    if cannot is not None:
        return state, [WindowOp(op="notice", key=cannot.capitalize(),
                                source=_FUNESTRA_NOTICE_SOURCE[funestra], level=logging.WARNING)]
    if funestra is RoomFunestra.MAIN:
        append_command(config.main_funestra_cmd_file, verb)
    else:
        send_satellite(config, funestra, verb)
    return state, []


def _no_loop(funestra: RoomFunestra, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return no_loop(funestra, state, config)


def _forward_to_main_funestra(verb: str, state: BridgeState, config: BridgeConfig,
                    _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """The Main Funestra owns the main slot in kino mode; in genau mode the paused Main Funestra
    still navigates in the background, and its SEEK commands apply to a live
    local clock, so rapid nudges stack naturally."""
    append_command(config.main_funestra_cmd_file, verb)
    return state, []


def _forward_to_main_funestra_on_screen(verb: str, state: BridgeState, config: BridgeConfig,
                              _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """Loop recording, versions and length only make sense while the Main Funestra owns the
    main slot — kino mode, not genau."""
    if not kino_shows(state.main_mode):
        return _ignored(state, _IN_GENAU_MODE)
    append_command(config.main_funestra_cmd_file, verb)
    if verb in _VERBS_THE_MAIN_FUNESTRA_ANSWERS and not config.vr_main_funestra:
        return state, [WindowOp(op="main_funestra_answers")]
    return state, []


def _forward_to_the_vr_main_funestra(verb: str, state: BridgeState, config: BridgeConfig,
                                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if not config.vr_main_funestra:
        return _ignored(state, _OUTSIDE_VR)
    append_command(config.main_funestra_cmd_file, verb)
    return state, []


def _main_lock(verb: str, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if kino_shows(state.main_mode):
        append_command(config.main_funestra_cmd_file, verb)
        return state, []
    if _locks_genau(verb, config):
        ensure_in_favs(config.favs_file, read_genau_status(config.genau_status_file).flick)
    return _lock_genau(verb, state, config)


def _lock_genau(verb: str, state: BridgeState,
                config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
    append_command(config.genau_cmd_file, verb)
    if origenerator_holds_the_rfb_slot(state, config) and _locks_genau(verb, config):
        return state, [WindowOp(op="follow_genaus_lock")]
    return state, []


def _locks_genau(verb: str, config: BridgeConfig) -> bool:
    return verb == LOCK_ON or (
        verb == TOGGLE_LOCK and not read_genau_status(config.genau_status_file).locked)


def _forward_to_genau(verb: str, state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    append_command(config.genau_cmd_file, verb)
    return state, []


def _robot_hand_hold(control: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """park / retract: the broker takes the device to that end and keeps it there,
    while the arbiter has both engines play on with nothing of them heard."""
    if config.broker_cmd_file is not None:
        write_broker_command(config.broker_cmd_file, HOLD_VERB[control])
    return replace(state, osr2_control=control), []


def _robot_hand_release(state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """unpark / OSR2 resume / the switch on: call the broker's hold off, and the
    arbiter hands the device back to whoever should have it."""
    if config.broker_cmd_file is not None:
        write_broker_command(config.broker_cmd_file, osr2_hold.RESUME)
    return replace(state, osr2_control=OSR2_DRIVING), []


def _take_the_osr2(funestra: RoomFunestra, state: BridgeState, _config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return replace(state, osr2_funestra=funestra), []


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
                      target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state, ops = _dispatch_enter_omnipause(state, config)
    state, _ = _robot_hand_hold(OSR2_RETRACTED, state, config, target_path)
    return state, ops


def _fmode(funestras: tuple[str, ...], target: bool | None, state: BridgeState,
           config: BridgeConfig, _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_fmode(funestras, target, state, config)


def _reorder(funestra: RoomFunestra, recent: bool, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if funestra is RoomFunestra.MAIN:
        return _dispatch_main_reorder(recent, state, config)
    return _dispatch_reorder(funestra, recent, state, config)


def _reset(funestras: tuple[RoomFunestra, ...], state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_reset(funestras, state, config)


def _main_projection(plays_vr: bool, plays_flat: bool, state: BridgeState,
                     config: BridgeConfig,
                     _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_main_projection(plays_vr, plays_flat, state, config)


def _main_length(length: LengthMode, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_main_length(length, state, config)


def _main_length_toggle(state: BridgeState, config: BridgeConfig,
                        _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_main_length(next_length_mode(state.main_length_mode), state, config)


def _main_reset(state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_main_reset(state, config)


def _set_filter(funestras: tuple[RoomFunestra, ...], query: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_set_filter(funestras, query, state, config)


def _mode_switch(target: str, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_mode_switch(target, state, config, [])


def _satellite_switch(funestra: RoomFunestra, target: SatellitesMode, state: BridgeState,
                      config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    refused = origenerator_refused(state, config, asked_for=origenerator_shows(target))
    if refused is not None:
        return state, refused
    return _switch_satellite(funestra, target, state, config)


def _both_satellites_switch(target: SatellitesMode | None, state: BridgeState,
                            config: BridgeConfig,
                            _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if target is None:
        held = all(hosts(funestra, state, config) for funestra in RoomFunestra.SATELLITES)
        target = KINO_MODE if held else ORIGENERATOR_MODE
    refused = origenerator_refused(state, config, asked_for=origenerator_shows(target))
    if refused is not None:
        return state, refused
    ops: list[WindowOp] = []
    for funestra in RoomFunestra.SATELLITES:
        state, more = _switch_satellite(funestra, target, state, config)
        ops.extend(more)
    return state, ops


def _speed(main_funestra_cmd: str | None, genau_cmd: str | None, by_driver: bool,
           state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """Send a speed command to the engine it drives (see :func:`_speed_target`)."""
    target = _speed_target(state, config, by_driver=by_driver)
    verb = genau_cmd if target is None else main_funestra_cmd
    if verb is None:
        return _ignored(state, _IN_GENAU_MODE)
    if target is None:
        append_command(config.genau_cmd_file, verb)
    elif target is RoomFunestra.MAIN:
        append_command(config.main_funestra_cmd_file, verb)
    else:
        send_satellite(config, target, verb)
    return state, []


def _mark_genaus_flick_weird(state: BridgeState, config: BridgeConfig,
                            _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    remove_from_favs(config.favs_file, read_genau_status(config.genau_status_file).flick)
    append_command(config.genau_cmd_file, "WEIRD")
    return state, [WindowOp(op="notice", key=MARKED_WEIRD, source=SOURCE_MAIN)]


def _save_clip(state: BridgeState, _config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """Ask the loop for a genaumacher save — asked for, not run: genaumacher boots a
    sibling repo's interpreter (up to its 10 s timeout) and this runs on the
    20 Hz tick, so the loop saves on a worker thread and flashes the result
    when it lands — the one notice that trails its keypress."""
    if state.main_mode == MAIN_GENAU_MODE:
        return _ignored(state, _IN_GENAU_MODE)
    return state, [WindowOp(op="save_clip")]


def _parsed_words_for_a_show_that_is_not_up(
        command: str, state: BridgeState, _config: BridgeConfig,
        _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if said_to_a_side(command) is None:
        return None
    return _ignored(state, _IN_KINO_MODE)


_IN_KINO_MODE = "in Kino mode"
_IN_GENAU_MODE = "in Genau mode"
_OUTSIDE_VR = "outside VR"
_ON_THE_MAIN_FUNESTRA = "on the Main Funestra"


def _ignored(state: BridgeState, why: str) -> tuple[BridgeState, list[WindowOp]]:
    return state, [WindowOp(op=Op.IGNORED, key=why)]


def _build_handlers() -> dict[str, Handler]:
    """Every exact command id and its handler — the map dispatch_command reads."""
    handlers: dict[str, Handler] = {}
    handlers.update({cmd: partial(_transport, funestra, verb)
                     for cmd, (funestra, verb) in _TRANSPORT_COMMANDS.items()})
    handlers.update({cmd: partial(_satellite_speed, funestra, verb)
                     for cmd, (funestra, verb) in _SATELLITE_SPEEDS.items()})
    handlers.update({cmd: partial(_step_through, funestra, verb)
                     for cmd, (funestra, verb) in _PLAYHEAD_COMMANDS.items()})
    handlers["portrait_lock"] = partial(_toggle_lock, RoomFunestra.PORTRAIT)
    handlers["landscape_lock"] = partial(_toggle_lock, RoomFunestra.LANDSCAPE)
    handlers.update({cmd: partial(_lock_as_said, funestra, locked)
                     for cmd, (funestra, locked) in _LOCK_STATE_COMMANDS.items()})
    handlers["portrait_trash"] = partial(_discard, RoomFunestra.PORTRAIT)
    handlers["landscape_trash"] = partial(_discard, RoomFunestra.LANDSCAPE)
    handlers.update({cmd: partial(cycle_variant, funestra, kind)
                     for cmd, (funestra, kind) in _CYCLE_COMMANDS.items()})
    handlers.update({cmd: partial(cycle_version, funestra, delta)
                     for cmd, (funestra, delta) in _VERSION_COMMANDS.items()})
    handlers.update({cmd: partial(more_seeds, funestra)
                     for cmd, funestra in _MORE_SEEDS_SIDES.items()})
    handlers.update({cmd: partial(wrong_action, funestra)
                     for cmd, funestra in _WRONG_ACTION_SIDES.items()})
    handlers.update({cmd: partial(group_loop, funestra, axis)
                     for cmd, (funestra, axis) in _LOOP_COMMANDS.items()})
    handlers.update({cmd: partial(loop_cycle, funestra)
                     for cmd, funestra in _LOOP_CYCLE_SIDES.items()})
    handlers.update({cmd: partial(_no_loop, funestra)
                     for cmd, funestra in _NO_LOOP_SIDES.items()})
    handlers.update({cmd: partial(_dispatch_lock_action, funestra)
                     for cmd, funestra in _LOCK_ACTION_SIDES.items()})
    handlers["main_prev"] = partial(_forward_to_main_funestra, PREV)
    handlers["main_next"] = partial(_forward_to_main_funestra, NEXT)
    handlers.update({cmd: partial(_main_lock, verb)
                     for cmd, verb in _MAIN_LOCK_COMMANDS.items()})
    handlers["genau_lock"] = partial(_main_lock, TOGGLE_LOCK)
    handlers["main_player_lock"] = partial(_forward_to_main_funestra_on_screen, TOGGLE_LOCK)
    handlers["projection_cycle"] = partial(_forward_to_the_vr_main_funestra, "CYCLE_PROJECTION")
    handlers["projection_cycle_back"] = partial(
        _forward_to_the_vr_main_funestra, "CYCLE_PROJECTION_BACK")
    handlers["projection_wider"] = partial(_forward_to_the_vr_main_funestra, "WIDEN_PROJECTION")
    handlers["projection_narrower"] = partial(_forward_to_the_vr_main_funestra, "NARROW_PROJECTION")
    handlers["recenter_view"] = partial(_forward_to_the_vr_main_funestra, "RECENTER")
    handlers["tilt_up"] = partial(_forward_to_the_vr_main_funestra, "TILT_UP")
    handlers["tilt_down"] = partial(_forward_to_the_vr_main_funestra, "TILT_DOWN")
    handlers["tilt_reset"] = partial(_forward_to_the_vr_main_funestra, "TILT_RESET")
    handlers["main_scene_prev"] = partial(_forward_to_the_vr_main_funestra, "PREV_SCENE")
    handlers["main_scene_next"] = partial(_forward_to_the_vr_main_funestra, "NEXT_SCENE")
    handlers["vr_reset"] = partial(_forward_to_the_vr_main_funestra, "LAYOUT_RESET")
    handlers.update({cmd: partial(_forward_to_main_funestra_on_screen, verb)
                     for cmd, verb in _MAIN_FUNESTRA_CMD_MAP.items()})
    handlers.update({cmd: partial(_set_muted, muted)
                     for cmd, muted in _MUTE_COMMANDS.items()})
    handlers.update({cmd: partial(_volume_step, step)
                     for cmd, step in _VOLUME_STEPS.items()})
    handlers["omnipause_toggle"] = _omnipause_toggle
    handlers["enter_omnipause"] = _enter_omnipause
    handlers["relief_omnipause"] = _relief_omnipause
    handlers.update({cmd: partial(_fmode, funestras, target)
                     for cmd, (funestras, target) in _FMODE_COMMANDS.items()})
    handlers.update({cmd: partial(_reorder, funestra, recent)
                     for cmd, (funestra, recent) in _REORDER_COMMANDS.items()})
    handlers.update({cmd: partial(_reset, funestras)
                     for cmd, funestras in _RESET_SIDES.items()})
    handlers.update({cmd: partial(_main_projection, plays_vr, plays_flat)
                     for cmd, (plays_vr, plays_flat) in _PROJECTION_COMMANDS.items()})
    handlers.update({cmd: partial(_main_length, length)
                     for cmd, length in _LENGTH_COMMANDS.items()})
    handlers["main_player_toggle_length"] = _main_length_toggle
    handlers[MAIN_RESET] = _main_reset
    handlers.update({cmd: partial(_set_filter, funestras, "")
                     for cmd, funestras in _NO_FILTER_SIDES.items()})
    handlers.update({cmd: partial(_mode_switch, target)
                     for cmd, target in _MODE_SWITCH_COMMANDS.items()})
    handlers.update({cmd: partial(_satellite_switch, funestra, mode)
                     for cmd, (funestra, mode) in SATELLITE_MODE_COMMANDS.items()})
    handlers.update({cmd: partial(_both_satellites_switch, mode)
                     for cmd, mode in BOTH_SATELLITES_MODE_COMMANDS.items()})
    handlers[RFB_ACTIVATE] = partial(switch_the_rfb_slot, RfbSlot.RFB)
    handlers[ORIGENERATOR_ACTIVATE] = partial(switch_the_rfb_slot, RfbSlot.ORIGENERATOR)
    handlers["kino_activate"] = _kino_activate
    handlers.update({command: partial(_crown, crown) for command, crown in CROWNS.items()})
    handlers.update({cmd: partial(_speed, verb, verb, True)
                     for cmd, verb in _SPEED_BY_DRIVER.items()})
    handlers.update({f"main_player_{act}": partial(_speed, verb, _GENAU_RATE_ENDS.get(act), False)
                     for act, verb in _PLAYBACK_RATE_ACTS.items()})
    handlers.update({cmd: partial(_forward_to_genau, verb)
                     for cmd, verb in _GENAU_CMD_MAP.items()})
    handlers[OSR2_CONTROL_BUTTONS[OSR2_PARKED]] = partial(_robot_hand_hold, OSR2_PARKED)
    handlers[OSR2_CONTROL_BUTTONS[OSR2_DRIVING]] = _robot_hand_release
    handlers.update({cmd: partial(_take_the_osr2, funestra)
                     for cmd, funestra in TAKE_OSR2_COMMANDS.items()})
    handlers["genaumacher_save"] = _save_clip
    handlers["genau_weird_flick"] = _mark_genaus_flick_weird
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


def _parsed_set_volume(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if not command.startswith(f"{SET_VOLUME_COMMAND}|"):
        return None
    return _dispatch_set_volume(command.partition("|")[2], state, config)


def _parsed_max_intensity(command: str, state: BridgeState, config: BridgeConfig,
                        _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if not command.startswith(MAX_INTENSITY_COMMAND):
        return None
    try:
        asked = int(command[len(MAX_INTENSITY_COMMAND):])
    except ValueError:
        return state, []
    max_intensity = max(0, min(FULL_INTENSITY, asked))
    publish_max_intensity(funestras_that_can_drive_the_osr2(config), max_intensity=max_intensity)
    return (replace(state, max_intensity=max_intensity),
            [WindowOp(op="notice", key=f"Max intensity {max_intensity}%", source=SOURCE_MAIN)])


def _parsed_filter(command: str, state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    filter_target = decode_filter_command(command)
    if filter_target is None:
        return None
    scope, query = filter_target
    return _dispatch_set_filter(RoomFunestra.for_scope(scope), query, state, config)


def _parsed_main_funestra_speed(command: str, state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """"main_player_speed_<pct>" — an absolute video rate, the Main Funestra's alone."""
    main_funestra_cmd = _percent_rate(command, "main_player_speed_")
    if main_funestra_cmd is None:
        return None
    return _speed(main_funestra_cmd, None, False, state, config, _target_path)


def _parsed_satellite_speed(command: str, state: BridgeState, config: BridgeConfig,
                            _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    for funestra in RoomFunestra.SATELLITES:
        verb = _percent_rate(command, f"{funestra.label}_speed_")
        if verb is not None:
            send_satellite(config, funestra, verb)
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
    if not command.startswith("active_"):
        return None
    return _ignored(state, _ON_THE_MAIN_FUNESTRA)


_PARSED_FORMS = (
    _parsed_nav,
    _parsed_play_video,
    _parsed_lock_video,
    _parsed_set_volume,
    _parsed_max_intensity,
    _parsed_filter,
    _parsed_main_funestra_speed,
    _parsed_satellite_speed,
    _parsed_numeric,
    _parsed_unresolved_active,
    _parsed_words_for_a_show_that_is_not_up,
)

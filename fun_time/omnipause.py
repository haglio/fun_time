from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from app_support import osr2_hold
from funestra.console import HOLD_VERB, OSR2_DRIVING, OSR2_RETRACTED

from .bridge_records import BridgeConfig, Handler, WindowOp
from .broker_control import write_broker_command
from .mode_plan import kino_shows
from .osr2_commands import robot_hand_hold
from .runtime_flow import write_flag_file
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OmniPausePlan:
    action: str
    next_omni_paused: bool
    resume_main_funestra_playback: bool
    # Where this leaves the OSR2: parked home on an enter; on a leave, back on
    # the script feed, or still where a park or retract is holding it.
    broker_command: str
    log_message: str


def build_omnipause_plan(action: str, *, omni_paused: bool, main_mode: str,
                        osr2_control: str = OSR2_DRIVING) -> OmniPausePlan:
    """Decide what one omnipause action means.

    ``toggle`` resolves against the current state; ``enter`` and ``leave`` are
    that decision already made.
    """
    if action == "toggle":
        action = "leave" if omni_paused else "enter"

    if action == "enter":
        return OmniPausePlan(
            action=action,
            next_omni_paused=True,
            resume_main_funestra_playback=False,
            broker_command=osr2_hold.PARK,
            log_message="OmniPause: entering",
        )

    if action == "leave":
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            # The Main Funestra owns the display in kino mode, so leaving omnipause
            # resumes its playback there (in genau mode Genau owns the display).
            resume_main_funestra_playback=kino_shows(main_mode),
            broker_command=HOLD_VERB.get(osr2_control, osr2_hold.RESUME),
            log_message="OmniPause: leaving",
        )

    raise ValueError(f"Unsupported omnipause action: {action}")


@dataclass(frozen=True)
class OmniPauseFlowResult:
    next_omni_paused: bool
    log_message: str


def apply_enter_omnipause(
    *,
    omni_paused: bool,
    main_mode: str,
    portrait_paused_file: str | Path,
    landscape_paused_file: str | Path,
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    main_funestra_paused_file: str | Path,
    broker_cmd_file: str | Path | None = None,
    origenerator_paused_file: str | Path | None = None,
) -> OmniPauseFlowResult:
    """Freeze the whole session, and send the OSR2 home."""
    plan = build_omnipause_plan(
        "enter",
        omni_paused=omni_paused,
        main_mode=main_mode,
    )
    write_flag_file(genau_paused_file, True)
    write_flag_file(audio_paused_file, True)
    write_flag_file(main_funestra_paused_file, True)
    # The satellites obey their paused flag file each tick, so freezing playback
    # is a single flag write per side.  A paused native satellite simply cannot
    # auto-advance (its advance() returns early while paused), so OmniPause is a
    # settled state: one write holds it, with nothing to police afterwards.
    write_flag_file(portrait_paused_file, True)
    write_flag_file(landscape_paused_file, True)
    if origenerator_paused_file is not None:
        # The hosted Origenerator obeys the same flag idiom: its shows stop
        # their dwell clocks and playing clips until the room resumes.
        write_flag_file(origenerator_paused_file, True)
    if broker_cmd_file is not None:
        write_broker_command(broker_cmd_file, plan.broker_command)
    return OmniPauseFlowResult(
        next_omni_paused=plan.next_omni_paused,
        log_message=plan.log_message,
    )


def apply_leave_omnipause(
    *,
    omni_paused: bool,
    main_mode: str,
    portrait_paused_file: str | Path,
    landscape_paused_file: str | Path,
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    main_funestra_paused_file: str | Path,
    broker_cmd_file: str | Path | None = None,
    origenerator_paused_file: str | Path | None = None,
    osr2_control: str = OSR2_DRIVING,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "leave",
        omni_paused=omni_paused,
        main_mode=main_mode,
        osr2_control=osr2_control,
    )
    write_flag_file(genau_paused_file, False)
    write_flag_file(audio_paused_file, False)
    if plan.resume_main_funestra_playback:
        write_flag_file(main_funestra_paused_file, False)
    if broker_cmd_file is not None:
        write_broker_command(broker_cmd_file, plan.broker_command)
    # Unfreeze both satellites; a locked one holds its clip (its lock is
    # independent of the pause flag), an unlocked one resumes auto-advancing --
    # whichever of the session and the hosted app is handing it what to play.
    write_flag_file(portrait_paused_file, False)
    write_flag_file(landscape_paused_file, False)
    if origenerator_paused_file is not None:
        write_flag_file(origenerator_paused_file, False)
    return OmniPauseFlowResult(
        next_omni_paused=plan.next_omni_paused,
        log_message=plan.log_message,
    )


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
        main_funestra_paused_file=config.main_funestra_paused_file,
        broker_cmd_file=config.broker_cmd_file,
        origenerator_paused_file=config.origenerator_paused_file,
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
        main_funestra_paused_file=config.main_funestra_paused_file,
        broker_cmd_file=config.broker_cmd_file,
        origenerator_paused_file=config.origenerator_paused_file,
        osr2_control=state.osr2_control,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    # Un-minimize first, then re-band, then focus: leaving OmniPause is the room
    # coming back, so a Funestra put away with its own minimize button comes back
    # with it — it has no HUD left to ask with, its HUD having gone down with the
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
    return [WindowOp(op="activate_role", key="main_funestra")]


def _omnipause_toggle(state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_omnipause_toggle(state, config)


def _enter_omnipause(state: BridgeState, config: BridgeConfig,
                     _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_enter_omnipause(state, config)


def _relief_omnipause(state: BridgeState, config: BridgeConfig,
                      target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state, ops = _dispatch_enter_omnipause(state, config)
    state, _ = robot_hand_hold(OSR2_RETRACTED, state, config, target_path)
    return state, ops


HANDLERS: dict[str, Handler] = {
    "omnipause_toggle": _omnipause_toggle,
    "enter_omnipause": _enter_omnipause,
    "relief_omnipause": _relief_omnipause,
}

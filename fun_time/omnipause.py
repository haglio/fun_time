from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from app_support import osr2_hold
from funestra.main_hud import HOLD_VERB, OSR2_DRIVING, OSR2_RETRACTED

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
    broker_command: str
    log_message: str


def build_omnipause_plan(action: str, *, omni_paused: bool, main_mode: str,
                        osr2_control: str = OSR2_DRIVING) -> OmniPausePlan:
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
            resume_main_funestra_playback=kino_shows(main_mode),
            broker_command=HOLD_VERB.get(osr2_control, osr2_hold.RESUME),
            log_message="OmniPause: leaving",
        )

    raise ValueError(f"Unsupported omnipause action: {action}")


@dataclass(frozen=True)
class OmniPauseFlowResult:
    next_omni_paused: bool
    log_message: str


@dataclass(frozen=True)
class PausedFiles:
    portrait: str | Path
    landscape: str | Path
    genau: str | Path
    audio: str | Path
    main_funestra: str | Path
    origenerator: str | Path | None = None

    @classmethod
    def of(cls, config: BridgeConfig) -> PausedFiles:
        return cls(
            portrait=config.portrait.paused_file,
            landscape=config.landscape.paused_file,
            genau=config.genau_paused_file,
            audio=config.audio_paused_file,
            main_funestra=config.main_funestra_paused_file,
            origenerator=config.origenerator_paused_file,
        )


def apply_enter_omnipause(
    *,
    omni_paused: bool,
    main_mode: str,
    paused: PausedFiles,
    broker_cmd_file: str | Path | None = None,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "enter",
        omni_paused=omni_paused,
        main_mode=main_mode,
    )
    write_flag_file(paused.genau, True)
    write_flag_file(paused.audio, True)
    write_flag_file(paused.main_funestra, True)
    write_flag_file(paused.portrait, True)
    write_flag_file(paused.landscape, True)
    if paused.origenerator is not None:
        write_flag_file(paused.origenerator, True)
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
    paused: PausedFiles,
    broker_cmd_file: str | Path | None = None,
    osr2_control: str = OSR2_DRIVING,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "leave",
        omni_paused=omni_paused,
        main_mode=main_mode,
        osr2_control=osr2_control,
    )
    write_flag_file(paused.genau, False)
    write_flag_file(paused.audio, False)
    if plan.resume_main_funestra_playback:
        write_flag_file(paused.main_funestra, False)
    if broker_cmd_file is not None:
        write_broker_command(broker_cmd_file, plan.broker_command)
    write_flag_file(paused.portrait, False)
    write_flag_file(paused.landscape, False)
    if paused.origenerator is not None:
        write_flag_file(paused.origenerator, False)
    return OmniPauseFlowResult(
        next_omni_paused=plan.next_omni_paused,
        log_message=plan.log_message,
    )


def _dispatch_omnipause_toggle(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
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
        paused=PausedFiles.of(config),
        broker_cmd_file=config.broker_cmd_file,
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
        paused=PausedFiles.of(config),
        broker_cmd_file=config.broker_cmd_file,
        osr2_control=state.osr2_control,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    ops = [WindowOp(op="restore_parked"), WindowOp(op="restore_all_topmost"),
           WindowOp(op="unsuspend_hotkeys")]
    ops.extend(_main_focus_ops())
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _main_focus_ops() -> list[WindowOp]:
    return [WindowOp(op="activate_window", key="main_funestra")]


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

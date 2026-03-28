"""Python-side command dispatcher for the Windows bridge.

Replaces the AHK ProcessDashboardCommand switch/case with a Python
implementation that calls logic modules directly as function calls
instead of subprocess invocations via the plan-file protocol.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from .media_actions import ensure_in_favs, move_to_weird, remove_from_favs
from .lock import build_lock_plan
from .runtime_flow import (
    apply_enter_omnipause,
    apply_leave_omnipause,
    apply_sync_robot_hand,
    apply_toggle_fmode,
    apply_toggle_robot_hand_enabled,
    build_omnipause_toggle,
    read_flag_file,
    write_flag_file,
)
from .vlc_actions import (
    ensure_playback_state,
    get_current_file_path,
    get_current_playlist_id,
    get_playback_time,
    set_repeat_mode,
    vlc_delete_playlist_item,
    vlc_http_cmd,
    vlc_nav_step,
)

logger = logging.getLogger(__name__)


@dataclass
class BridgeState:
    locked2: bool = False
    locked3: bool = False
    robot_hand_mode: bool = False
    f_mode_enabled: bool = False
    omni_paused: bool = False


@dataclass
class BridgeConfig:
    primary_port: int
    portrait_port: int
    landscape_port: int
    vlc_password: str
    favs_file: Path
    weird_dir: Path
    state_dir: Path
    primary_sources: str
    portrait_sources: str
    landscape_sources: str
    robot_hand_enabled_file: Path
    robot_hand_mode_file: Path
    robot_hand_cmd_file: Path
    robot_hand_paused_file: Path
    audio_paused_file: Path
    dashboard_state_file: Path


@dataclass(frozen=True)
class WindowOp:
    op: str
    pid: int = 0
    title: str = ""
    key: str = ""
    value: bool = True
    vk: int = 0


def _effective_robot_hand_mode(config: BridgeConfig) -> bool:
    if not read_flag_file(config.robot_hand_enabled_file, True):
        return False
    return read_flag_file(config.robot_hand_mode_file, False)


def _cancel_lock(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    plan = build_lock_plan("cancel-lock", which=which, locked=locked, current_path="")
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if which == 2:
        return replace(state, locked2=plan.next_locked)
    return replace(state, locked3=plan.next_locked)


def _toggle_lock(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    current_path = get_current_file_path(port, config.vlc_password)
    plan = build_lock_plan("toggle-lock", which=which, locked=locked, current_path=current_path)
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if plan.ensure_in_favs and current_path:
        ensure_in_favs(config.favs_file, current_path)
    if plan.advance_playlist:
        vlc_http_cmd(port, "pl_next", config.vlc_password)
    if plan.log_message:
        logger.info(plan.log_message)
    if which == 2:
        return replace(state, locked2=plan.next_locked)
    return replace(state, locked3=plan.next_locked)


def _discard(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    current_path = get_current_file_path(port, config.vlc_password)
    doomed_id = get_current_playlist_id(port, config.vlc_password)
    plan = build_lock_plan("discard", which=which, locked=locked, current_path=current_path)
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if plan.remove_from_favs and current_path:
        remove_from_favs(config.favs_file, current_path)
    if plan.advance_playlist:
        vlc_http_cmd(port, "pl_next", config.vlc_password)
    if plan.move_to_weird and current_path:
        move_to_weird(config.weird_dir, Path(current_path))
    if doomed_id >= 0:
        vlc_delete_playlist_item(port, config.vlc_password, doomed_id)
    if plan.log_message:
        logger.info(plan.log_message)
    if which == 2:
        return replace(state, locked2=plan.next_locked)
    return replace(state, locked3=plan.next_locked)


def dispatch_command(
    command: str,
    state: BridgeState,
    config: BridgeConfig,
) -> tuple[BridgeState, list[WindowOp]]:
    """Dispatch a dashboard/hotkey command, returning updated state and window operations."""
    ops: list[WindowOp] = []

    if command == "portrait_prev":
        state = _cancel_lock(2, state, config)
        vlc_nav_step(config.portrait_port, config.vlc_password, "prev")
        return state, ops

    if command == "portrait_next":
        state = _cancel_lock(2, state, config)
        vlc_nav_step(config.portrait_port, config.vlc_password, "next")
        return state, ops

    if command == "portrait_lock":
        state = _toggle_lock(2, state, config)
        return state, ops

    if command == "portrait_trash":
        state = _discard(2, state, config)
        return state, ops

    if command == "landscape_prev":
        state = _cancel_lock(3, state, config)
        vlc_nav_step(config.landscape_port, config.vlc_password, "prev")
        return state, ops

    if command == "landscape_next":
        state = _cancel_lock(3, state, config)
        vlc_nav_step(config.landscape_port, config.vlc_password, "next")
        return state, ops

    if command == "landscape_lock":
        state = _toggle_lock(3, state, config)
        return state, ops

    if command == "landscape_trash":
        state = _discard(3, state, config)
        return state, ops

    if command in ("primary_prev", "primary_next"):
        if _effective_robot_hand_mode(config):
            cmd = "PREV" if command == "primary_prev" else "NEXT"
            write_flag_file(config.robot_hand_cmd_file, False)
            config.robot_hand_cmd_file.write_text(cmd, encoding="utf-8")
        else:
            direction = "prev" if command == "primary_prev" else "next"
            logger.info("nav: %s → vlc_nav_step(%s) on port %s", command, direction, config.primary_port)
            ok = vlc_nav_step(config.primary_port, config.vlc_password, direction)
            logger.info("nav: vlc_nav_step returned %s", ok)
        return state, ops

    if command == "quarter_button":
        config.robot_hand_cmd_file.write_text("OFFSET_QUARTER_CYCLE", encoding="utf-8")
        return state, ops

    if command == "omnipause_toggle":
        return _dispatch_omnipause_toggle(state, config)

    if command == "enter_omnipause":
        return _dispatch_enter_omnipause(state, config)

    if command == "leave_omnipause_skip_primary":
        return _dispatch_leave_omnipause_skip_primary(state, config)

    if command == "fmode_toggle":
        return _dispatch_fmode_toggle(state, config)

    if command in ("robot_toggle", "link_toggle"):
        return _dispatch_robot_toggle(state, config, ops)

    if command == "sync_robot_hand":
        return _dispatch_sync_robot_hand(state, config)

    if command == "vlc_nudge_prev":
        ops.append(WindowOp(op="send_vk", vk=0x25))  # VK_LEFT
        return state, ops

    if command == "vlc_nudge_next":
        ops.append(WindowOp(op="send_vk", vk=0x27))  # VK_RIGHT
        return state, ops

    if command == "clipper_save":
        if not _effective_robot_hand_mode(config):
            msg = _dispatch_clipper_save(config)
            if msg:
                ops.append(WindowOp(op="tooltip", key=msg))
        return state, ops

    return state, ops


def _dispatch_omnipause_toggle(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    ops: list[WindowOp] = []
    toggle = build_omnipause_toggle(
        omni_paused=state.omni_paused,
        robot_hand_mode_on=state.robot_hand_mode,
    )
    if toggle.action == "enter":
        result = apply_enter_omnipause(
            omni_paused=state.omni_paused,
            robot_hand_mode_on=state.robot_hand_mode,
            portrait_port=config.portrait_port,
            landscape_port=config.landscape_port,
            primary_port=config.primary_port,
            password=config.vlc_password,
            robot_hand_paused_file=config.robot_hand_paused_file,
            audio_paused_file=config.audio_paused_file,
        )
        state = replace(state, omni_paused=result.next_omni_paused)
        ops.append(WindowOp(op="suspend_hotkeys"))
    else:
        result = apply_leave_omnipause(
            omni_paused=state.omni_paused,
            robot_hand_mode_on=state.robot_hand_mode,
            skip_primary_resume=False,
            primary_port=config.primary_port,
            portrait_port=config.portrait_port,
            landscape_port=config.landscape_port,
            password=config.vlc_password,
            robot_hand_paused_file=config.robot_hand_paused_file,
            audio_paused_file=config.audio_paused_file,
        )
        state = replace(state, omni_paused=result.next_omni_paused)
        ops.append(WindowOp(op="unsuspend_hotkeys"))
        if state.robot_hand_mode:
            ops.append(WindowOp(op="set_topmost", title="Robot Hand", value=True))
            ops.append(WindowOp(op="activate", title="Robot Hand"))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_enter_omnipause(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    ops: list[WindowOp] = []
    result = apply_enter_omnipause(
        omni_paused=state.omni_paused,
        robot_hand_mode_on=state.robot_hand_mode,
        portrait_port=config.portrait_port,
        landscape_port=config.landscape_port,
        primary_port=config.primary_port,
        password=config.vlc_password,
        robot_hand_paused_file=config.robot_hand_paused_file,
        audio_paused_file=config.audio_paused_file,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    ops.append(WindowOp(op="suspend_hotkeys"))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_leave_omnipause_skip_primary(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    ops: list[WindowOp] = []
    result = apply_leave_omnipause(
        omni_paused=state.omni_paused,
        robot_hand_mode_on=state.robot_hand_mode,
        skip_primary_resume=True,
        primary_port=config.primary_port,
        portrait_port=config.portrait_port,
        landscape_port=config.landscape_port,
        password=config.vlc_password,
        robot_hand_paused_file=config.robot_hand_paused_file,
        audio_paused_file=config.audio_paused_file,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    ops.append(WindowOp(op="unsuspend_hotkeys"))
    if state.robot_hand_mode:
        ops.append(WindowOp(op="set_topmost", title="Robot Hand", value=True))
        ops.append(WindowOp(op="activate", title="Robot Hand"))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_fmode_toggle(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_toggle_fmode(
        f_mode_enabled=state.f_mode_enabled,
        primary_sources=config.primary_sources,
        portrait_sources=config.portrait_sources,
        landscape_sources=config.landscape_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        primary_port=config.primary_port,
        portrait_port=config.portrait_port,
        landscape_port=config.landscape_port,
        password=config.vlc_password,
    )
    if result.log_message:
        logger.info(result.log_message)
    return replace(
        state,
        f_mode_enabled=result.next_f_mode_enabled,
        locked2=result.next_locked2,
        locked3=result.next_locked3,
    ), []


def _dispatch_robot_toggle(
    state: BridgeState, config: BridgeConfig, ops: list[WindowOp]
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_toggle_robot_hand_enabled(
        robot_hand_mode_on=state.robot_hand_mode,
        omni_paused=state.omni_paused,
        enabled_file=config.robot_hand_enabled_file,
        mode_state_file=config.robot_hand_mode_file,
        paused_file=config.robot_hand_paused_file,
        audio_paused_file=config.audio_paused_file,
        primary_port=config.primary_port,
        password=config.vlc_password,
    )
    state = replace(state, robot_hand_mode=result.next_robot_hand_mode)
    if result.enforce_outputs:
        if result.enforce_active:
            ops.append(WindowOp(op="show", title="Robot Hand"))
            if result.is_transition:
                ops.append(WindowOp(op="set_topmost", title="Robot Hand", value=True))
                ops.append(WindowOp(op="activate", title="Robot Hand"))
        else:
            if result.is_transition:
                ops.append(WindowOp(op="hide", title="Robot Hand"))
                ops.append(WindowOp(op="set_topmost", title="Robot Hand", value=False))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_sync_robot_hand(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    if state.omni_paused:
        return state, []
    ops: list[WindowOp] = []
    result = apply_sync_robot_hand(
        robot_hand_mode_on=state.robot_hand_mode,
        omni_paused=state.omni_paused,
        enabled_file=config.robot_hand_enabled_file,
        mode_state_file=config.robot_hand_mode_file,
        paused_file=config.robot_hand_paused_file,
        audio_paused_file=config.audio_paused_file,
        primary_port=config.primary_port,
        password=config.vlc_password,
    )
    state = replace(state, robot_hand_mode=result.next_robot_hand_mode)
    if result.enforce_outputs:
        if result.enforce_active:
            if result.is_transition:
                ops.append(WindowOp(op="show", title="Robot Hand"))
                ops.append(WindowOp(op="set_topmost", title="Robot Hand", value=True))
                ops.append(WindowOp(op="activate", title="Robot Hand"))
        else:
            if result.is_transition:
                ops.append(WindowOp(op="hide", title="Robot Hand"))
                ops.append(WindowOp(op="set_topmost", title="Robot Hand", value=False))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


_CLIPPER_PROJECT_DIR = Path(__file__).resolve().parents[1].parent / "clipper"
_CLIPPER_PYTHON = _CLIPPER_PROJECT_DIR / ".venv" / "Scripts" / "python.exe"


def _clipper_python() -> str:
    if _CLIPPER_PYTHON.is_file():
        return str(_CLIPPER_PYTHON)
    return sys.executable


def _dispatch_clipper_save(config: BridgeConfig) -> str:
    """Save a Clipper session for the current Primary VLC video.

    Returns a short user-visible message on success, or empty string on failure.
    """
    video_path = get_current_file_path(config.primary_port, config.vlc_password)
    if not video_path:
        logger.warning("clipper_save: no video playing in primary VLC")
        return ""
    playback_time = get_playback_time(config.primary_port, config.vlc_password)
    if playback_time is None:
        logger.warning("clipper_save: could not get playback time")
        return ""
    try:
        result = subprocess.run(
            [
                _clipper_python(), "-m", "clipper.create_session",
                "--video", video_path,
                "--time", str(playback_time),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(_CLIPPER_PROJECT_DIR),
        )
        if result.returncode == 0:
            session_path = result.stdout.strip()
            logger.info("clipper_save: %s", session_path)
            name = Path(session_path).stem if session_path else "session"
            return f"Clipper: {name}"
        logger.warning("clipper_save failed: %s", result.stderr.strip())
        return ""
    except Exception as exc:
        logger.warning("clipper_save error: %s", exc)
        return ""

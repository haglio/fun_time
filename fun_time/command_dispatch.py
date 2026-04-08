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

from .media_actions import ensure_in_favs, make_web_url_from_path, move_to_weird, remove_from_favs
from .lock import build_lock_plan
from .mode_plan import genau_active
from .runtime_flow import (
    apply_enter_omnipause,
    apply_leave_omnipause,
    apply_mode_switch,
    apply_toggle_fmode,
    build_omnipause_toggle,
    read_flag_file,
    write_flag_file,
)
from .vlc_actions import (
    ensure_playback_state,
    get_current_file_path,
    get_playback_time,
    set_repeat_mode,
    vlc_advance_and_remove,
    vlc_http_cmd,
    vlc_nav_step,
)

logger = logging.getLogger(__name__)


@dataclass
class BridgeState:
    locked2: bool = False
    locked3: bool = False
    primary_mode: str = "vlc"
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
    genau_mode_file: Path
    genau_cmd_file: Path
    genau_paused_file: Path
    audio_paused_file: Path
    dashboard_state_file: Path
    broker_cmd_file: Path | None = None
    broker_heartbeat_file: Path | None = None
    broker_tray_launcher: Path | None = None


@dataclass(frozen=True)
class WindowOp:
    op: str
    pid: int = 0
    title: str = ""
    key: str = ""
    value: bool = True
    vk: int = 0


_GENAU_CMD_MAP = {
    "genau_speed_down": "SPEED_DOWN",
    "genau_speed_up": "SPEED_UP",
    "genau_amplitude_down": "AMPLITUDE_DOWN",
    "genau_amplitude_up": "AMPLITUDE_UP",
    "genau_center_down": "CENTER_DOWN",
    "genau_center_up": "CENTER_UP",
    "genau_cycle_shape": "CYCLE_SHAPE",
    "genau_toggle_auto": "TOGGLE_AUTO",
    "genau_toggle_cruise": "TOGGLE_CRUISE",
    "genau_cruise_on": "CRUISE_ON",
    "genau_cruise_off": "CRUISE_OFF",
    "genau_prev_clip": "PREV",
    "genau_next_clip": "NEXT",
}


_GENAU_NUMERIC_PREFIXES = {
    "genau_amp_": "AMP",
    "genau_center_": "CENTER",
    "genau_speed_": "SPEED",
}


def _parse_genau_numeric_command(command: str) -> str | None:
    """Parse 'genau_amp_50' -> 'AMP 50', etc."""
    for prefix, keyword in _GENAU_NUMERIC_PREFIXES.items():
        if command.startswith(prefix):
            value_str = command[len(prefix):]
            try:
                int(value_str)
            except ValueError:
                return None
            return f"{keyword} {value_str}"
    return None


def _cancel_lock(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    plan = build_lock_plan("cancel-lock", which=which, locked=locked, current_path="")
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if which == 2:
        return replace(state, locked2=plan.next_locked)
    return replace(state, locked3=plan.next_locked)


def _toggle_lock(which: int, state: BridgeState, config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
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
    lock_ops: list[WindowOp] = []
    if plan.open_rfb_tab and current_path:
        url = make_web_url_from_path(current_path)
        if url:
            lock_ops.append(WindowOp(op="open_rfb_tab", key=url))
    if which == 2:
        return replace(state, locked2=plan.next_locked), lock_ops
    return replace(state, locked3=plan.next_locked), lock_ops


def _discard(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    current_path = get_current_file_path(port, config.vlc_password)
    plan = build_lock_plan("discard", which=which, locked=locked, current_path=current_path)
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if plan.remove_from_favs and current_path:
        remove_from_favs(config.favs_file, current_path)
    if plan.advance_playlist:
        vlc_advance_and_remove(port, config.vlc_password)
    if plan.move_to_weird and current_path:
        move_to_weird(config.weird_dir, Path(current_path))
    ensure_playback_state(port, config.vlc_password, should_play=True)
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
        ensure_playback_state(config.portrait_port, config.vlc_password, should_play=True)
        return state, ops

    if command == "portrait_next":
        state = _cancel_lock(2, state, config)
        vlc_nav_step(config.portrait_port, config.vlc_password, "next")
        ensure_playback_state(config.portrait_port, config.vlc_password, should_play=True)
        return state, ops

    if command == "portrait_lock":
        state, lock_ops = _toggle_lock(2, state, config)
        ops.extend(lock_ops)
        return state, ops

    if command == "portrait_trash":
        state = _discard(2, state, config)
        return state, ops

    if command == "landscape_prev":
        state = _cancel_lock(3, state, config)
        vlc_nav_step(config.landscape_port, config.vlc_password, "prev")
        ensure_playback_state(config.landscape_port, config.vlc_password, should_play=True)
        return state, ops

    if command == "landscape_next":
        state = _cancel_lock(3, state, config)
        vlc_nav_step(config.landscape_port, config.vlc_password, "next")
        ensure_playback_state(config.landscape_port, config.vlc_password, should_play=True)
        return state, ops

    if command == "landscape_lock":
        state, lock_ops = _toggle_lock(3, state, config)
        ops.extend(lock_ops)
        return state, ops

    if command == "landscape_trash":
        state = _discard(3, state, config)
        return state, ops

    if command in ("primary_prev", "primary_next"):
        direction = "prev" if command == "primary_prev" else "next"
        vlc_nav_step(config.primary_port, config.vlc_password, direction)
        return state, ops

    if command == "quarter_button":
        config.genau_cmd_file.write_text("OFFSET_QUARTER_CYCLE", encoding="utf-8")
        return state, ops

    if command == "omnipause_toggle":
        return _dispatch_omnipause_toggle(state, config)

    if command == "enter_omnipause":
        return _dispatch_enter_omnipause(state, config)

    if command == "leave_omnipause_skip_primary":
        return _dispatch_leave_omnipause_skip_primary(state, config)

    if command in ("fmode_toggle", "fmode_panel"):
        return _dispatch_fmode_toggle(state, config)

    if command in ("genau_activate", "vlc_activate", "hybrid_activate"):
        target = {"genau_activate": "genau", "vlc_activate": "vlc", "hybrid_activate": "hybrid"}[command]
        return _dispatch_mode_switch(target, state, config, ops)

    if command in _GENAU_CMD_MAP:
        if genau_active(state.primary_mode):
            config.genau_cmd_file.write_text(_GENAU_CMD_MAP[command], encoding="utf-8")
        return state, ops

    genau_numeric = _parse_genau_numeric_command(command)
    if genau_numeric is not None:
        if genau_active(state.primary_mode):
            config.genau_cmd_file.write_text(genau_numeric, encoding="utf-8")
        return state, ops

    if command == "vlc_nudge_prev":
        ops.append(WindowOp(op="vlc_http_seek", key="seek&val=-10"))
        return state, ops

    if command == "vlc_nudge_next":
        ops.append(WindowOp(op="vlc_http_seek", key="seek&val=%2B10"))
        return state, ops

    if command == "clipper_save":
        if state.primary_mode != "genau":
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
        primary_mode=state.primary_mode,
    )
    if toggle.action == "enter":
        result = apply_enter_omnipause(
            omni_paused=state.omni_paused,
            primary_mode=state.primary_mode,
            portrait_port=config.portrait_port,
            landscape_port=config.landscape_port,
            primary_port=config.primary_port,
            password=config.vlc_password,
            genau_paused_file=config.genau_paused_file,
            audio_paused_file=config.audio_paused_file,
            genau_cmd_file=config.genau_cmd_file,
            broker_cmd_file=config.broker_cmd_file,
        )
        state = replace(state, omni_paused=result.next_omni_paused)
        ops.append(WindowOp(op="disable_all_topmost"))
        ops.append(WindowOp(op="suspend_hotkeys"))
    else:
        result = apply_leave_omnipause(
            omni_paused=state.omni_paused,
            primary_mode=state.primary_mode,
            skip_primary_resume=False,
            primary_port=config.primary_port,
            portrait_port=config.portrait_port,
            landscape_port=config.landscape_port,
            password=config.vlc_password,
            genau_paused_file=config.genau_paused_file,
            audio_paused_file=config.audio_paused_file,
            genau_cmd_file=config.genau_cmd_file,
            broker_cmd_file=config.broker_cmd_file,
        )
        state = replace(state, omni_paused=result.next_omni_paused)
        ops.append(WindowOp(op="restore_all_topmost"))
        ops.append(WindowOp(op="unsuspend_hotkeys"))
        if genau_active(state.primary_mode):
            ops.append(WindowOp(op="set_topmost", title="Genau", value=True))
            ops.append(WindowOp(op="activate", title="Genau"))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_enter_omnipause(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    ops: list[WindowOp] = []
    result = apply_enter_omnipause(
        omni_paused=state.omni_paused,
        primary_mode=state.primary_mode,
        portrait_port=config.portrait_port,
        landscape_port=config.landscape_port,
        primary_port=config.primary_port,
        password=config.vlc_password,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        broker_cmd_file=config.broker_cmd_file,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    ops.append(WindowOp(op="disable_all_topmost"))
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
        primary_mode=state.primary_mode,
        skip_primary_resume=True,
        primary_port=config.primary_port,
        portrait_port=config.portrait_port,
        landscape_port=config.landscape_port,
        password=config.vlc_password,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        broker_cmd_file=config.broker_cmd_file,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    ops.append(WindowOp(op="restore_all_topmost"))
    ops.append(WindowOp(op="unsuspend_hotkeys"))
    if genau_active(state.primary_mode):
        ops.append(WindowOp(op="set_topmost", title="Genau", value=True))
        ops.append(WindowOp(op="activate", title="Genau"))
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


def _dispatch_mode_switch(
    target_mode: str, state: BridgeState, config: BridgeConfig, ops: list[WindowOp]
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_mode_switch(
        current_mode=state.primary_mode,
        target_mode=target_mode,
        omni_paused=state.omni_paused,
        paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        primary_port=config.primary_port,
        password=config.vlc_password,
        broker_cmd_file=config.broker_cmd_file,
    )
    state = replace(state, primary_mode=result.next_mode)
    if result.is_transition:
        if genau_active(result.next_mode):
            ops.append(WindowOp(op="set_topmost", title="Genau", value=True))
            ops.append(WindowOp(op="activate", title="Genau"))
        else:
            ops.append(WindowOp(op="set_topmost", title="Genau", value=False))
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

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .controller_modes import build_fmode_playlists
from .controller_omnipause import build_omnipause_plan
from .controller_robot_hand import build_robot_hand_plan
from .controller_vlc_actions import ensure_playback_state, replace_playlist_from_file


def read_flag_file(path: str | Path, default: bool) -> bool:
    try:
        target = Path(path)
        if not target.exists():
            return default
        return target.read_text(encoding="utf-8").strip() not in {"", "0", "false", "False"}
    except OSError:
        return default


def write_flag_file(path: str | Path, value: bool) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("1" if value else "0", encoding="utf-8")


@dataclass(frozen=True)
class RobotHandFlowResult:
    next_robot_hand_mode: bool
    current_enabled: bool
    enforce_outputs: bool
    enforce_active: bool
    is_transition: bool
    log_message: str


@dataclass(frozen=True)
class FModeFlowResult:
    success: bool
    next_f_mode_enabled: bool
    next_locked2: bool
    next_locked3: bool
    log_message: str


def apply_sync_robot_hand(
    *,
    robot_hand_mode_on: bool,
    omni_paused: bool,
    enabled_file: str | Path,
    mode_state_file: str | Path,
    paused_file: str | Path,
    audio_paused_file: str | Path,
    primary_port: int,
    password: str,
) -> RobotHandFlowResult:
    enabled = read_flag_file(enabled_file, True)
    mode_state_on = read_flag_file(mode_state_file, False)
    plan = build_robot_hand_plan(
        "sync-state",
        robot_hand_mode_on=robot_hand_mode_on,
        enabled=enabled,
        mode_state_on=mode_state_on,
        omni_paused=omni_paused,
    )
    if plan.enforce_outputs:
        write_flag_file(paused_file, not plan.enforce_active)
        write_flag_file(audio_paused_file, not plan.enforce_active)
        if not ensure_playback_state(primary_port, password, should_play=not plan.enforce_active):
            raise RuntimeError("Primary VLC failed to reach desired Robot Hand sync playback state")
    return RobotHandFlowResult(
        next_robot_hand_mode=plan.next_robot_hand_mode,
        current_enabled=enabled,
        enforce_outputs=plan.enforce_outputs,
        enforce_active=plan.enforce_active,
        is_transition=plan.is_transition,
        log_message=plan.log_message,
    )


def apply_toggle_robot_hand_enabled(
    *,
    robot_hand_mode_on: bool,
    omni_paused: bool,
    enabled_file: str | Path,
    mode_state_file: str | Path,
    paused_file: str | Path,
    audio_paused_file: str | Path,
    primary_port: int,
    password: str,
) -> RobotHandFlowResult:
    enabled = read_flag_file(enabled_file, True)
    mode_state_on = read_flag_file(mode_state_file, False)
    plan = build_robot_hand_plan(
        "toggle-enabled",
        robot_hand_mode_on=robot_hand_mode_on,
        enabled=enabled,
        mode_state_on=mode_state_on,
        omni_paused=omni_paused,
    )
    if plan.write_enabled:
        write_flag_file(enabled_file, plan.enabled_value)
    if plan.enforce_outputs:
        write_flag_file(paused_file, not plan.enforce_active)
        write_flag_file(audio_paused_file, not plan.enforce_active)
        if not ensure_playback_state(primary_port, password, should_play=not plan.enforce_active):
            raise RuntimeError("Primary VLC failed to reach desired Robot Hand toggle playback state")
    return RobotHandFlowResult(
        next_robot_hand_mode=plan.next_robot_hand_mode,
        current_enabled=plan.enabled_value if plan.write_enabled else enabled,
        enforce_outputs=plan.enforce_outputs,
        enforce_active=plan.enforce_active,
        is_transition=plan.is_transition,
        log_message=plan.log_message,
    )


def apply_toggle_fmode(
    *,
    f_mode_enabled: bool,
    primary_sources: str,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    primary_port: int,
    portrait_port: int,
    landscape_port: int,
    password: str,
) -> FModeFlowResult:
    target_enabled = not f_mode_enabled
    plan = build_fmode_playlists(
        primary_sources=primary_sources,
        portrait_sources=portrait_sources,
        landscape_sources=landscape_sources,
        favs_file=Path(favs_file),
        state_dir=Path(state_dir),
        enabled=target_enabled,
    )
    if not plan.success:
        return FModeFlowResult(
            success=False,
            next_f_mode_enabled=f_mode_enabled,
            next_locked2=False,
            next_locked3=False,
            log_message="F-mode toggle aborted because one or more playlists would be empty",
        )
    if not replace_playlist_from_file(primary_port, password, plan.primary_playlist_path):
        raise RuntimeError("Primary VLC failed to load F-mode playlist")
    if not replace_playlist_from_file(portrait_port, password, plan.portrait_playlist_path, repeat_mode="all"):
        raise RuntimeError("Portrait VLC failed to load F-mode playlist")
    if not replace_playlist_from_file(landscape_port, password, plan.landscape_playlist_path, repeat_mode="all"):
        raise RuntimeError("Landscape VLC failed to load F-mode playlist")
    return FModeFlowResult(
        success=True,
        next_f_mode_enabled=target_enabled,
        next_locked2=False,
        next_locked3=False,
        log_message=f"F-mode hotkey: {'enabled' if target_enabled else 'disabled'}",
    )


@dataclass(frozen=True)
class OmniPauseFlowResult:
    action: str
    next_omni_paused: bool
    robot_hand_branch: bool
    log_message: str


def build_omnipause_toggle(*, omni_paused: bool, robot_hand_mode_on: bool) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "toggle",
        omni_paused=omni_paused,
        robot_hand_mode_on=robot_hand_mode_on,
        skip_primary_resume=False,
    )
    return OmniPauseFlowResult(
        action=plan.action,
        next_omni_paused=plan.next_omni_paused,
        robot_hand_branch=plan.robot_hand_branch,
        log_message=plan.log_message,
    )


def apply_enter_omnipause(
    *,
    omni_paused: bool,
    robot_hand_mode_on: bool,
    portrait_port: int,
    landscape_port: int,
    primary_port: int,
    password: str,
    robot_hand_paused_file: str | Path,
    audio_paused_file: str | Path,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "enter",
        omni_paused=omni_paused,
        robot_hand_mode_on=robot_hand_mode_on,
        skip_primary_resume=False,
    )
    if not ensure_playback_state(portrait_port, password, should_play=False):
        raise RuntimeError("Portrait VLC failed to pause for omnipause")
    if not ensure_playback_state(landscape_port, password, should_play=False):
        raise RuntimeError("Landscape VLC failed to pause for omnipause")
    write_flag_file(robot_hand_paused_file, True)
    write_flag_file(audio_paused_file, True)
    if not ensure_playback_state(primary_port, password, should_play=False):
        raise RuntimeError("Primary VLC failed to pause for omnipause")
    return OmniPauseFlowResult(
        action=plan.action,
        next_omni_paused=plan.next_omni_paused,
        robot_hand_branch=plan.robot_hand_branch,
        log_message=plan.log_message,
    )


def apply_leave_omnipause(
    *,
    omni_paused: bool,
    robot_hand_mode_on: bool,
    skip_primary_resume: bool,
    primary_port: int,
    password: str,
    robot_hand_paused_file: str | Path,
    audio_paused_file: str | Path,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "leave",
        omni_paused=omni_paused,
        robot_hand_mode_on=robot_hand_mode_on,
        skip_primary_resume=skip_primary_resume,
    )
    write_flag_file(robot_hand_paused_file, False)
    write_flag_file(audio_paused_file, False)
    if plan.resume_primary_playback and not ensure_playback_state(primary_port, password, should_play=True):
        raise RuntimeError("Primary VLC failed to resume from omnipause")
    return OmniPauseFlowResult(
        action=plan.action,
        next_omni_paused=plan.next_omni_paused,
        robot_hand_branch=plan.robot_hand_branch,
        log_message=plan.log_message,
    )

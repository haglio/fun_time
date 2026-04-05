from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from .modes import build_fmode_playlists
from .omnipause import build_omnipause_plan
from .genau_plan import build_genau_toggle_plan
from .vlc_actions import ensure_playback_state, replace_playlist_from_file


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
class GenauFlowResult:
    next_genau_mode: bool
    is_transition: bool
    log_message: str


@dataclass(frozen=True)
class FModeFlowResult:
    success: bool
    next_f_mode_enabled: bool
    next_locked2: bool
    next_locked3: bool
    log_message: str


def apply_toggle_genau_active(
    *,
    genau_mode_on: bool,
    omni_paused: bool,
    paused_file: str | Path,
    audio_paused_file: str | Path,
    genau_cmd_file: str | Path,
    primary_port: int,
    password: str,
) -> GenauFlowResult:
    plan = build_genau_toggle_plan(
        genau_mode_on=genau_mode_on,
        omni_paused=omni_paused,
    )
    if plan.is_transition:
        write_flag_file(paused_file, not plan.target_active)
        write_flag_file(audio_paused_file, not plan.target_active)
        Path(genau_cmd_file).write_text(
            "RESUME" if plan.target_active else "PAUSE", encoding="utf-8",
        )
        if not ensure_playback_state(primary_port, password, should_play=not plan.target_active):
            logger.warning("Primary VLC failed to reach desired Genau toggle playback state")
    return GenauFlowResult(
        next_genau_mode=plan.target_active,
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
    if not replace_playlist_from_file(primary_port, password, plan.primary_playlist_path):
        logger.warning("Primary VLC failed to load F-mode playlist")
    if not replace_playlist_from_file(portrait_port, password, plan.portrait_playlist_path, repeat_mode="all"):
        logger.warning("Portrait VLC failed to load F-mode playlist")
    if not replace_playlist_from_file(landscape_port, password, plan.landscape_playlist_path, repeat_mode="all"):
        logger.warning("Landscape VLC failed to load F-mode playlist")
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
    genau_branch: bool
    log_message: str


def build_omnipause_toggle(*, omni_paused: bool, genau_mode_on: bool) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "toggle",
        omni_paused=omni_paused,
        genau_mode_on=genau_mode_on,
        skip_primary_resume=False,
    )
    return OmniPauseFlowResult(
        action=plan.action,
        next_omni_paused=plan.next_omni_paused,
        genau_branch=plan.genau_branch,
        log_message=plan.log_message,
    )


def apply_enter_omnipause(
    *,
    omni_paused: bool,
    genau_mode_on: bool,
    portrait_port: int,
    landscape_port: int,
    primary_port: int,
    password: str,
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    genau_cmd_file: str | Path,
    broker_cmd_file: str | Path | None = None,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "enter",
        omni_paused=omni_paused,
        genau_mode_on=genau_mode_on,
        skip_primary_resume=False,
    )
    write_flag_file(genau_paused_file, True)
    write_flag_file(audio_paused_file, True)
    Path(genau_cmd_file).write_text("PAUSE", encoding="utf-8")
    if broker_cmd_file is not None:
        Path(broker_cmd_file).write_text("PARK", encoding="utf-8")
    vlc_targets = [
        (portrait_port, "Portrait"),
        (landscape_port, "Landscape"),
        (primary_port, "Primary"),
    ]
    with ThreadPoolExecutor(max_workers=len(vlc_targets)) as pool:
        futures = {
            pool.submit(ensure_playback_state, port, password, should_play=False): label
            for port, label in vlc_targets
        }
        for fut in futures:
            if not fut.result():
                logger.warning("%s VLC failed to pause for omnipause", futures[fut])
    return OmniPauseFlowResult(
        action=plan.action,
        next_omni_paused=plan.next_omni_paused,
        genau_branch=plan.genau_branch,
        log_message=plan.log_message,
    )


def apply_leave_omnipause(
    *,
    omni_paused: bool,
    genau_mode_on: bool,
    skip_primary_resume: bool,
    primary_port: int,
    portrait_port: int,
    landscape_port: int,
    password: str,
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    genau_cmd_file: str | Path,
    broker_cmd_file: str | Path | None = None,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "leave",
        omni_paused=omni_paused,
        genau_mode_on=genau_mode_on,
        skip_primary_resume=skip_primary_resume,
    )
    write_flag_file(genau_paused_file, False)
    write_flag_file(audio_paused_file, False)
    Path(genau_cmd_file).write_text("RESUME", encoding="utf-8")
    if broker_cmd_file is not None:
        Path(broker_cmd_file).write_text("RESUME", encoding="utf-8")
    vlc_targets = [
        (portrait_port, "Portrait"),
        (landscape_port, "Landscape"),
    ]
    if plan.resume_primary_playback:
        vlc_targets.append((primary_port, "Primary"))
    with ThreadPoolExecutor(max_workers=len(vlc_targets)) as pool:
        futures = {
            pool.submit(ensure_playback_state, port, password, should_play=True): label
            for port, label in vlc_targets
        }
        for fut in futures:
            if not fut.result():
                logger.warning("%s VLC failed to resume from omnipause", futures[fut])
    return OmniPauseFlowResult(
        action=plan.action,
        next_omni_paused=plan.next_omni_paused,
        genau_branch=plan.genau_branch,
        log_message=plan.log_message,
    )

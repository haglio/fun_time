from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from .modes import SatelliteLibraryContext, build_fmode_playlists, build_satellite_playlists
from .omnipause import build_omnipause_plan
from .mode_plan import build_mode_switch_plan, genau_active
from .vlc_actions import ensure_playback_state, replace_playlist_from_file
from .watch_stats import watch_stats_path

NAU_RELOAD_PLAYLIST_CMD = "RELOAD_PLAYLIST"


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
class ModeSwitchFlowResult:
    next_mode: str
    is_transition: bool
    log_message: str


@dataclass(frozen=True)
class FModeFlowResult:
    success: bool
    next_f_mode_enabled: bool
    next_locked2: bool
    next_locked3: bool
    log_message: str


@dataclass(frozen=True)
class RecencyOrderFlowResult:
    next_recency_order: bool
    next_locked2: bool
    next_locked3: bool
    log_message: str


def apply_mode_switch(
    *,
    current_mode: str,
    target_mode: str,
    omni_paused: bool,
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    genau_cmd_file: str | Path,
    nau_paused_file: str | Path,
    broker_cmd_file: str | Path | None = None,
) -> ModeSwitchFlowResult:
    plan = build_mode_switch_plan(
        current_mode=current_mode,
        target_mode=target_mode,
        omni_paused=omni_paused,
    )
    if plan.is_transition:
        will_genau = genau_active(plan.target_mode)
        write_flag_file(genau_paused_file, not will_genau)
        write_flag_file(audio_paused_file, not will_genau)
        if plan.nau_should_play is not None:
            write_flag_file(nau_paused_file, not plan.nau_should_play)
        cmds = [cmd for cmd in (plan.genau_cmd, plan.hud_cmd) if cmd is not None]
        if cmds:
            Path(genau_cmd_file).write_text("\n".join(cmds), encoding="utf-8")
        if not will_genau and broker_cmd_file is not None:
            Path(broker_cmd_file).write_text("RESUME", encoding="utf-8")
    return ModeSwitchFlowResult(
        next_mode=plan.target_mode,
        is_transition=plan.is_transition,
        log_message=plan.log_message,
    )


def apply_toggle_fmode(
    *,
    f_mode_enabled: bool,
    recent: bool,
    primary_sources: str,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    portrait_port: int,
    landscape_port: int,
    password: str,
    nau_cmd_file: str | Path,
    provider_media_root: Path | None = None,
    provider_metadata_root: Path | None = None,
) -> FModeFlowResult:
    target_enabled = not f_mode_enabled
    plan = build_fmode_playlists(
        primary_sources=primary_sources,
        portrait_sources=portrait_sources,
        landscape_sources=landscape_sources,
        favs_file=Path(favs_file),
        state_dir=Path(state_dir),
        enabled=target_enabled,
        recent=recent,
        library=SatelliteLibraryContext(
            media_root=provider_media_root,
            metadata_root=provider_metadata_root,
            watch_stats_file=watch_stats_path(state_dir),
        ),
    )
    if not replace_playlist_from_file(portrait_port, password, plan.portrait_playlist_path, repeat_mode="all"):
        logger.warning("Portrait VLC failed to load F-mode playlist")
    if not replace_playlist_from_file(landscape_port, password, plan.landscape_playlist_path, repeat_mode="all"):
        logger.warning("Landscape VLC failed to load F-mode playlist")
    Path(nau_cmd_file).write_text(NAU_RELOAD_PLAYLIST_CMD, encoding="utf-8")
    return FModeFlowResult(
        success=True,
        next_f_mode_enabled=target_enabled,
        next_locked2=False,
        next_locked3=False,
        log_message=f"F-mode hotkey: {'enabled' if target_enabled else 'disabled'}",
    )


def apply_refresh_recency_order(
    *,
    f_mode_enabled: bool,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    portrait_port: int,
    landscape_port: int,
    password: str,
    provider_media_root: Path | None = None,
    provider_metadata_root: Path | None = None,
) -> RecencyOrderFlowResult:
    """Refresh the Portrait/Landscape VLC playlists to newest-first (Premiere).

    Rescans the satellite sources (honouring the current F-mode filter),
    rebuilds their playlists newest-first, and reloads them — so a repeat press
    picks up any newly-arrived files and restarts each player from the top
    (``replace_playlist_from_file`` empties then re-plays from item 0).  Action
    groups still collapse to one entry (represented by the group's newest
    member) when the provider roots are supplied.  The primary/Nau player is left
    alone.  Pushing a fresh playlist with repeat-all clears any per-window lock,
    so the caller's lock flags reset to match.
    """
    plan = build_satellite_playlists(
        portrait_sources=portrait_sources,
        landscape_sources=landscape_sources,
        favs_file=Path(favs_file),
        state_dir=Path(state_dir),
        f_mode=f_mode_enabled,
        recent=True,
        library=SatelliteLibraryContext(
            media_root=provider_media_root,
            metadata_root=provider_metadata_root,
            watch_stats_file=watch_stats_path(state_dir),
        ),
    )
    if not replace_playlist_from_file(portrait_port, password, plan.portrait_playlist_path, repeat_mode="all"):
        logger.warning("Portrait VLC failed to load recency-ordered playlist")
    if not replace_playlist_from_file(landscape_port, password, plan.landscape_playlist_path, repeat_mode="all"):
        logger.warning("Landscape VLC failed to load recency-ordered playlist")
    return RecencyOrderFlowResult(
        next_recency_order=True,
        next_locked2=False,
        next_locked3=False,
        log_message="Premiere: Portrait/Landscape reloaded newest-first",
    )


@dataclass(frozen=True)
class OmniPauseFlowResult:
    action: str
    next_omni_paused: bool
    genau_branch: bool
    log_message: str


def build_omnipause_toggle(*, omni_paused: bool, primary_mode: str) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "toggle",
        omni_paused=omni_paused,
        primary_mode=primary_mode,
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
    primary_mode: str,
    portrait_port: int,
    landscape_port: int,
    password: str,
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    genau_cmd_file: str | Path,
    nau_paused_file: str | Path,
    broker_cmd_file: str | Path | None = None,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "enter",
        omni_paused=omni_paused,
        primary_mode=primary_mode,
    )
    write_flag_file(genau_paused_file, True)
    write_flag_file(audio_paused_file, True)
    write_flag_file(nau_paused_file, True)
    Path(genau_cmd_file).write_text("PAUSE", encoding="utf-8")
    if broker_cmd_file is not None:
        Path(broker_cmd_file).write_text("PARK", encoding="utf-8")
    vlc_targets = [
        (portrait_port, "Portrait"),
        (landscape_port, "Landscape"),
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
    primary_mode: str,
    portrait_port: int,
    landscape_port: int,
    password: str,
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    genau_cmd_file: str | Path,
    nau_paused_file: str | Path,
    broker_cmd_file: str | Path | None = None,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "leave",
        omni_paused=omni_paused,
        primary_mode=primary_mode,
    )
    if plan.genau_branch:
        write_flag_file(genau_paused_file, False)
        write_flag_file(audio_paused_file, False)
        Path(genau_cmd_file).write_text("RESUME", encoding="utf-8")
    if plan.resume_nau_playback:
        write_flag_file(nau_paused_file, False)
    if broker_cmd_file is not None:
        Path(broker_cmd_file).write_text("RESUME", encoding="utf-8")
    vlc_targets = [
        (portrait_port, "Portrait"),
        (landscape_port, "Landscape"),
    ]
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

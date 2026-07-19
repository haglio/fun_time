from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from .modes import (
    PLAYLIST_LANDSCAPE,
    PLAYLIST_PORTRAIT,
    SatelliteLibraryContext,
    build_fmode_playlists,
    build_playlist_file_path,
    build_satellite_playlist_paths,
    build_satellite_playlists,
    write_playlist_file,
)
from .broker_control import PARK_CMD, RESUME_CMD, write_broker_command
from .omnipause import build_omnipause_plan
from .mode_plan import build_mode_switch_plan, genau_active
from .satellite_control import write_satellite_command
from .watch_stats import watch_stats_path

# Both Nau and the native satellites re-read their playlist file on this verb.
RELOAD_PLAYLIST_CMD = "RELOAD_PLAYLIST"


def _satellite_library(
    state_dir: str | Path,
    metadata_root: Path | None,
) -> SatelliteLibraryContext:
    """The library context satellite builds need: metadata root + watch stats."""
    return SatelliteLibraryContext(
        metadata_root=metadata_root,
        watch_stats_file=watch_stats_path(state_dir),
    )


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
    nau_cmd_file: str | Path,
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
        cmds = [
            cmd for cmd in (plan.genau_cmd, plan.hud_cmd, plan.display_cmd)
            if cmd is not None
        ]
        if cmds:
            Path(genau_cmd_file).write_text("\n".join(cmds), encoding="utf-8")
        if plan.reenable_nau_tcode:
            Path(nau_cmd_file).write_text("SET_TCODE_ENABLED 1", encoding="utf-8")
        if not will_genau and broker_cmd_file is not None:
            write_broker_command(broker_cmd_file, RESUME_CMD)
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
    portrait_cmd_file: str | Path,
    landscape_cmd_file: str | Path,
    nau_cmd_file: str | Path,
    provider_media_root: Path | None = None,
    provider_metadata_root: Path | None = None,
    portrait_filter: str = "",
    landscape_filter: str = "",
) -> FModeFlowResult:
    target_enabled = not f_mode_enabled
    # Writes each satellite's and Nau's playlist file in place; the players below
    # are told to re-read them.
    build_fmode_playlists(
        primary_sources=primary_sources,
        portrait_sources=portrait_sources,
        landscape_sources=landscape_sources,
        favs_file=Path(favs_file),
        state_dir=Path(state_dir),
        enabled=target_enabled,
        recent=recent,
        portrait_filter=portrait_filter,
        landscape_filter=landscape_filter,
        library=_satellite_library(state_dir, provider_metadata_root),
    )
    write_satellite_command(Path(portrait_cmd_file), RELOAD_PLAYLIST_CMD)
    write_satellite_command(Path(landscape_cmd_file), RELOAD_PLAYLIST_CMD)
    Path(nau_cmd_file).write_text(RELOAD_PLAYLIST_CMD, encoding="utf-8")
    return FModeFlowResult(
        success=True,
        next_f_mode_enabled=target_enabled,
        next_locked2=False,
        next_locked3=False,
        log_message=f"F-mode hotkey: {'enabled' if target_enabled else 'disabled'}",
    )


def apply_reorder_satellites(
    *,
    recent: bool,
    f_mode_enabled: bool,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    portrait_cmd_file: str | Path,
    landscape_cmd_file: str | Path,
    portrait_filter: str = "",
    landscape_filter: str = "",
    provider_media_root: Path | None = None,
    provider_metadata_root: Path | None = None,
) -> RecencyOrderFlowResult:
    """Rebuild and reload the Portrait/Landscape playlists in a fresh order.

    ``recent`` chooses the order: newest-first (Premiere) or reshuffled
    (Shuffle, Premiere's counterpart).  Either rescans the satellite sources —
    honouring the current F-mode and metadata filters — so newly-arrived files
    are picked up, writes each satellite's playlist file, and tells the player to
    re-read it (RELOAD_PLAYLIST keeps the clip on screen playing when it survives
    the reorder, else restarts from the top).  Clips still collapse to one entry
    per group when the provider roots are supplied.  The primary/Nau player is left
    alone.  A rebuild drops any per-window lock, so the caller's lock flags reset.
    """
    build_satellite_playlists(
        portrait_sources=portrait_sources,
        landscape_sources=landscape_sources,
        favs_file=Path(favs_file),
        state_dir=Path(state_dir),
        f_mode=f_mode_enabled,
        recent=recent,
        portrait_filter=portrait_filter,
        landscape_filter=landscape_filter,
        library=_satellite_library(state_dir, provider_metadata_root),
    )
    order = "newest-first" if recent else "reshuffled"
    logger.info("Reordering satellites %s", order)
    write_satellite_command(Path(portrait_cmd_file), RELOAD_PLAYLIST_CMD)
    write_satellite_command(Path(landscape_cmd_file), RELOAD_PLAYLIST_CMD)
    return RecencyOrderFlowResult(
        next_recency_order=recent,
        next_locked2=False,
        next_locked3=False,
        log_message=f"{'Premiere' if recent else 'Shuffle'}: Portrait/Landscape {order}",
    )


def satellite_browse_paths(
    *,
    which: int,
    query: str,
    f_mode_enabled: bool,
    recent: bool,
    sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    provider_metadata_root: Path | None = None,
) -> list[str]:
    """The paths a satellite's default browse holds under *query* and the current
    ordering — one clip per group, filter-honouring, premiere/shuffle-aware.

    This is the list a filter rebuild loads into the satellite, and equally the
    target "no loop" reshapes the queue back to when a group loop ends.  ``which``
    selects nothing here (both satellites browse the same way); it is kept for a
    symmetric call site.
    """
    library = _satellite_library(state_dir, provider_metadata_root)
    return build_satellite_playlist_paths(
        sources, f_mode_enabled, Path(favs_file),
        filter_query=query, recent=recent, library=library,
    )


@dataclass(frozen=True)
class SatelliteFilterFlowResult:
    count: int
    applied: bool
    log_message: str


def apply_satellite_filter(
    *,
    which: int,
    query: str,
    f_mode_enabled: bool,
    recent: bool,
    sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    cmd_file: str | Path,
    provider_media_root: Path | None = None,
    provider_metadata_root: Path | None = None,
) -> SatelliteFilterFlowResult:
    """Rebuild and reload one satellite (2=portrait, 3=landscape) under *query*.

    Ordering follows the caller's ``recent``/``f_mode`` just like a full rebuild,
    so the filtered playlist still honours premiere vs shuffle and F-mode.  A
    non-empty query that matches nothing leaves the current playlist in place
    rather than blanking the satellite; ``query == ""`` clears the filter.  The
    playlist file it writes is the one the satellite plays, so a RELOAD_PLAYLIST
    verb makes the player pick it up.
    """
    label = "portrait" if which == 2 else "landscape"
    name = PLAYLIST_PORTRAIT if which == 2 else PLAYLIST_LANDSCAPE
    paths = satellite_browse_paths(
        which=which, query=query, f_mode_enabled=f_mode_enabled, recent=recent,
        sources=sources, favs_file=favs_file, state_dir=state_dir,
        provider_metadata_root=provider_metadata_root,
    )
    if query and not paths:
        return SatelliteFilterFlowResult(0, False, f"Filter {label}: no matches for '{query}'")
    playlist_path = build_playlist_file_path(Path(state_dir), name)
    write_playlist_file(playlist_path, paths)
    write_satellite_command(Path(cmd_file), RELOAD_PLAYLIST_CMD)
    summary = "cleared" if not query else f"'{query}'"
    return SatelliteFilterFlowResult(len(paths), True, f"Filter {label}: {summary} ({len(paths)})")


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
    portrait_paused_file: str | Path,
    landscape_paused_file: str | Path,
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
    # The satellites obey their paused flag file each tick, so freezing playback
    # is a single flag write per side.  A paused native satellite simply cannot
    # auto-advance (its advance() returns early while paused), so OmniPause is a
    # settled state: one write holds it, with nothing to police afterwards.
    write_flag_file(portrait_paused_file, True)
    write_flag_file(landscape_paused_file, True)
    Path(genau_cmd_file).write_text("PAUSE", encoding="utf-8")
    if broker_cmd_file is not None:
        write_broker_command(broker_cmd_file, PARK_CMD)
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
    portrait_paused_file: str | Path,
    landscape_paused_file: str | Path,
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
        write_broker_command(broker_cmd_file, RESUME_CMD)
    # Unfreeze both satellites; a locked one holds its clip (its lock is
    # independent of the pause flag), an unlocked one resumes auto-advancing.
    write_flag_file(portrait_paused_file, False)
    write_flag_file(landscape_paused_file, False)
    return OmniPauseFlowResult(
        action=plan.action,
        next_omni_paused=plan.next_omni_paused,
        genau_branch=plan.genau_branch,
        log_message=plan.log_message,
    )

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_OFF, RELOAD_PLAYLIST, play_file
from funestra.modes import SatellitesMode

from .bridge_records import SatelliteChannel
from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import keep_aside
from .main_list_builds import BUILDS_HERE, MainListBuild, MainListBuilds
from .mode_plan import build_mode_switch_plan
from .modes import (
    SatelliteBuild,
    build_one_satellite_playlist,
    build_satellite_playlist_paths,
    scripted_item,
    write_playlist_file,
)
from .origenerator_switches import CLOSE_SLIDESHOW, KINO_MODE, OPEN_SLIDESHOW, said_about
from .polled_files import READER_HOLD_BUDGET_S, replace_despite_readers

logger = logging.getLogger(__name__)


def write_flag_file(path: str | Path, value: bool) -> None:
    flag = Path(path)
    if not replace_despite_readers(flag, "1" if value else "0"):
        logger.warning("Could not set %s to %d: another process kept it open for %gs",
                       flag.name, value, READER_HOLD_BUDGET_S)


@dataclass(frozen=True)
class ModeSwitchFlowResult:
    next_mode: str
    is_transition: bool
    log_message: str


FMODE_FUNESTRAS = (FunTimeFunestra.MAIN, FunTimeFunestra.PORTRAIT, FunTimeFunestra.LANDSCAPE)


@dataclass(frozen=True)
class FModeFlowResult:
    funestras: tuple[FunTimeFunestra, ...]
    enabled: bool
    log_message: str


def apply_mode_switch(
    *,
    current_mode: str,
    target_mode: str,
    omni_paused: bool,
    main_funestra_paused_file: str | Path,
    main_funestra_cmd_file: str | Path,
) -> ModeSwitchFlowResult:
    plan = build_mode_switch_plan(
        current_mode=current_mode,
        target_mode=target_mode,
        omni_paused=omni_paused,
    )
    if plan.is_transition:
        if plan.main_funestra_should_play is not None:
            write_flag_file(main_funestra_paused_file, not plan.main_funestra_should_play)
        append_command(Path(main_funestra_cmd_file), plan.show_cmd)
    return ModeSwitchFlowResult(
        next_mode=plan.target_mode,
        is_transition=plan.is_transition,
        log_message=plan.log_message,
    )


@dataclass(frozen=True)
class SatelliteFmodeInputs:
    channel: SatelliteChannel
    recent: bool = False
    filter_query: str = ""


def apply_satellite_fmode(
    satellite: SatelliteFmodeInputs, *, enabled: bool, metadata_root: Path | None = None,
) -> None:
    build = SatelliteBuild(
        satellite.channel.sources, favorites_filter=enabled, recent=satellite.recent,
        filter_query=satellite.filter_query,
    )
    build_one_satellite_playlist(build, satellite.channel.playlist_file, metadata_root=metadata_root)
    append_command(satellite.channel.cmd_file, RELOAD_PLAYLIST)


def apply_fmode(
    *,
    funestras: Sequence[FunTimeFunestra],
    enabled: bool,
    main_build: MainListBuild,
    main_builds: MainListBuilds = BUILDS_HERE,
    satellites: Mapping[FunTimeFunestra, SatelliteFmodeInputs],
) -> FModeFlowResult:
    named = tuple(funestra for funestra in FMODE_FUNESTRAS if funestra in funestras)
    if FunTimeFunestra.MAIN in named:
        main_builds.build(replace(
            main_build, settings=replace(main_build.settings, scripted_filter=enabled)))
    for funestra in FunTimeFunestra.SATELLITES:
        if funestra in named:
            apply_satellite_fmode(satellites[funestra], enabled=enabled,
                                  metadata_root=main_build.main_list.metadata_root)
    return FModeFlowResult(
        funestras=named,
        enabled=enabled,
        log_message=(
            f"F-mode {'enabled' if enabled else 'disabled'}: "
            f"{', '.join(funestra.label for funestra in named) or 'nothing'}"
        ),
    )


def satellite_browse_paths(
    *,
    query: str,
    favorites_filter: bool,
    recent: bool,
    sources: str,
    regen_metadata_root: Path | None = None,
) -> list[str]:
    return build_satellite_playlist_paths(
        SatelliteBuild(sources, favorites_filter=favorites_filter, recent=recent,
                       filter_query=query),
        metadata_root=regen_metadata_root,
    )


@dataclass(frozen=True)
class SatelliteFilterFlowResult:
    count: int
    applied: bool
    log_message: str


def apply_satellite_filter(
    funestra: FunTimeFunestra,
    channel: SatelliteChannel,
    build: SatelliteBuild,
    *,
    start_at_top: bool = False,
    regen_metadata_root: Path | None = None,
) -> SatelliteFilterFlowResult:
    label = FunTimeFunestra(funestra).label
    query = build.filter_query
    paths = build_satellite_playlist_paths(build, metadata_root=regen_metadata_root)
    if query and not paths:
        return SatelliteFilterFlowResult(0, False, f"Filter {label}: no matches for '{query}'")
    write_playlist_file(channel.playlist_file, paths, metadata_root=regen_metadata_root)
    append_command(channel.cmd_file, RELOAD_PLAYLIST)
    if start_at_top and paths:
        append_command(channel.cmd_file, play_file(scripted_item(paths[0], regen_metadata_root)))
    summary = "cleared" if not query else f"'{query}'"
    return SatelliteFilterFlowResult(len(paths), True, f"Filter {label}: {summary} ({len(paths)})")


@dataclass(frozen=True)
class SatelliteSwitchFlowResult:
    next_mode: SatellitesMode
    is_transition: bool
    log_message: str


def apply_satellite_switch(
    *,
    funestra: FunTimeFunestra,
    current_mode: SatellitesMode,
    target_mode: SatellitesMode,
    origenerator_cmd_file: str | Path | None,
    channel: SatelliteChannel,
) -> SatelliteSwitchFlowResult:
    satellite = funestra.label.capitalize()
    if current_mode == target_mode:
        return SatelliteSwitchFlowResult(
            next_mode=target_mode, is_transition=False,
            log_message=f"{satellite} already in {target_mode} mode")
    if target_mode == KINO_MODE:
        if origenerator_cmd_file is not None:
            append_command(Path(origenerator_cmd_file), said_about(funestra.label, CLOSE_SLIDESHOW))
    else:
        keep_aside(channel)
        append_command(Path(channel.cmd_file), LOCK_OFF)
        if origenerator_cmd_file is not None:
            append_command(Path(origenerator_cmd_file), said_about(funestra.label, OPEN_SLIDESHOW))
    return SatelliteSwitchFlowResult(
        next_mode=target_mode, is_transition=True,
        log_message=f"{satellite} switched to {target_mode} mode")

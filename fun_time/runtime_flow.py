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
from .main_list import MainList, MainListSettings
from .main_list_builds import BUILDS_HERE, MainListBuild, MainListBuilds
from .mode_plan import build_mode_switch_plan
from .modes import (
    PLAYLIST_LANDSCAPE,
    PLAYLIST_PORTRAIT,
    build_one_satellite_playlist,
    build_playlist_file_path,
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


def rebuild_main_list(
    main_list: MainList,
    settings: MainListSettings,
    *,
    main_funestra_cmd_file: str | Path,
    on_screen: str = "",
    start_at_top: bool = False,
    builds: MainListBuilds = BUILDS_HERE,
    says: str = "",
) -> None:
    builds.build(MainListBuild(
        main_list=main_list, settings=settings,
        main_funestra_cmd_file=Path(main_funestra_cmd_file),
        on_screen=on_screen, start_at_top=start_at_top, says=says,
    ))


def apply_satellite_fmode(
    *,
    funestra: FunTimeFunestra,
    enabled: bool,
    sources: str,
    state_dir: str | Path,
    cmd_file: str | Path,
    recent: bool = False,
    filter_query: str = "",
    regen_metadata_root: Path | None = None,
) -> None:
    build_one_satellite_playlist(
        sources=sources,
        name=PLAYLIST_PORTRAIT if funestra == FunTimeFunestra.PORTRAIT else PLAYLIST_LANDSCAPE,
        state_dir=Path(state_dir),
        favorites_filter=enabled,
        recent=recent,
        filter_query=filter_query,
        metadata_root=regen_metadata_root,
    )
    append_command(Path(cmd_file), RELOAD_PLAYLIST)


@dataclass(frozen=True)
class SatelliteFmodeInputs:
    sources: str
    cmd_file: str | Path
    recent: bool = False
    filter_query: str = ""


def apply_fmode(
    *,
    funestras: Sequence[FunTimeFunestra],
    enabled: bool,
    main_list: MainList,
    main_settings: MainListSettings,
    main_on_screen: str = "",
    state_dir: str | Path,
    main_funestra_cmd_file: str | Path,
    main_builds: MainListBuilds = BUILDS_HERE,
    satellites: Mapping[FunTimeFunestra, SatelliteFmodeInputs],
    regen_metadata_root: Path | None = None,
) -> FModeFlowResult:
    named = tuple(funestra for funestra in FMODE_FUNESTRAS if funestra in funestras)
    if FunTimeFunestra.MAIN in named:
        rebuild_main_list(
            main_list, replace(main_settings, scripted_filter=enabled),
            main_funestra_cmd_file=main_funestra_cmd_file, on_screen=main_on_screen,
            builds=main_builds,
        )
    for funestra in FunTimeFunestra.SATELLITES:
        if funestra in named:
            satellite = satellites[funestra]
            apply_satellite_fmode(
                funestra=funestra,
                enabled=enabled,
                sources=satellite.sources,
                state_dir=state_dir,
                cmd_file=satellite.cmd_file,
                recent=satellite.recent,
                filter_query=satellite.filter_query,
                regen_metadata_root=regen_metadata_root,
            )
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
        sources, favorites_filter,
        filter_query=query, recent=recent, metadata_root=regen_metadata_root,
    )


@dataclass(frozen=True)
class SatelliteFilterFlowResult:
    count: int
    applied: bool
    log_message: str


def apply_satellite_filter(
    *,
    funestra: FunTimeFunestra,
    query: str,
    favorites_filter: bool,
    recent: bool,
    sources: str,
    state_dir: str | Path,
    cmd_file: str | Path,
    start_at_top: bool = False,
    regen_metadata_root: Path | None = None,
) -> SatelliteFilterFlowResult:
    label = FunTimeFunestra(funestra).label
    name = PLAYLIST_PORTRAIT if funestra == FunTimeFunestra.PORTRAIT else PLAYLIST_LANDSCAPE
    paths = satellite_browse_paths(
        query=query, favorites_filter=favorites_filter, recent=recent,
        sources=sources, regen_metadata_root=regen_metadata_root,
    )
    if query and not paths:
        return SatelliteFilterFlowResult(0, False, f"Filter {label}: no matches for '{query}'")
    playlist_path = build_playlist_file_path(Path(state_dir), name)
    write_playlist_file(playlist_path, paths, metadata_root=regen_metadata_root)
    append_command(Path(cmd_file), RELOAD_PLAYLIST)
    if start_at_top and paths:
        append_command(Path(cmd_file), play_file(scripted_item(paths[0], regen_metadata_root)))
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

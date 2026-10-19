from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from funestra.console import OSR2_DRIVING
from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_OFF, RELOAD_PLAYLIST, play_file
from funestra.modes import SatellitesMode

logger = logging.getLogger(__name__)

from .bridge_records import SatelliteChannel
from .broker_control import write_broker_command
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
from .omnipause import build_omnipause_plan
from .origenerator_switches import CLOSE_SLIDESHOW, KINO_MODE, OPEN_SLIDESHOW, said_about
from .polled_files import READER_HOLD_BUDGET_S, replace_despite_readers
from .room_funestras import RoomFunestra


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


# F-mode can be set on any of the three, each with its own flag, because it means
# a different narrowing on each: the satellites drop to the favorites, the Main
# Funestra to the videos a person hand-wrote a funscript for.
FMODE_FUNESTRAS = (RoomFunestra.MAIN, RoomFunestra.PORTRAIT, RoomFunestra.LANDSCAPE)


@dataclass(frozen=True)
class FModeFlowResult:
    """Which Funestras were put into (or out of) F-mode, and what to say about it."""

    funestras: tuple[RoomFunestra, ...]
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
    """Switch the main slot between kino and genau mode, sending the Main Funestra
    what :class:`fun_time.mode_plan.ModeSwitchPlan` says it is owed.

    Queued, never written whole: the file is a queue shared with every other
    writer, and replacing it here erased whatever it had appended since the
    last drain.
    """
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
    """``start_at_top`` is the reorder's, and means here what it means for a
    satellite: the Main Funestra keeps the video on screen across a reload whenever the new list
    still holds it — which a reorder's always does — so a newest-first rebuild
    would otherwise apply only after it, and the new arrivals never come up.
    """
    builds.build(MainListBuild(
        main_list=main_list, settings=settings,
        main_funestra_cmd_file=Path(main_funestra_cmd_file),
        on_screen=on_screen, start_at_top=start_at_top, says=says,
    ))


def apply_satellite_fmode(
    *,
    funestra: RoomFunestra,
    enabled: bool,
    sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    cmd_file: str | Path,
    recent: bool = False,
    filter_query: str = "",
    regen_metadata_root: Path | None = None,
) -> None:
    """Rebuild one satellite's playlist under *enabled* and tell it to re-read.

    F-mode narrows a satellite to the favorites.  The side's own filter and
    order ride along, so narrowing to favorites does not quietly undo either.
    """
    build_one_satellite_playlist(
        sources=sources,
        name=PLAYLIST_PORTRAIT if funestra == RoomFunestra.PORTRAIT else PLAYLIST_LANDSCAPE,
        favs_file=Path(favs_file),
        state_dir=Path(state_dir),
        favorites_filter=enabled,
        recent=recent,
        filter_query=filter_query,
        metadata_root=regen_metadata_root,
    )
    append_command(Path(cmd_file), RELOAD_PLAYLIST)


@dataclass(frozen=True)
class SatelliteFmodeInputs:
    """One satellite's F-mode rebuild inputs: its channel and narrowing."""

    sources: str
    cmd_file: str | Path
    recent: bool = False
    filter_query: str = ""


def apply_fmode(
    *,
    funestras: Sequence[RoomFunestra],
    enabled: bool,
    main_list: MainList,
    main_settings: MainListSettings,
    main_on_screen: str = "",
    favs_file: str | Path,
    state_dir: str | Path,
    main_funestra_cmd_file: str | Path,
    main_builds: MainListBuilds = BUILDS_HERE,
    satellites: Mapping[RoomFunestra, SatelliteFmodeInputs],
    regen_metadata_root: Path | None = None,
) -> FModeFlowResult:
    """Put each of *Funestras* into F-mode, or take it out, and rebuild just those.

    A Funestra not named is not touched at all — its playlist file is left exactly
    as it is, so setting one side's F-mode cannot reshuffle the other's queue out
    from under it.  That is the whole reason the rebuild is per Funestra rather
    than one build of all three.
    """
    named = tuple(funestra for funestra in FMODE_FUNESTRAS if funestra in funestras)
    if RoomFunestra.MAIN in named:
        rebuild_main_list(
            main_list, replace(main_settings, scripted_filter=enabled),
            main_funestra_cmd_file=main_funestra_cmd_file, on_screen=main_on_screen,
            builds=main_builds,
        )
    for funestra in RoomFunestra.SATELLITES:
        if funestra in named:
            satellite = satellites[funestra]
            apply_satellite_fmode(
                funestra=funestra,
                enabled=enabled,
                sources=satellite.sources,
                favs_file=favs_file,
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
    favs_file: str | Path,
    regen_metadata_root: Path | None = None,
) -> list[str]:
    """The paths a satellite's default browse holds under *query* and the current
    ordering — one clip per group, filter-honoring, Latest/Shuffle-aware.

    This is the list a filter rebuild loads into the satellite, and equally the
    target "no loop" reshapes the queue back to when a group loop ends.
    """
    return build_satellite_playlist_paths(
        sources, favorites_filter, Path(favs_file),
        filter_query=query, recent=recent, metadata_root=regen_metadata_root,
    )


@dataclass(frozen=True)
class SatelliteFilterFlowResult:
    count: int
    applied: bool
    log_message: str


def apply_satellite_filter(
    *,
    funestra: RoomFunestra,
    query: str,
    favorites_filter: bool,
    recent: bool,
    sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    cmd_file: str | Path,
    start_at_top: bool = False,
    regen_metadata_root: Path | None = None,
) -> SatelliteFilterFlowResult:
    """Rebuild and reload one satellite under *query*.

    Ordering follows the caller's ``recent``/``favorites_filter`` just like a full rebuild,
    so the filtered playlist still honors Latest vs Shuffle and F-mode.  A
    non-empty query that matches nothing leaves the current playlist in place
    rather than blanking the satellite; ``query == ""`` clears the filter.  The
    playlist file it writes is the one the satellite plays, so a RELOAD_PLAYLIST
    verb makes the Funestra pick it up.

    That reload keeps the clip on screen playing while it survives the new list, and
    carries on from where it sits — which is right for a filter, and wrong for a
    caller whose whole point is a fresh start.  Reordering newest-first is exactly
    that: the new order would otherwise only apply *after* the clip playing, and the
    newest arrivals never come up.  ``start_at_top`` follows the reload with a jump
    to the head of the list it just wrote.
    """
    label = RoomFunestra(funestra).label
    name = PLAYLIST_PORTRAIT if funestra == RoomFunestra.PORTRAIT else PLAYLIST_LANDSCAPE
    paths = satellite_browse_paths(
        query=query, favorites_filter=favorites_filter, recent=recent,
        sources=sources, favs_file=favs_file, regen_metadata_root=regen_metadata_root,
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
    funestra: RoomFunestra,
    current_mode: SatellitesMode,
    target_mode: SatellitesMode,
    origenerator_cmd_file: str | Path | None,
    channel: SatelliteChannel,
) -> SatelliteSwitchFlowResult:
    """Switch one Satellite Funestra between its own videos and the hosted
    app's slideshows.

    Like the main slot's switch, nothing is torn down, and nothing pauses.
    Entering keeps the Funestra's own list aside for the way back, lets go of
    the session's hold on it -- what holds is the app's to say now -- and asks
    the app for a slideshow on it, so the mode opens playing rather than empty: the
    app picks the set, the whole library of that side's shape, shuffled.
    Leaving tells it to let go; the Funestra comes home once it has (see
    :mod:`fun_time.funestra_handover`).
    """
    side = funestra.label.capitalize()
    if current_mode == target_mode:
        return SatelliteSwitchFlowResult(
            next_mode=target_mode, is_transition=False,
            log_message=f"{side} already in {target_mode} mode")
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
        log_message=f"{side} switched to {target_mode} mode")


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

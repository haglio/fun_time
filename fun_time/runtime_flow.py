from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from .modes import (
    PLAYLIST_LANDSCAPE,
    PLAYLIST_NAU,
    PLAYLIST_PORTRAIT,
    SatelliteLibraryContext,
    build_one_satellite_playlist,
    build_playlist_file_path,
    build_main_playlist_paths,
    build_satellite_playlist_paths,
    matching_funscript,
    playlist_entry_line,
    write_nau_playlist_file,
    write_playlist_file,
)
from .broker_control import RESUME_CMD, write_broker_command
from .omnipause import build_omnipause_plan
from .mode_plan import build_mode_switch_plan, genau_active
from .satellite_control import write_satellite_command
from .watch_stats import watch_stats_path

# Both Nau and the native satellites re-read their playlist file on this verb.
RELOAD_PLAYLIST_CMD = "RELOAD_PLAYLIST"
PLAY_FILE_CMD = "PLAY_FILE"
# Nau's HUD says whether F-mode is on, and this is the only way it can know: the
# playlist it is handed has already been narrowed, and a list of scripted videos
# looks like any other.  The satellites need no such verb — fun_time draws their
# HUD model itself.
SET_F_MODE_CMD = "SET_F_MODE"
# Puts Nau back into an A/B loop it was left running, bounds and all.  The only
# piece of the main player's state a restart has to hand back rather than rebuild: a
# loop is a range inside one video, so it dies with the player process while
# everything else rides in on the playlist or a seeded flag.
SET_LOOP_CMD = "SET_LOOP"


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


# The three players F-mode can be set on, each with its own flag.  It means a
# different narrowing on each — the satellites drop to the favorites, the main player
# to the videos that have a funscript — which is exactly why it is worth setting
# one player at a time.
MAIN_PLAYER = "main"
PORTRAIT_PLAYER = "portrait"
LANDSCAPE_PLAYER = "landscape"
FMODE_PLAYERS = (MAIN_PLAYER, PORTRAIT_PLAYER, LANDSCAPE_PLAYER)


@dataclass(frozen=True)
class FModeFlowResult:
    """Which players were put into (or out of) F-mode, and what to say about it."""

    players: tuple[str, ...]
    enabled: bool
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
            cmd for cmd in (plan.genau_cmd, plan.hud_cmd, plan.genau_display_cmd)
            if cmd is not None
        ]
        if cmds:
            Path(genau_cmd_file).write_text("\n".join(cmds), encoding="utf-8")
        # Nau is told which mode the main slot is in on every switch: in
        # hybrid, Genau's window is a transparent layer over Nau's and its own
        # panel holds the top-left corner, so Nau starts its own furniture past
        # it.  It is told whether it is on screen too, the mirror of the
        # DISPLAY_ON/DISPLAY_OFF Genau gets.  All of it on one write, together
        # with the T-Code re-enable, because this file is overwritten, not
        # appended — a second write would drop the first.
        nau_cmds = [f"SET_HYBRID {int(plan.target_mode == 'hybrid')}"]
        if plan.nau_display_cmd is not None:
            nau_cmds.append(plan.nau_display_cmd)
        if plan.reenable_nau_tcode:
            nau_cmds.append("SET_TCODE_ENABLED 1")
        Path(nau_cmd_file).write_text("\n".join(nau_cmds), encoding="utf-8")
        if not will_genau and broker_cmd_file is not None:
            write_broker_command(broker_cmd_file, RESUME_CMD)
    return ModeSwitchFlowResult(
        next_mode=plan.target_mode,
        is_transition=plan.is_transition,
        log_message=plan.log_message,
    )


def apply_main_fmode(
    *,
    enabled: bool,
    main_sources: str,
    state_dir: str | Path,
    nau_cmd_file: str | Path,
    recent: bool = False,
    start_at_top: bool = False,
) -> None:
    """Rebuild the main player's playlist under *enabled* and hand it to Nau.

    F-mode narrows the main player to the videos that have a funscript beside them —
    the OSR2 has something to follow for every clip that comes up.

    ``start_at_top`` is the reorder's, and means here exactly what it means for a
    satellite (see :func:`apply_satellite_filter`): Nau keeps the video on screen
    across a reload whenever the new list still holds it — which a reorder's
    always does, since it filters nothing out — so a newest-first rebuild would
    otherwise apply only *behind* the video playing and the new arrivals would
    never come up.  An F-mode change wants the opposite and does not ask.
    """
    paths = build_main_playlist_paths(main_sources, enabled, recent=recent)
    write_nau_playlist_file(build_playlist_file_path(Path(state_dir), PLAYLIST_NAU), paths)
    # Every verb on one write: this file is overwritten, not appended, so telling
    # Nau the flag afterwards would drop the reload that goes with it.  Nau's HUD
    # has no other way to know — the playlist it is handed has already been
    # narrowed, and a list of scripted videos looks like any other.  The jump goes
    # last, so it lands on the list the reload has just taken.
    verbs = [RELOAD_PLAYLIST_CMD, f"{SET_F_MODE_CMD} {int(enabled)}"]
    if start_at_top and paths:
        head = playlist_entry_line(paths[0], matching_funscript(paths[0]))
        verbs.append(f"{PLAY_FILE_CMD} {head}")
    Path(nau_cmd_file).write_text("\n".join(verbs), encoding="utf-8")


def apply_satellite_fmode(
    *,
    which: int,
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
        name=PLAYLIST_PORTRAIT if which == 2 else PLAYLIST_LANDSCAPE,
        favs_file=Path(favs_file),
        state_dir=Path(state_dir),
        f_mode=enabled,
        recent=recent,
        filter_query=filter_query,
        library=_satellite_library(state_dir, regen_metadata_root),
    )
    write_satellite_command(Path(cmd_file), RELOAD_PLAYLIST_CMD)


def apply_fmode(
    *,
    players: Sequence[str],
    enabled: bool,
    portrait_recent: bool,
    landscape_recent: bool,
    main_sources: str,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    main_recent: bool = False,
    portrait_cmd_file: str | Path,
    landscape_cmd_file: str | Path,
    nau_cmd_file: str | Path,
    regen_metadata_root: Path | None = None,
    portrait_filter: str = "",
    landscape_filter: str = "",
) -> FModeFlowResult:
    """Put each of *players* into F-mode, or take it out, and rebuild just those.

    A player not named is not touched at all — its playlist file is left exactly
    as it is, so setting one side's F-mode cannot reshuffle the other's queue out
    from under it.  That is the whole reason the rebuild is per player rather than
    the one all-three build this used to do.
    """
    named = tuple(player for player in FMODE_PLAYERS if player in players)
    if MAIN_PLAYER in named:
        apply_main_fmode(
            enabled=enabled,
            main_sources=main_sources,
            recent=main_recent,
            state_dir=state_dir,
            nau_cmd_file=nau_cmd_file,
        )
    for player, which, sources, cmd_file, recent, query in (
        (PORTRAIT_PLAYER, 2, portrait_sources, portrait_cmd_file, portrait_recent, portrait_filter),
        (LANDSCAPE_PLAYER, 3, landscape_sources, landscape_cmd_file, landscape_recent, landscape_filter),
    ):
        if player in named:
            apply_satellite_fmode(
                which=which,
                enabled=enabled,
                sources=sources,
                favs_file=favs_file,
                state_dir=state_dir,
                cmd_file=cmd_file,
                recent=recent,
                filter_query=query,
                regen_metadata_root=regen_metadata_root,
            )
    return FModeFlowResult(
        players=named,
        enabled=enabled,
        log_message=(
            f"F-mode {'enabled' if enabled else 'disabled'}: {', '.join(named) or 'nothing'}"
        ),
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
    regen_metadata_root: Path | None = None,
) -> list[str]:
    """The paths a satellite's default browse holds under *query* and the current
    ordering — one clip per group, filter-honoring, Latest/Shuffle-aware.

    This is the list a filter rebuild loads into the satellite, and equally the
    target "no loop" reshapes the queue back to when a group loop ends.  ``which``
    selects nothing here (both satellites browse the same way); it is kept for a
    symmetric call site.
    """
    library = _satellite_library(state_dir, regen_metadata_root)
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
    start_at_top: bool = False,
    regen_media_root: Path | None = None,
    regen_metadata_root: Path | None = None,
) -> SatelliteFilterFlowResult:
    """Rebuild and reload one satellite (2=portrait, 3=landscape) under *query*.

    Ordering follows the caller's ``recent``/``f_mode`` just like a full rebuild,
    so the filtered playlist still honors Latest vs Shuffle and F-mode.  A
    non-empty query that matches nothing leaves the current playlist in place
    rather than blanking the satellite; ``query == ""`` clears the filter.  The
    playlist file it writes is the one the satellite plays, so a RELOAD_PLAYLIST
    verb makes the player pick it up.

    That reload keeps the clip on screen playing while it survives the new list, and
    carries on from where it sits — which is right for a filter, and wrong for a
    caller whose whole point is a fresh start.  Reordering newest-first is exactly
    that: the new order would otherwise only apply *behind* the clip playing, and the
    newest arrivals never come up.  ``start_at_top`` follows the reload with a jump
    to the head of the list it just wrote.
    """
    label = "portrait" if which == 2 else "landscape"
    name = PLAYLIST_PORTRAIT if which == 2 else PLAYLIST_LANDSCAPE
    paths = satellite_browse_paths(
        which=which, query=query, f_mode_enabled=f_mode_enabled, recent=recent,
        sources=sources, favs_file=favs_file, state_dir=state_dir,
        regen_metadata_root=regen_metadata_root,
    )
    if query and not paths:
        return SatelliteFilterFlowResult(0, False, f"Filter {label}: no matches for '{query}'")
    playlist_path = build_playlist_file_path(Path(state_dir), name)
    write_playlist_file(playlist_path, paths)
    write_satellite_command(Path(cmd_file), RELOAD_PLAYLIST_CMD)
    if start_at_top and paths:
        write_satellite_command(Path(cmd_file), f"{PLAY_FILE_CMD} {paths[0]}")
    summary = "cleared" if not query else f"'{query}'"
    return SatelliteFilterFlowResult(len(paths), True, f"Filter {label}: {summary} ({len(paths)})")


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
    genau_cmd_file: str | Path,
    nau_paused_file: str | Path,
    broker_cmd_file: str | Path | None = None,
    relief: bool = False,
) -> OmniPauseFlowResult:
    """Freeze the whole session, and send the OSR2 somewhere safe.

    ``relief`` picks which somewhere: home by default, or — for the sensation
    emergency Shift+Esc raises — the far end of the stroke, away from the user.
    Nothing else about the freeze differs between the two.
    """
    plan = build_omnipause_plan(
        "relief" if relief else "enter",
        omni_paused=omni_paused,
        main_mode=main_mode,
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
    genau_cmd_file: str | Path,
    nau_paused_file: str | Path,
    broker_cmd_file: str | Path | None = None,
) -> OmniPauseFlowResult:
    plan = build_omnipause_plan(
        "leave",
        omni_paused=omni_paused,
        main_mode=main_mode,
    )
    if plan.genau_branch:
        write_flag_file(genau_paused_file, False)
        write_flag_file(audio_paused_file, False)
    if plan.resume_genau_playback:
        Path(genau_cmd_file).write_text("RESUME", encoding="utf-8")
    if plan.resume_nau_playback:
        write_flag_file(nau_paused_file, False)
    if broker_cmd_file is not None:
        write_broker_command(broker_cmd_file, plan.broker_command)
    # Unfreeze both satellites; a locked one holds its clip (its lock is
    # independent of the pause flag), an unlocked one resumes auto-advancing.
    write_flag_file(portrait_paused_file, False)
    write_flag_file(landscape_paused_file, False)
    return OmniPauseFlowResult(
        next_omni_paused=plan.next_omni_paused,
        log_message=plan.log_message,
    )

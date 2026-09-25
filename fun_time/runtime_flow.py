from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app_support.file_channel import write_flag
from player_core.console import OSR2_DRIVING
from player_core.file_channel import append_command
from player_core.player_verbs import LOCK_OFF, RELOAD_PLAYLIST, SET_F_MODE, play_file
from player_core.playlist import PlaylistItem

logger = logging.getLogger(__name__)

from .bridge_records import SatelliteChannel
from .broker_control import write_broker_command
from .mode_plan import build_mode_switch_plan
from .modes import (
    PLAYLIST_LANDSCAPE,
    PLAYLIST_MAIN_PLAYER,
    PLAYLIST_PORTRAIT,
    VideoShapes,
    build_main_playlist_paths,
    build_one_satellite_playlist,
    build_playlist_file_path,
    build_satellite_playlist_paths,
    scripted_item,
    write_main_player_playlist_file,
    write_playlist_file,
)
from .omnipause import build_omnipause_plan
from .player_handover import keep_aside
from .players import Player
from .satellites_mode import CLOSE_SHOWS, OPEN_SHOWS, VIDEO_MODE

# Puts the main player back into an A/B loop it was left running, bounds and all.  The only
# piece of the main player's state a restart has to hand back rather than rebuild: a
# loop is a range inside one video, so it dies with the player process while
# everything else rides in on the playlist or a seeded flag.
SET_LOOP_CMD = "SET_LOOP"


def read_flag_file(path: str | Path, default: bool) -> bool:
    try:
        target = Path(path)
        if not target.exists():
            return default
        return target.read_text(encoding="utf-8").strip() not in {"", "0", "false", "False"}
    except OSError:
        return default


def write_flag_file(path: str | Path, value: bool) -> None:
    write_flag(Path(path), value)


@dataclass(frozen=True)
class ModeSwitchFlowResult:
    next_mode: str
    is_transition: bool
    log_message: str


# F-mode can be set on any of the three, each with its own flag, because it means
# a different narrowing on each: the satellites drop to the favorites, the main
# player to the videos a person hand-wrote a funscript for.
FMODE_PLAYERS = (Player.MAIN, Player.PORTRAIT, Player.LANDSCAPE)


@dataclass(frozen=True)
class FModeFlowResult:
    """Which players were put into (or out of) F-mode, and what to say about it."""

    players: tuple[Player, ...]
    enabled: bool
    log_message: str


def apply_mode_switch(
    *,
    current_mode: str,
    target_mode: str,
    omni_paused: bool,
    genau_cmd_file: str | Path,
    main_player_paused_file: str | Path,
    main_player_cmd_file: str | Path,
) -> ModeSwitchFlowResult:
    """Switch the main slot between video and genau mode, sending each player
    what :class:`fun_time.mode_plan.ModeSwitchPlan` says it is owed.

    Queued, never written whole: the files are queues shared with every other
    writer, and replacing one here erased whatever they had appended since the
    last drain.
    """
    plan = build_mode_switch_plan(
        current_mode=current_mode,
        target_mode=target_mode,
        omni_paused=omni_paused,
    )
    if plan.is_transition:
        write_flag_file(main_player_paused_file, not plan.main_player_should_play)
        for cmd in (plan.genau_cmd, plan.hud_cmd):
            append_command(Path(genau_cmd_file), cmd)
        append_command(Path(main_player_cmd_file), plan.main_player_display_cmd)
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
    main_player_cmd_file: str | Path,
    recent: bool = False,
    start_at_top: bool = False,
    shapes: VideoShapes | None = None,
    metadata_root: Path | None = None,
) -> None:
    """Rebuild the main player's playlist under *enabled* and hand it to the main player.

    The one place the main player's playlist is rewritten while a session runs,
    so everything narrowing it rides here: F-mode, the browse order, and the
    headset's shape filter.

    F-mode narrows the main player to the videos a person hand-wrote a funscript
    for — the OSR2 follows a script someone meant, not one a bulk run inferred.

    ``start_at_top`` is the reorder's, and means here what it means for a
    satellite: the main player keeps the video on screen across a reload whenever the new list
    still holds it — which a reorder's always does — so a newest-first rebuild
    would otherwise apply only after it, and the new arrivals never come up.
    """
    paths = build_main_playlist_paths(main_sources, enabled, recent=recent, shapes=shapes,
                                      metadata_root=metadata_root)
    write_main_player_playlist_file(build_playlist_file_path(Path(state_dir), PLAYLIST_MAIN_PLAYER), paths,
                                    metadata_root=metadata_root)
    # Queued in order — the reload first, the flag with it, the jump last so it
    # lands on the list the reload has just taken.  The main player's HUD has no other way
    # to know the flag: the playlist it is handed has already been narrowed,
    # and a list of scripted videos looks like any other.
    verbs = [RELOAD_PLAYLIST, f"{SET_F_MODE} {int(enabled)}"]
    if start_at_top and paths:
        verbs.append(play_file(scripted_item(paths[0], metadata_root)))
    for verb in verbs:
        append_command(Path(main_player_cmd_file), verb)


def apply_satellite_fmode(
    *,
    player: Player,
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
        name=PLAYLIST_PORTRAIT if player == Player.PORTRAIT else PLAYLIST_LANDSCAPE,
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
    players: Sequence[Player],
    enabled: bool,
    main_sources: str,
    favs_file: str | Path,
    state_dir: str | Path,
    main_recent: bool = False,
    main_shapes: VideoShapes | None = None,
    main_player_cmd_file: str | Path,
    satellites: Mapping[Player, SatelliteFmodeInputs],
    regen_metadata_root: Path | None = None,
) -> FModeFlowResult:
    """Put each of *players* into F-mode, or take it out, and rebuild just those.

    A player not named is not touched at all — its playlist file is left exactly
    as it is, so setting one side's F-mode cannot reshuffle the other's queue out
    from under it.  That is the whole reason the rebuild is per player rather
    than one build of all three.
    """
    named = tuple(player for player in FMODE_PLAYERS if player in players)
    if Player.MAIN in named:
        apply_main_fmode(
            enabled=enabled,
            main_sources=main_sources,
            recent=main_recent,
            state_dir=state_dir,
            main_player_cmd_file=main_player_cmd_file,
            shapes=main_shapes,
            metadata_root=regen_metadata_root,
        )
    for player in Player.SATELLITES:
        if player in named:
            satellite = satellites[player]
            apply_satellite_fmode(
                player=player,
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
        players=named,
        enabled=enabled,
        log_message=(
            f"F-mode {'enabled' if enabled else 'disabled'}: "
            f"{', '.join(player.label for player in named) or 'nothing'}"
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
    player: Player,
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
    verb makes the player pick it up.

    That reload keeps the clip on screen playing while it survives the new list, and
    carries on from where it sits — which is right for a filter, and wrong for a
    caller whose whole point is a fresh start.  Reordering newest-first is exactly
    that: the new order would otherwise only apply *after* the clip playing, and the
    newest arrivals never come up.  ``start_at_top`` follows the reload with a jump
    to the head of the list it just wrote.
    """
    label = Player(player).label
    name = PLAYLIST_PORTRAIT if player == Player.PORTRAIT else PLAYLIST_LANDSCAPE
    paths = satellite_browse_paths(
        query=query, favorites_filter=favorites_filter, recent=recent,
        sources=sources, favs_file=favs_file, regen_metadata_root=regen_metadata_root,
    )
    if query and not paths:
        return SatelliteFilterFlowResult(0, False, f"Filter {label}: no matches for '{query}'")
    playlist_path = build_playlist_file_path(Path(state_dir), name)
    write_playlist_file(playlist_path, paths)
    append_command(Path(cmd_file), RELOAD_PLAYLIST)
    if start_at_top and paths:
        append_command(Path(cmd_file), play_file(PlaylistItem(Path(paths[0]))))
    summary = "cleared" if not query else f"'{query}'"
    return SatelliteFilterFlowResult(len(paths), True, f"Filter {label}: {summary} ({len(paths)})")


@dataclass(frozen=True)
class SatellitesSwitchFlowResult:
    next_mode: str
    is_transition: bool
    log_message: str


def apply_satellites_switch(
    *,
    current_mode: str,
    target_mode: str,
    omni_paused: bool,
    origenerator_cmd_file: str | Path | None,
    channels: Sequence[SatelliteChannel],
) -> SatellitesSwitchFlowResult:
    """Switch the satellite side between video and origenerator mode.

    Like the main slot's switch, nothing is torn down, and nothing pauses: in
    origenerator mode the two players show the hosted app's slideshows.
    Entering keeps each player's own list aside for the way back, lets go of the
    session's hold on the player -- what holds is the app's to say now -- and
    tells the app to fill both, so the mode opens playing rather than empty.
    Leaving tells it to let go; the players come home once it has (see
    :mod:`fun_time.player_handover`).  Under OmniPause the switch is
    state-only, exactly as a main-mode switch is: the room is frozen.
    """
    if current_mode == target_mode:
        return SatellitesSwitchFlowResult(
            next_mode=target_mode, is_transition=False,
            log_message=f"Satellites already in {target_mode} mode")
    if omni_paused:
        return SatellitesSwitchFlowResult(
            next_mode=target_mode, is_transition=False,
            log_message=f"Satellites set to {target_mode} (omnipaused)")
    if target_mode == VIDEO_MODE:
        if origenerator_cmd_file is not None:
            append_command(Path(origenerator_cmd_file), CLOSE_SHOWS)
    else:
        for channel in channels:
            keep_aside(channel)
            append_command(Path(channel.cmd_file), LOCK_OFF)
        # Both players come up playing, the way they are playing the moment
        # video mode is entered: a mode that opened onto two players with
        # nothing new on them asked the user to go and start it.  The hosted
        # app picks the sets -- its whole library, shuffled, one shape each.
        if origenerator_cmd_file is not None:
            append_command(Path(origenerator_cmd_file), OPEN_SHOWS)
    return SatellitesSwitchFlowResult(
        next_mode=target_mode, is_transition=True,
        log_message=f"Satellites switched to {target_mode} mode")


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
    main_player_paused_file: str | Path,
    broker_cmd_file: str | Path | None = None,
    origenerator_paused_file: str | Path | None = None,
    relief: bool = False,
) -> OmniPauseFlowResult:
    """Freeze the whole session, and send the OSR2 somewhere safe.

    ``relief`` picks which somewhere: home by default, or — for the sensation
    emergency Shift+Esc raises — the far end of the travel, away from the user.
    Nothing else about the freeze differs between the two.
    """
    plan = build_omnipause_plan(
        "relief" if relief else "enter",
        omni_paused=omni_paused,
        main_mode=main_mode,
    )
    write_flag_file(genau_paused_file, True)
    write_flag_file(audio_paused_file, True)
    write_flag_file(main_player_paused_file, True)
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
    append_command(Path(genau_cmd_file), "PAUSE")
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
    main_player_paused_file: str | Path,
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
    if plan.resume_genau_playback:
        append_command(Path(genau_cmd_file), "RESUME")
    if plan.resume_main_player_playback:
        write_flag_file(main_player_paused_file, False)
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

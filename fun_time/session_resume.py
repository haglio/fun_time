"""Bring a reopened session back to the clip each player was on, and the state
that shaped its playlists: ``docs/resuming-a-session.md``."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields, replace
from pathlib import Path

from app_support.state_files import GENAU_DRIVE, GENAU_STATUS
from player_core.clip_advance import ClipAdvanceState
from player_core.drive_readout import read_drive
from player_core.file_channel import append_command
from player_core.player_verbs import LOCK_OFF, LOCK_ON, SET_SPEED
from player_core.playlist import PlaylistItem, read_playlist, write_playlist
from player_core.robot_hand import RobotHandState, WaveformShape
from player_core.status import PlayerStatus

from main_player.loop_verbs import SET_LOOP

from .media_metadata import normalize_path_key
from .modes import rotated_onto, source_roots
from .player_handover import take_back_the_list
from .player_status import MainPlayerStatus, read_genau_status
from .players import Player
from .shared_state import (
    BridgeState,
    SatelliteState,
    migrate_shared_state,
    read_shared_state,
    write_shared_state,
)

PlaylistEntries = list[PlaylistItem]

HAND_SPEED = "SPEED"
HAND_AMPLITUDE = "AMP"
HAND_CENTER = "CENTER"
CLIP_SECONDS = "CLIP_SECONDS"
CYCLE_SHAPE = "CYCLE_SHAPE"
CRUISE_ON = "CRUISE_ON"
LEARNED_ON = "LEARNED_ON"

# What a reopened session does NOT come back believing; everything else does.
# Which four of those it keeps without a file of their own, and what re-asserts
# each, is docs/resuming-a-session.md.
NOT_RESUMED = frozenset({
    "omni_paused",
    "active_player",
    "satellites_mode",
    "origenerator_ready",
    "majority",
})

NOT_RESUMED_PER_SATELLITE = frozenset({"nav_anchor"})

RESUMED_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(BridgeState) if field.name not in NOT_RESUMED
)

RESUMED_SATELLITE_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(SatelliteState) if field.name not in NOT_RESUMED_PER_SATELLITE
)


def _resumed_satellite(satellite: SatelliteState) -> SatelliteState:
    return SatelliteState(**{name: getattr(satellite, name) for name in RESUMED_SATELLITE_FIELDS})


def playlist_fits_sources(playlist_file: Path, sources: str) -> bool:
    """Whether every video in *playlist_file* comes from *sources*.

    A playlist is only ever built from the source spec of the session that built
    it, so an entry from outside this one's means the file was left by the OTHER
    app sharing this state dir.
    """
    roots = source_roots(sources)
    return all(
        any(_is_within(item.path, root) for root in roots)
        for item in read_playlist(playlist_file)
    )


def _is_within(video: Path, root: Path) -> bool:
    """Whether *video* is *root* itself or sits somewhere beneath it.

    Compared component by component, on the app's normalized path key: case and
    separator differ between a library dir and a playlist naming a file in it,
    and a component match also keeps ``.../VR_old`` out of ``.../VR``.
    """
    root_parts = [normalize_path_key(part) for part in root.parts]
    video_parts = [normalize_path_key(part) for part in video.parts]
    return video_parts[: len(root_parts)] == root_parts


def playlist_opens_on(playlist_file: Path, video: str) -> bool:
    """Whether the player handed *playlist_file* will load *video*, every player
    starting at the top.  Asked before the main player's loop is handed back."""
    return playlist_leads_with(read_playlist(playlist_file), video)


def playlist_leads_with(entries: PlaylistEntries, video: str) -> bool:
    """:func:`playlist_opens_on` asked of a playlist in hand, not one on disk."""
    return bool(entries) and normalize_path_key(str(entries[0].path)) == normalize_path_key(video)


def _surviving_entries(playlist_file: Path) -> PlaylistEntries:
    """Last session's playlist, minus clips trashed or pruned since: handing mpv
    a path to nothing is how a satellite comes up stuck."""
    return [item for item in read_playlist(playlist_file) if item.path.exists()]


def resume_playlists(resumptions: Sequence[tuple[Path, str]]) -> bool:
    """Rotate each playlist file onto the video its player last had on screen.

    *resumptions* pairs a playlist file with the video named in that player's
    status file; the answer is whether there was a session to come back to.
    """
    for playlist_file, _ in resumptions:
        take_back_the_list(playlist_file)
    rotated: list[tuple[Path, PlaylistEntries]] = []
    for playlist_file, last_video in resumptions:
        entries = _surviving_entries(playlist_file)
        if not entries:
            return False
        rotated.append((playlist_file, rotated_onto(entries, last_video)))
    for playlist_file, entries in rotated:
        write_playlist(playlist_file, entries)
    return True


def resume_main_video(playlist_file: Path, video: str) -> bool:
    """Rotate a just-REBUILT main playlist onto *video*; False when it is not in
    it, which is the other half of the cross-app rebuild (docs/entering-vr.md)."""
    entries = read_playlist(playlist_file)
    rotated = rotated_onto(entries, video)
    if not playlist_leads_with(rotated, video):
        return False
    write_playlist(playlist_file, rotated)
    return True


def resume_satellite_locks(locks: Sequence[tuple[Path, bool]]) -> None:
    """Queue LOCK_ON on the command file of each satellite that was locked."""
    for command_file, locked in locks:
        if locked:
            append_command(Path(command_file), LOCK_ON)


def _shape_steps(shape: str) -> list[str]:
    shapes = [kind.value for kind in WaveformShape]
    if shape not in shapes:
        return []
    opens_on = shapes.index(RobotHandState().shape.value)
    return [CYCLE_SHAPE] * ((shapes.index(shape) - opens_on) % len(shapes))


def resume_genau(genau_cmd_file: Path, state_dir: Path) -> None:
    state_dir = Path(state_dir)
    status = read_genau_status(state_dir / GENAU_STATUS)
    drive = read_drive(state_dir / GENAU_DRIVE)
    verbs: list[str] = []
    if drive is not None:
        hand, advance = RobotHandState(), ClipAdvanceState()
        verbs += [f"{verb} {value}" for verb, value, opens_at in (
            (HAND_SPEED, drive.speed, hand.speed),
            (HAND_AMPLITUDE, drive.amplitude, hand.amplitude),
            (HAND_CENTER, drive.center, hand.center),
            (CLIP_SECONDS, drive.advance_interval, advance.interval),
        ) if value != opens_at]
        verbs += _shape_steps(drive.shape)
    if status.cruise_active:
        verbs.append(CRUISE_ON)
    if status.learned_active:
        verbs.append(LEARNED_ON)
    if not status.locked:
        verbs.append(LOCK_OFF)
    for verb in verbs:
        append_command(Path(genau_cmd_file), verb)


def resume_main_lock(main_player_cmd_file: Path, *, locked: bool) -> None:
    if not locked:
        append_command(Path(main_player_cmd_file), LOCK_OFF)


def resume_rates(rates: Sequence[tuple[Path, float]]) -> None:
    for command_file, rate in rates:
        if rate != 1.0:
            append_command(Path(command_file), f"{SET_SPEED} {rate:g}")


def resume_what_lives_in_a_player(
    *,
    main_player: tuple[Path, MainPlayerStatus],
    satellites: Sequence[tuple[Path, PlayerStatus]],
    genau_cmd_file: Path,
    state_dir: Path,
) -> None:
    """What any reopen re-sends because it lives in a player: ``docs/entering-vr.md``."""
    main_player_cmd_file, main_player_status = main_player
    resume_rates([
        (main_player_cmd_file, main_player_status.speed),
        *((command_file, status.speed) for command_file, status in satellites),
    ])
    resume_main_lock(main_player_cmd_file, locked=main_player_status.locked)
    resume_genau(genau_cmd_file, state_dir)


def resume_main_loop(main_player_cmd_file: Path, status: MainPlayerStatus,
                     playlist_file: Path) -> None:
    """Queue SET_LOOP for the loop *status* was left running.

    Only when *playlist_file* really does lead with the clip that loop was cut
    from: a rebuild, or a clip deleted since, leaves another video leading, and
    those bounds would then mark out a stretch of a video nobody chose.
    """
    if not playlist_opens_on(playlist_file, status.video):
        return
    bounds = status.loop_bounds
    if bounds is not None:
        append_command(Path(main_player_cmd_file), f"{SET_LOOP} {bounds[0]} {bounds[1]}")


def resume_shared_state(state_file: Path, *, resumed: bool) -> BridgeState:
    """Seed *state_file* with the state a resumed session comes back in.

    Pass *resumed* as :func:`resume_playlists` reported it.
    """
    migrate_shared_state(state_file)
    previous = read_shared_state(state_file) if resumed else None
    state = BridgeState() if previous is None else replace(
        BridgeState(**{field: getattr(previous, field) for field in RESUMED_FIELDS}),
        portrait=_resumed_satellite(previous.satellite(Player.PORTRAIT)),
        landscape=_resumed_satellite(previous.satellite(Player.LANDSCAPE)),
    )
    write_shared_state(state_file, state)
    return state

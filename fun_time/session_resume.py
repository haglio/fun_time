from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from pathlib import Path

from app_support.file_channel import read_flag
from funestra.file_channel import append_command
from funestra.flick_advance import FlickAdvanceState
from funestra.funestra_verbs import LOCK_OFF, LOCK_ON, SET_SPEED, SET_VOLUME
from funestra.main_hud import OSR2_DRIVING, OSR2_PARKED, OSR2_RETRACTED
from funestra.playlist import PlaylistItem, read_playlist, write_playlist
from funestra.robot_hand import RobotHandState, WaveformShape

from main_funestra.loop_verbs import SET_LOOP

from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import take_back_the_list
from .funestra_settings import FunestraReports, with_what_the_funestras_report
from .main_list import MainList, MainListSettings, new_shuffle_seed
from .media_metadata import normalize_path_key
from .modes import rotated_onto
from .shared_state import (
    BridgeState,
    SatelliteState,
    migrate_shared_state,
    read_shared_state,
    write_shared_state,
    written_with,
)

logger = logging.getLogger(__name__)

PlaylistEntries = list[PlaylistItem]

HAND_SPEED = "SPEED"
HAND_AMPLITUDE = "AMP"
HAND_CENTER = "CENTER"
FLICK_SECONDS = "FLICK_SECONDS"
CYCLE_SHAPE = "CYCLE_SHAPE"
LEARNED_ON = "LEARNED_ON"

NOT_RESUMED = frozenset({
    "omni_paused",
    "active_funestra",
    "origenerator_ready",
    "majority",
})

NOT_RESUMED_PER_SATELLITE = frozenset({"nav_anchor", "handed_over"})

DESCRIBES_THE_PLAYLIST = frozenset({
    "locked", "filter", "favorites_filter", "latest", "loop", "nav_map_anchor", "widen_video",
})

RESUMED_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(BridgeState) if field.name not in NOT_RESUMED
)

RESUMED_SATELLITE_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(SatelliteState) if field.name not in NOT_RESUMED_PER_SATELLITE
)


def _resumed_satellite(satellite: SatelliteState, *, playlist_resumed: bool) -> SatelliteState:
    return SatelliteState(**{name: getattr(satellite, name) for name in RESUMED_SATELLITE_FIELDS
                             if playlist_resumed or name not in DESCRIBES_THE_PLAYLIST})


def playlist_opens_on(playlist_file: Path, video: str) -> bool:
    entries = read_playlist(playlist_file)
    return bool(entries) and normalize_path_key(str(entries[0].path)) == normalize_path_key(video)


def _surviving_entries(playlist_file: Path) -> PlaylistEntries:
    """Last session's playlist, minus videos trashed or pruned since: handing mpv
    a path to nothing is how a satellite comes up stuck."""
    return [item for item in read_playlist(playlist_file) if item.path.exists()]


def resume_playlists(resumptions: Sequence[tuple[Path, str]]) -> bool:
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


def open_the_main_list(main_list: MainList, carried: BridgeState, *, state_file: Path,
                       on_screen: str) -> BridgeState:
    videos = main_list.write(MainListSettings.of(carried), on_screen=on_screen).videos
    if not videos:
        carried = MainListSettings(shuffle_seed=carried.main_shuffle_seed).into(carried)
        write_shared_state(state_file, carried)
        videos = main_list.write(MainListSettings.of(carried), on_screen=on_screen).videos
    logger.info("The Main Funestra opens on the video it was left on"
                if videos[:1] and normalize_path_key(videos[0]) == normalize_path_key(on_screen)
                else "The Main Funestra opens at the top of its list")
    return carried


def resume_satellite_locks(locks: Sequence[tuple[Path, bool]]) -> None:
    for command_file, locked in locks:
        if locked:
            append_command(Path(command_file), LOCK_ON)


def _shape_steps(shape: str) -> list[str]:
    shapes = [kind.value for kind in WaveformShape]
    if shape not in shapes:
        return []
    opens_on = shapes.index(RobotHandState().shape.value)
    return [CYCLE_SHAPE] * ((shapes.index(shape) - opens_on) % len(shapes))


def resume_genau(genau_cmd_file: Path, carried: BridgeState) -> None:
    hand, advance = RobotHandState(), FlickAdvanceState()
    verbs = [f"{verb} {value}" for verb, value, opens_at in (
        (HAND_SPEED, carried.genau_speed, hand.speed),
        (HAND_AMPLITUDE, carried.genau_amplitude, hand.amplitude),
        (HAND_CENTER, carried.genau_center, hand.center),
        (FLICK_SECONDS, carried.genau_flick_seconds, advance.interval),
    ) if value != opens_at]
    verbs += _shape_steps(carried.genau_shape)
    if carried.genau_learned:
        verbs.append(LEARNED_ON)
    if not carried.genau_locked:
        verbs.append(LOCK_OFF)
    for verb in verbs:
        append_command(Path(genau_cmd_file), verb)


def resume_main_lock(main_funestra_cmd_file: Path, *, locked: bool) -> None:
    if not locked:
        append_command(Path(main_funestra_cmd_file), LOCK_OFF)


def resume_satellite_volumes(carried: BridgeState,
                             satellite_cmd_files: Mapping[FunTimeFunestra, Path]) -> None:
    opens_at = SatelliteState()
    for funestra, command_file in satellite_cmd_files.items():
        satellite = carried.satellite(funestra)
        if (satellite.volume, satellite.muted) != (opens_at.volume, opens_at.muted):
            append_command(Path(command_file), f"{SET_VOLUME} {satellite.volume} {int(satellite.muted)}")


def resume_rates(rates: Sequence[tuple[Path, float]]) -> None:
    for command_file, rate in rates:
        if rate != 1.0:
            append_command(Path(command_file), f"{SET_SPEED} {rate:g}")


def resume_what_lives_in_a_funestra(
    carried: BridgeState,
    *,
    main_funestra_cmd_file: Path,
    satellite_cmd_files: Mapping[FunTimeFunestra, Path],
    genau_cmd_file: Path,
) -> None:
    resume_rates([
        (main_funestra_cmd_file, carried.main_speed),
        *((command_file, carried.satellite(funestra).speed)
          for funestra, command_file in satellite_cmd_files.items()),
    ])
    resume_main_lock(main_funestra_cmd_file, locked=carried.main_locked)
    resume_satellite_volumes(carried, satellite_cmd_files)
    resume_genau(genau_cmd_file, carried)


def resume_main_loop(main_funestra_cmd_file: Path, carried: BridgeState,
                     playlist_file: Path) -> None:
    if carried.main_loop_out_ms <= carried.main_loop_in_ms:
        return
    if playlist_opens_on(playlist_file, carried.main_loop_video):
        append_command(Path(main_funestra_cmd_file),
                       f"{SET_LOOP} {carried.main_loop_in_ms} {carried.main_loop_out_ms}")


_OSR2_CONTROLS = (OSR2_DRIVING, OSR2_PARKED, OSR2_RETRACTED)


NOTHING_REPORTED = FunestraReports()


_RETIRED_MIC_OFF_FLAG = "mic_off.flag"


def _the_mic_was_left_off(state_dir: Path) -> bool:
    flag = state_dir / _RETIRED_MIC_OFF_FLAG
    was_off = read_flag(flag, default=False)
    flag.unlink(missing_ok=True)
    return was_off


def resume_shared_state(state_file: Path, *, resumed: bool,
                        last_reported: FunestraReports = NOTHING_REPORTED) -> BridgeState:
    migrate_shared_state(state_file)
    previous = read_shared_state(state_file)
    if previous is not None:
        if not written_with(state_file, "mic_muted"):
            previous = replace(previous, mic_muted=_the_mic_was_left_off(Path(state_file).parent))
        previous = with_what_the_funestras_report(previous, last_reported)
    state = BridgeState() if previous is None else replace(
        BridgeState(**{field: getattr(previous, field) for field in RESUMED_FIELDS}),
        portrait=_resumed_satellite(previous.satellite(FunTimeFunestra.PORTRAIT),
                                    playlist_resumed=resumed),
        landscape=_resumed_satellite(previous.satellite(FunTimeFunestra.LANDSCAPE),
                                     playlist_resumed=resumed),
    )
    if previous is None or not resumed:
        state = replace(state, main_shuffle_seed=new_shuffle_seed())
    if state.osr2_control not in _OSR2_CONTROLS:
        state = replace(state, osr2_control=OSR2_PARKED)
    write_shared_state(state_file, state)
    return state

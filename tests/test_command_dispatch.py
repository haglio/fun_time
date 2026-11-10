from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app_support import osr2_hold
from funestra.file_channel import append_command
from funestra.hud_placement import HudCorner, HudEdge
from funestra.hud_title import F_MODE_LABEL
from funestra.main_hud import OSR2_CONTROL_BUTTONS, OSR2_DRIVING, OSR2_PARKED, OSR2_RETRACTED
from funestra.modes import LengthMode, MainMode

from fun_time import genaumacher_save
from fun_time.bridge_records import BridgeConfig, Op, WindowOp
from fun_time.command_dispatch import dispatch_command
from fun_time.command_value import parse_command, say_command
from fun_time.content import Noun, load_content
from fun_time.crown import Crown
from fun_time.device_arbiter import TCODE_OFF
from fun_time.event_log import FAVORITE, MESSAGE, SOURCE_LANDSCAPE, SOURCE_MAIN, SOURCE_PORTRAIT
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.genaumacher_save import _genaumacher_project_dir
from fun_time.hosted_words import routes_to_origenerator
from fun_time.main_list import MainListSettings
from fun_time.main_list_builds import MainListBuild
from fun_time.media_metadata import (
    is_favorite,
    mark_favorite,
    metadata_path_for,
)
from fun_time.mode_plan import EnhancementLevel
from fun_time.origenerator_switches import RfbMode
from fun_time.satellite_groups import send_satellite
from fun_time.shared_state import BridgeState, SatelliteState
from fun_time.voice_commands import VOICE_COMMANDS, build_voice_commands
from main_funestra.genau_verbs import genau_f_mode
from tests.bridge_configs import made_up_bridge_config
from tests.hosted_shows import publish_the_show


def _make_config(tmp_path: Path, *, vr_main_funestra: bool = False) -> BridgeConfig:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    trash_dir = tmp_path / "trash"
    trash_dir.mkdir(exist_ok=True)
    return made_up_bridge_config(
        state_dir,
        vr_main_funestra=vr_main_funestra,
        trash_dir=trash_dir,
        main_sources=str(tmp_path / "primary"),
        broker_cmd_file=state_dir / "broker_cmd.txt",
    )


def _set_current(config: BridgeConfig, funestra: FunTimeFunestra, video: str, *, locked: bool = False,
                 playlist_length: int = 0) -> None:
    status = config.satellite(funestra).status_file
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(
        f"video={video}\nposition_ms=100\nduration_ms=1000\n"
        f"paused=0\nlocked={'1' if locked else '0'}\nplaylist_length={playlist_length}\n",
        encoding="utf-8",
    )


def _cmds(config: BridgeConfig, funestra: FunTimeFunestra) -> list[str]:
    cmd_file = config.satellite(funestra).cmd_file
    if not cmd_file.exists():
        return []
    return [line.strip() for line in cmd_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _playlist(config: BridgeConfig, funestra: FunTimeFunestra) -> list[str]:
    playlist = config.satellite(funestra).playlist_file
    if not playlist.exists():
        return []
    return [
        line.split("\t")[0]
        for line in playlist.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _make_state(**overrides) -> BridgeState:
    defaults = {"main_mode": MainMode.KINO, "omni_paused": False}
    defaults.update(overrides)
    return BridgeState(**defaults)


def test_primary_prev_in_kino_mode_writes_main_funestra_cmd(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("main_prev", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "PREV\n"


def test_primary_next_in_kino_mode_writes_main_funestra_cmd(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("main_next", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "NEXT\n"


def test_primary_next_in_main_funestra_mode_writes_main_funestra_cmd(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("main_next", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "NEXT\n"


def test_primary_prev_and_next_step_genaus_flick_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    dispatch_command("main_prev", state, config)
    dispatch_command("main_next", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "PREV\nNEXT\n"
    assert not config.main_funestra_cmd_file.exists()


def test_primary_lock_toggles_the_main_funestras_hold_on_the_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("main_lock", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "TOGGLE_LOCK\n"


def test_the_spoken_forms_name_the_state_they_want(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("main_lock_on", state, config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "LOCK_ON\n"

    dispatch_command("main_lock_off", state, config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "LOCK_ON\nLOCK_OFF\n"


def test_a_main_lock_queues_behind_the_osr2_handoff_verb_in_the_same_file(tmp_path: Path):
    config = _make_config(tmp_path)
    append_command(config.main_funestra_cmd_file, TCODE_OFF)

    dispatch_command("main_lock_on", _make_state(main_mode=MainMode.KINO), config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == f"{TCODE_OFF}\nLOCK_ON\n"


def test_primary_lock_reaches_genau_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    dispatch_command("main_lock", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "TOGGLE_LOCK\n"
    assert not config.main_funestra_cmd_file.exists()


def test_the_spoken_forms_follow_the_mode_too(tmp_path: Path):
    config = _make_config(tmp_path)

    dispatch_command("main_lock_off", _make_state(main_mode=MainMode.GENAU), config)
    assert config.genau_cmd_file.read_text(encoding="utf-8") == "LOCK_OFF\n"

    dispatch_command("main_lock_on", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "LOCK_ON\n"


def _show_on_genau(config: BridgeConfig, flick: Path, *, locked: bool) -> None:
    config.genau_status_file.write_text(
        f"flick={flick}\nlocked={'1' if locked else '0'}\n", encoding="utf-8")


def test_a_lock_on_genaus_flick_follows_what_genau_holds(tmp_path: Path):
    config = _make_config(tmp_path)
    _show_on_genau(config, tmp_path / "flicks" / "made up flick.mp4", locked=False)

    _state, ops = dispatch_command("main_lock", _make_state(main_mode=MainMode.GENAU), config)

    assert ops == [WindowOp(op=Op.FOLLOW_THE_LOCK, key=MainMode.GENAU)]


def test_letting_genaus_flick_go_follows_nothing(tmp_path: Path):
    config = _make_config(tmp_path)
    config.regen_metadata_root = tmp_path / "metadata"
    flick = tmp_path / "flicks" / "made up flick.mp4"
    _show_on_genau(config, flick, locked=True)

    _state, ops = dispatch_command("main_lock", _make_state(main_mode=MainMode.GENAU), config)

    assert ops == []
    assert not is_favorite(flick, config.regen_metadata_root)


def test_marking_genaus_flick_trash_takes_it_out_of_the_favorites_as_a_satellites_does(
        tmp_path: Path):
    config = _make_config(tmp_path)
    config.regen_metadata_root = tmp_path / "metadata"
    flick = tmp_path / "flicks" / "made up flick.mp4"
    _show_on_genau(config, flick, locked=True)
    mark_favorite(flick, config.regen_metadata_root, True)

    dispatch_command("genau_trash_flick", _make_state(main_mode=MainMode.GENAU), config)

    assert not is_favorite(flick, config.regen_metadata_root)


@pytest.mark.parametrize(("command", "main_mode"), [
    ("main_lock", MainMode.KINO),
    ("main_lock", MainMode.GENAU),
])
def test_locking_the_primary_makes_it_the_satellite_a_bare_command_reaches(
        tmp_path: Path, command, main_mode):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=main_mode, active_funestra=2)

    state, _ops = dispatch_command(command, state, config)

    assert state.active_funestra == 1


@pytest.mark.parametrize(("command", "verb"), [
    ("projection_cycle", "CYCLE_PROJECTION"),
    ("projection_cycle_back", "CYCLE_PROJECTION_BACK"),
    ("recenter_view", "RECENTER"),
    ("tilt_up", "TILT_UP"),
    ("tilt_down", "TILT_DOWN"),
    ("tilt_reset", "TILT_RESET"),
    ("main_scene_prev", "PREV_SCENE"),
    ("main_scene_next", "NEXT_SCENE"),
    ("vr_reset", "LAYOUT_RESET"),
])
def test_a_vr_only_verb_reaches_the_vr_main_funestra(command, verb, tmp_path: Path):
    config = _make_config(tmp_path, vr_main_funestra=True)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command(command, state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == f"{verb}\n"


@pytest.mark.parametrize(
    "command",
    ["projection_cycle", "projection_cycle_back", "recenter_view", "tilt_up", "tilt_down",
     "tilt_reset", "main_scene_prev", "main_scene_next", "vr_reset"],
)
@pytest.mark.parametrize("main_mode", ["kino", "genau"])
def test_a_vr_only_verb_is_ignored_in_a_desktop_session(command, main_mode, tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=main_mode)

    _state, ops = dispatch_command(command, state, config)

    assert not config.main_funestra_cmd_file.exists()
    assert ops == [WindowOp(op=Op.IGNORED, key="outside VR")]


def test_main_funestra_cycle_version_writes_main_funestra_cmd(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("main_funestra_cycle_version", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "CYCLE_VERSION\n"


def test_main_funestra_cycle_version_back_writes_the_other_way(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode="kino")

    dispatch_command("main_funestra_cycle_version_back", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "CYCLE_VERSION_BACK\n"


def _on_screen(config: BridgeConfig, video: str, **status: str) -> None:
    config.main_funestra_status_file.parent.mkdir(parents=True, exist_ok=True)
    config.main_funestra_status_file.write_text(
        "".join(f"{key}={value}\n" for key, value in {"video": video, **status}.items()),
        encoding="utf-8")


@pytest.mark.parametrize(("command", "length"), [
    ("main_funestra_length_clips", LengthMode.CLIPS),
    ("main_funestra_length_full", LengthMode.FULL),
    ("main_funestra_length_mixed", LengthMode.MIXED),
])
def test_a_length_button_narrows_the_one_main_list_from_the_video_on_screen(
        tmp_path: Path, command, length):
    config, builds = _recording(_make_config(tmp_path))
    _on_screen(config, "C:/v/scene one.mp4")
    state = _make_state(main_length_mode=LengthMode.NONE if length is LengthMode.MIXED
                        else LengthMode.MIXED)

    state, _ops = dispatch_command(command, state, config)

    assert state.main_length_mode is length
    assert [(build.settings.length, build.on_screen) for build in builds.asked] == [
        (length, "C:/v/scene one.mp4")]
    assert not config.main_funestra_cmd_file.exists()


def test_naming_the_length_already_running_asks_for_nothing(tmp_path: Path):
    config, builds = _recording(_make_config(tmp_path))

    _state, ops = dispatch_command("main_funestra_length_mixed", _make_state(), config)

    assert (builds.asked, ops) == ([], [])


def test_inside_a_compilation_naming_the_length_running_is_the_way_back_out(tmp_path: Path):
    config, builds = _recording(_make_config(tmp_path))
    _on_screen(config, "C:/v/clip one.mp4", compilation="Vol6")

    dispatch_command("main_funestra_length_mixed", _make_state(), config)

    assert [build.settings.length for build in builds.asked] == [LengthMode.MIXED]


def test_end_compilation_writes_end_compilation(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("main_funestra_end_compilation", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "END_COMPILATION\n"


def test_a_length_button_is_ignored_while_the_main_funestra_is_off_screen(tmp_path: Path):
    config, builds = _recording(_make_config(tmp_path))

    state, ops = dispatch_command("main_funestra_length_clips", _make_state(main_mode=MainMode.GENAU),
                                  config)

    assert state.main_length_mode is LengthMode.MIXED
    assert builds.asked == []
    assert not config.main_funestra_cmd_file.exists()
    assert [op.op for op in ops] == [Op.IGNORED]


def test_compilation_writes_play_compilation(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_compilation", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "PLAY_COMPILATION\n"


def test_full_vid_writes_play_full_vid(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_full_vid", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "PLAY_FULL_VID\n"


def test_video_jump_writes_play_video_jump(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_clip_jump", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "PLAY_CLIP_JUMP\n"


def test_funscript_jump_writes_jump_to_funscript(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_funscript_jump", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "JUMP_TO_FUNSCRIPT\n"


def test_funscript_jump_is_refused_while_a_satellite_has_the_osr2(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO, osr2_funestra=FunTimeFunestra.PORTRAIT)

    _, ops = dispatch_command("main_funestra_funscript_jump", state, config)

    assert not config.main_funestra_cmd_file.exists()
    assert ops == [WindowOp(op="message", key="The Main Funestra doesn't have the OSR2",
                            level=logging.WARNING)]


def test_next_funscripted_writes_next_funscripted(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_next_funscripted", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "NEXT_FUNSCRIPTED\n"


def test_video_nav_inert_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_compilation", _make_state(main_mode=MainMode.GENAU), config)
    assert not config.main_funestra_cmd_file.exists()


def test_a_funscript_jump_is_ignored_while_the_main_funestra_is_off_screen(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_next_funscripted", _make_state(main_mode=MainMode.GENAU), config)
    assert not config.main_funestra_cmd_file.exists()


def test_video_nav_voice_phrases():
    assert VOICE_COMMANDS["compilation"] == "main_funestra_compilation"
    assert VOICE_COMMANDS["full video"] == "main_funestra_full_vid"

    for phrase in load_content()["clip_jump_phrases"]:
        assert VOICE_COMMANDS[phrase] == "main_funestra_clip_jump"


def test_funscript_nav_voice_phrases_are_split_for_vosk():
    assert VOICE_COMMANDS["jump to fun script"] == "active_funscript_jump"
    assert VOICE_COMMANDS["next fun scripted"] == "active_next_funscripted"
    assert VOICE_COMMANDS["main jump to fun script"] == "main_funestra_funscript_jump"
    assert VOICE_COMMANDS["next fun scripted main"] == "main_funestra_next_funscripted"
    assert VOICE_COMMANDS["portrait jump to fun script"] == "portrait_funscript_jump"
    assert VOICE_COMMANDS["satellites next fun scripted"] == "satellites_next_funscripted"
    assert "jump to funscript" not in VOICE_COMMANDS


@pytest.mark.parametrize("command", [
    "portrait_more_seeds",
    "landscape_wrong_action",
    "portrait_action_loop",
    "landscape_loop",
    "portrait_lock_action",
    "landscape_cycle_seed",
])
def test_a_satellite_with_no_video_on_screen_ignores_video_scoped_commands(
    tmp_path: Path, command: str,
):
    config = _make_config(tmp_path)

    _state, ops = dispatch_command(command, _make_state(), config)

    assert ops == []
    assert _cmds(config, 2) == []
    assert _cmds(config, 3) == []


def test_a_fresh_session_addresses_the_main_funestra_on_the_screen_he_looks_at_first():
    assert BridgeState().active_funestra == FunTimeFunestra.MAIN


def test_portrait_command_sets_active_funestra_to_portrait(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(active_funestra=3)

    new_state, _ops = dispatch_command("portrait_next", state, config)

    assert new_state.active_funestra == 2


def test_landscape_command_sets_active_funestra_to_landscape(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, _ops = dispatch_command("landscape_next", _make_state(active_funestra=2), config)

    assert new_state.active_funestra == 3


def test_primary_nav_sets_active_funestra_to_primary(tmp_path: Path):
    config = _make_config(tmp_path)

    for command in ("main_next", "main_prev"):
        new_state, _ops = dispatch_command(command, _make_state(active_funestra=3), config)
        assert new_state.active_funestra == 1, command


def test_a_bare_f_mode_follows_the_funestra_whose_f_mode_was_named_last(tmp_path: Path):
    config = _make_config(tmp_path)

    for suffix in ("", "_on", "_off"):
        bare = parse_command(f"active_fmode{suffix}")
        new_state, _ops = dispatch_command(
            f"main_fmode{suffix}", _make_state(active_funestra=3), config)
        assert new_state.active_funestra == 1, suffix
        assert bare.aimed_at_the_active(new_state.active_funestra).text == f"main_fmode{suffix}"
        new_state, _ops = dispatch_command(
            f"portrait_fmode{suffix}", _make_state(active_funestra=1), config)
        assert new_state.active_funestra == 2, suffix
        assert bare.aimed_at_the_active(new_state.active_funestra).text == f"portrait_fmode{suffix}"


def test_naming_a_funestras_playback_speed_makes_it_the_active_one(tmp_path: Path):
    config = _make_config(tmp_path)

    for command in ("main_funestra_speed_up", "main_funestra_speed_down", "main_funestra_speed_150"):
        new_state, _ops = dispatch_command(command, _make_state(active_funestra=3), config)
        assert new_state.active_funestra == 1, command
    new_state, _ops = dispatch_command("portrait_speed_50", _make_state(active_funestra=1), config)
    assert new_state.active_funestra == 2


def test_crowning_the_portrait_funestra_takes_the_crown_from_the_main_funestra(tmp_path: Path):
    new_state, ops = dispatch_command(
        "portrait_crown", _make_state(crowned=Crown.MAIN), _make_config(tmp_path))

    assert new_state.crowned is Crown.PORTRAIT
    assert ops == []


def test_crowning_the_main_funestra_takes_the_crown_back_from_the_portrait_funestra(tmp_path: Path):
    new_state, _ops = dispatch_command(
        "main_crown", _make_state(crowned=Crown.PORTRAIT), _make_config(tmp_path))

    assert new_state.crowned is Crown.MAIN


def test_a_funestras_own_minimize_parks_that_window_alone_not_all_of_fun_time(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(hud_minimized=True))

    new_state, ops = dispatch_command("portrait_minimize", state, config)

    assert ops == [WindowOp(op="minimize_window", key="portrait")]
    assert new_state.satellite(FunTimeFunestra.PORTRAIT) == state.satellite(FunTimeFunestra.PORTRAIT)
    assert _cmds(config, 2) == []


def test_minimize_names_each_satellite_its_own_window(tmp_path: Path):
    config = _make_config(tmp_path)

    _state, ops = dispatch_command("landscape_minimize", _make_state(), config)

    assert ops == [WindowOp(op="minimize_window", key="landscape")]


def test_the_main_funestras_button_parks_its_window_in_either_mode(tmp_path: Path):
    config = _make_config(tmp_path)

    for mode in ("genau", "kino"):
        _state, ops = dispatch_command(
            "main_minimize", _make_state(main_mode=mode), config)
        assert ops == [WindowOp(op="minimize_window", key="main_funestra")], mode


def test_omnipause_toggle_enters_pause_from_unpaused(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert new_state.omni_paused is True
    assert any(op.op == "suspend_hotkeys" for op in ops)
    assert config.portrait.paused_file.read_text(encoding="utf-8") == "1"
    assert config.landscape.paused_file.read_text(encoding="utf-8") == "1"


def test_omnipause_toggle_leaves_pause_from_paused(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert new_state.omni_paused is False
    assert any(op.op == "unsuspend_hotkeys" for op in ops)
    assert config.portrait.paused_file.read_text(encoding="utf-8") == "0"
    assert config.landscape.paused_file.read_text(encoding="utf-8") == "0"
    assert config.main_funestra_paused_file.read_text(encoding="utf-8") == "0"


def _fmode_result(**kwargs):
    funestras = tuple(kwargs["funestras"])
    enabled = kwargs["enabled"]
    return type("R", (), {
        "funestras": funestras,
        "enabled": enabled,
        "log_message": f"F-mode {'enabled' if enabled else 'disabled'}: "
                       f"{', '.join(funestra.label for funestra in funestras)}",
    })()


def _dispatch_fmode(command: str, state: BridgeState, config: BridgeConfig):
    with patch("fun_time.f_mode.apply_fmode", side_effect=_fmode_result) as mock_fmode:
        new_state, ops = dispatch_command(command, state, config)
    return new_state, ops, mock_fmode


def test_the_main_funestra_named_goes_into_f_mode_alone(tmp_path: Path):
    config = _make_config(tmp_path)
    for funestra in FunTimeFunestra.SATELLITES:
        publish_the_show(config, funestra, f_mode=False)

    new_state, _ops, mock_fmode = _dispatch_fmode("main_fmode", _make_state(), config)

    assert new_state.main_scripted_filter is True
    assert mock_fmode.call_args.kwargs["funestras"] == (FunTimeFunestra.MAIN,)
    assert _origenerator_cmds(config) == []


def test_f_mode_on_genau_keeps_its_favorites_and_leaves_kinos_list_alone(tmp_path: Path):
    config = _make_config(tmp_path)

    state, ops, mock_fmode = _dispatch_fmode("main_fmode", _make_state(main_mode=MainMode.GENAU), config)

    assert (state.genau_favorites_filter, state.main_scripted_filter) == (True, False)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == f"{genau_f_mode(True)}\n"
    assert mock_fmode.call_args.kwargs["funestras"] == ()
    assert ops == [WindowOp(op="message", key=f"{F_MODE_LABEL} enabled", source=SOURCE_MAIN, level=FAVORITE)]


def test_f_mode_on_or_off_misheard_twice_still_lands_where_it_was_asked(tmp_path: Path):
    config = _make_config(tmp_path)

    stays_on, _ops, _mock = _dispatch_fmode(
        "main_fmode_on", _make_state(main_scripted_filter=True), config)
    stays_off, _ops, _mock = _dispatch_fmode(
        "main_fmode_off", _make_state(main_scripted_filter=False), config)

    assert stays_on.main_scripted_filter is True
    assert stays_off.main_scripted_filter is False


def test_f_mode_on_said_twice_rebuilds_and_so_reshuffles_nothing(tmp_path: Path):
    config = _make_config(tmp_path)

    _state, _ops, mock_fmode = _dispatch_fmode(
        "main_fmode_on", _make_state(main_scripted_filter=True), config)

    assert mock_fmode.call_args.kwargs["funestras"] == ()


@pytest.mark.parametrize(("way_in", "source"), [
    ("fmode_toggle", "system"),
    ("main_fmode", "main"),
])
def test_key_hud_button_and_spoken_phrase_flash_the_same_f_mode_confirmation(
        tmp_path: Path, way_in: str, source: str):
    config = _make_config(tmp_path)

    _state, ops, _mock = _dispatch_fmode(way_in, _make_state(), config)

    assert ops == [WindowOp(op="message", key=f"{F_MODE_LABEL} enabled", source=source,
                            level=FAVORITE)]


def test_f_mode_off_is_neither_an_error_nor_a_favoriting_and_flashes_white(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_scripted_filter=True)

    _state, ops, _mock = _dispatch_fmode("fmode_toggle", state, config)

    assert ops == [
        WindowOp(op="message", key=f"{F_MODE_LABEL} disabled", source="system", level=MESSAGE),
    ]


def test_fmode_passes_the_metadata_root_for_group_collapse(tmp_path: Path):
    config = replace(_make_config(tmp_path), regen_metadata_root=tmp_path / "metadata")

    _state, _ops, mock_fmode = _dispatch_fmode("fmode_toggle", _make_state(), config)

    assert mock_fmode.call_args.kwargs["main_build"].main_list.metadata_root == tmp_path / "metadata"


def test_a_video_a_lock_sends_to_a_satellite_screen_carries_its_funscript(tmp_path: Path):
    config = _make_config(tmp_path)
    video = tmp_path / "videos" / "videos" / "portrait" / "pick_me.mp4"
    script = tmp_path / "videos" / "scripts" / "scripts" / "portrait" / "pick_me.funscript"
    script.parent.mkdir(parents=True)
    script.write_text("{}", encoding="utf-8")

    dispatch_command("portrait_lock_on", _make_state(), config, target_path=str(video))

    assert _cmds(config, 2) == [f"PLAY_FILE {video}\t{script}", "LOCK_ON"]


def test_landscape_nav_sets_the_active_funestra(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    new_state, _ops = dispatch_command("landscape_nav_down", state, config)

    assert new_state.active_funestra == 3


def _publish_status(status_file: Path, **fields) -> None:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text("".join(f"{key}={value}\n" for key, value in
                                   {"video": "C:/v/a.mp4", **fields}.items()),
                           encoding="utf-8")


def test_a_switch_brings_the_main_funestras_window_back_and_hands_it_the_focus(tmp_path: Path):
    config = _make_config(tmp_path)

    for from_mode, command, to_mode in (
            (MainMode.GENAU, "main_kino_activate", MainMode.KINO),
            (MainMode.KINO, "genau_activate", MainMode.GENAU)):
        new_state, ops = dispatch_command(command, _make_state(main_mode=from_mode), config)

        assert new_state.main_mode is to_mode
        assert ops == [
            WindowOp(op="show_window", key="main_funestra"),
            WindowOp(op="activate_window", key="main_funestra"),
        ]


def test_a_mode_switch_tells_the_main_funestra_which_of_kino_and_genau_has_its_window(tmp_path: Path):
    config = _make_config(tmp_path)

    dispatch_command("genau_activate", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines() == ["SHOW genau"]

    config.main_funestra_cmd_file.unlink()
    dispatch_command("main_kino_activate", _make_state(main_mode=MainMode.GENAU), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines() == ["SHOW kino"]


def test_genau_speed_down_writes_cmd_file_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    new_state, ops = dispatch_command("robot_hand_speed_down", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_DOWN\n"
    assert new_state == state
    assert ops == []


def test_the_robot_hand_at_work_in_both_modes_takes_its_rate_keys_in_both(tmp_path: Path):
    for mode in ("kino", "genau"):
        config = _make_config(tmp_path / mode)
        state = _make_state(main_mode=mode)

        new_state, ops = dispatch_command("robot_hand_speed_up", state, config)

        assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_UP\n", mode
        assert not config.main_funestra_cmd_file.exists()
        assert new_state == state
        assert ops == []


def _set_main_funestra_driving(config, *, driving: bool) -> None:
    config.main_funestra_status_file.write_text(
        f"has_funscript={'1' if driving else '0'}\nfunscript_resting=0\n",
        encoding="utf-8",
    )


def test_the_motion_rate_never_reaches_the_videos_playback_rate(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_main_funestra_driving(config, driving=True)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("robot_hand_speed_up", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_UP\n"
    assert not config.main_funestra_cmd_file.exists()


def test_the_bare_nudge_routes_to_main_funestra_in_kino_mode_while_the_funscript_drives(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_main_funestra_driving(config, driving=True)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("speed_up", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SPEED_UP\n"
    assert not config.genau_cmd_file.exists()


def test_the_bare_nudge_routes_to_genau_in_kino_mode_while_genau_drives(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_main_funestra_driving(config, driving=False)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("speed_down", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_DOWN\n"
    assert not config.main_funestra_cmd_file.exists()


def test_the_bare_nudge_reaches_the_video_while_the_osr2_is_held(tmp_path: Path):
    config = _make_config(tmp_path)

    dispatch_command("speed_up",
                     _make_state(main_mode=MainMode.KINO, osr2_control=OSR2_PARKED), config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SPEED_UP\n"
    assert not config.genau_cmd_file.exists()


def test_the_bare_nudge_follows_the_only_engine_running_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path / "genau")
    dispatch_command("speed_up", _make_state(main_mode=MainMode.GENAU), config)
    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_UP\n"
    assert not config.main_funestra_cmd_file.exists()


def test_the_main_funestras_min_and_max_route_to_the_active_engine(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_speed_min", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SET_SPEED min\n"

    config = _make_config(tmp_path)
    dispatch_command("main_funestra_speed_max", _make_state(main_mode=MainMode.GENAU), config)
    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED 100\n"


def test_main_funestra_multiplier_sets_main_funestra_speed(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_speed_150", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SET_SPEED 1.5\n"


def test_main_funestra_speed_up_down_nudge_the_video_rate_where_main_funestra_is_on_screen(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_speed_up", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SPEED_UP\n"
    assert not config.genau_cmd_file.exists()


@pytest.mark.parametrize("command", [
    "main_funestra_speed_up", "main_funestra_speed_down", "main_funestra_speed_150"])
def test_the_main_funestras_video_rate_is_ignored_in_genau_mode(tmp_path: Path, command: str):
    config = _make_config(tmp_path)

    _state, ops = dispatch_command(command, _make_state(main_mode=MainMode.GENAU), config)

    assert not config.main_funestra_cmd_file.exists()
    assert not config.genau_cmd_file.exists()
    assert ops == [WindowOp(op=Op.IGNORED, key="in Genau mode")]


def test_naming_the_playback_reaches_the_video_while_genau_holds_the_osr2(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_main_funestra_driving(config, driving=False)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("main_funestra_speed_down", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SPEED_DOWN\n"
    assert not config.genau_cmd_file.exists()


def test_absolute_speed_reaches_main_funestra_video_in_kino_mode_even_when_genau_drives(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_main_funestra_driving(config, driving=False)
    state = _make_state(main_mode=MainMode.KINO)

    dispatch_command("main_funestra_speed_150", state, config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SET_SPEED 1.5\n"
    assert not config.genau_cmd_file.exists()


def test_the_main_funestras_max_sets_its_video_in_kino_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_main_funestra_driving(config, driving=False)
    dispatch_command("main_funestra_speed_max", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SET_SPEED max\n"


def test_reset_speed_command_maps_to_normal_rate(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_speed_100", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SET_SPEED 1\n"


def test_a_satellites_speed_pair_steps_that_satellite_alone(tmp_path: Path):
    config = _make_config(tmp_path)

    dispatch_command("portrait_speed_up", _make_state(), config)
    dispatch_command("landscape_speed_down", _make_state(), config)

    assert config.portrait.cmd_file.read_text(encoding="utf-8") == "SPEED_UP\n"
    assert config.landscape.cmd_file.read_text(encoding="utf-8") == "SPEED_DOWN\n"
    assert not config.main_funestra_cmd_file.exists()


def test_a_rate_named_for_a_satellite_is_set_on_that_satellite_alone(tmp_path: Path):
    config = _make_config(tmp_path)

    dispatch_command("portrait_speed_50", _make_state(), config)
    dispatch_command("landscape_speed_200", _make_state(), config)

    assert config.portrait.cmd_file.read_text(encoding="utf-8") == "SET_SPEED 0.5\n"
    assert config.landscape.cmd_file.read_text(encoding="utf-8") == "SET_SPEED 2\n"
    assert not config.main_funestra_cmd_file.exists()


def test_either_end_of_the_range_named_for_a_satellite_is_set_on_that_satellite_alone(tmp_path: Path):
    config = _make_config(tmp_path)

    dispatch_command("portrait_speed_min", _make_state(), config)
    dispatch_command("landscape_speed_max", _make_state(), config)

    assert config.portrait.cmd_file.read_text(encoding="utf-8") == "SET_SPEED min\n"
    assert config.landscape.cmd_file.read_text(encoding="utf-8") == "SET_SPEED max\n"
    assert not config.main_funestra_cmd_file.exists()


def test_volume_down_steps_both_audio_sinks_down(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, ops = dispatch_command("audio_volume_down", _make_state(), config)

    assert new_state.volume == 90
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 90 0"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "90"
    assert ops == [WindowOp(op="message", key="Volume 90%", source="main")]


def test_volume_up_steps_both_audio_sinks_up(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, _ops = dispatch_command("audio_volume_up", _make_state(volume=40), config)

    assert new_state.volume == 50
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 50 0"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "50"


def test_volume_clamps_at_silent_and_at_full(tmp_path: Path):
    config = _make_config(tmp_path)

    floored, _ops = dispatch_command("audio_volume_down", _make_state(volume=5), config)
    assert floored.volume == 0

    ceilinged, _ops = dispatch_command("audio_volume_up", _make_state(volume=95), config)
    assert ceilinged.volume == 100


def test_the_main_funestra_is_told_muted_apart_from_turned_all_the_way_down(tmp_path: Path):
    config = _make_config(tmp_path)

    dispatch_command("audio_mute", _make_state(volume=70), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 70 1"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "0", "the sink stays dumb"

    dispatch_command("audio_set_volume|0", _make_state(volume=70), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").split("\n")[1] == "SET_VOLUME 0 0"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "0", "the sink stays dumb"


def test_setting_the_volume_outright_lands_on_both_sinks(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, ops = dispatch_command(
        "audio_set_volume|35", _make_state(volume=70, muted=True), config)

    assert (new_state.volume, new_state.muted) == (35, False)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 35 0"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "35"
    assert ops == [WindowOp(op="message", key="Volume 35%", source="main")]


def test_both_funestras_that_draw_the_level_are_told_it_a_hidden_genau_too(tmp_path: Path):
    config = _make_config(tmp_path)

    dispatch_command("audio_mute", _make_state(volume=70, main_mode=MainMode.KINO), config)

    assert config.genau_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 70 1"
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 70 1"


def test_setting_the_volume_clamps_and_ignores_nonsense(tmp_path: Path):
    config = _make_config(tmp_path)

    assert dispatch_command("audio_set_volume|400", _make_state(), config)[0].volume == 100
    assert dispatch_command("audio_set_volume|-5", _make_state(), config)[0].volume == 0
    unchanged, _ops = dispatch_command("audio_set_volume|loud", _make_state(volume=70), config)
    assert unchanged.volume == 70


def test_the_max_intensity_slider_reaches_every_funestra_that_can_drive_the_device(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, ops = dispatch_command("max_intensity_35", _make_state(), config)

    assert new_state.max_intensity == 35
    for command_file in (config.genau_cmd_file, config.main_funestra_cmd_file,
                         config.portrait.cmd_file, config.landscape.cmd_file):
        assert command_file.read_text(encoding="utf-8").strip() == "SET_MAX_INTENSITY 35"
    assert ops == [WindowOp(op="message", key="Max intensity 35%", source="main")]


def test_the_max_intensity_clamps_and_ignores_nonsense(tmp_path: Path):
    config = _make_config(tmp_path)

    assert dispatch_command("max_intensity_400", _make_state(), config)[0].max_intensity == 100
    assert dispatch_command("max_intensity_-5", _make_state(), config)[0].max_intensity == 0
    unchanged, _ops = dispatch_command("max_intensity_loud", _make_state(max_intensity=70), config)
    assert unchanged.max_intensity == 70


def test_mute_silences_both_sinks_and_remembers_the_level(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, ops = dispatch_command("audio_mute", _make_state(volume=70), config)

    assert new_state.muted is True
    assert new_state.volume == 70
    assert config.audio_volume_file.read_text(encoding="utf-8") == "0"
    assert ops == [WindowOp(op="message", key="Muted", source="main")]


def test_unmute_restores_the_level_the_mute_interrupted(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, ops = dispatch_command(
        "audio_unmute", _make_state(volume=70, muted=True), config,
    )

    assert new_state.muted is False
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 70 0"
    assert ops == [WindowOp(op="message", key="Volume 70%", source="main")]


def test_mute_or_unmute_misheard_twice_still_lands_where_it_was_asked(tmp_path: Path):
    config = _make_config(tmp_path)

    still_muted, _ops = dispatch_command("audio_mute", _make_state(muted=True), config)
    assert still_muted.muted is True

    still_audible, _ops = dispatch_command("audio_unmute", _make_state(muted=False), config)
    assert still_audible.muted is False


def test_a_louder_said_while_muted_is_heard_rather_than_seeming_missed(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, _ops = dispatch_command(
        "audio_volume_up", _make_state(volume=70, muted=True), config,
    )

    assert new_state.muted is False
    assert new_state.volume == 80
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 80 0"


def test_genau_next_flick_writes_cmd_file_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    _new_state, _ops = dispatch_command("genau_next_flick", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "NEXT\n"


def test_genau_cycle_shape_prev_writes_cmd_file_when_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    new_state, ops = dispatch_command("robot_hand_cycle_shape_prev", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "CYCLE_SHAPE_PREV\n"
    assert new_state == state
    assert ops == []


@pytest.mark.parametrize(("command", "verb"), [
    ("robot_hand_toggle_learned", "TOGGLE_LEARNED"),
    ("robot_hand_learned_on", "LEARNED_ON"),
    ("robot_hand_learned_off", "LEARNED_OFF"),
])
def test_genau_learned_motion_verbs_write_cmd_file(tmp_path: Path, command, verb):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    new_state, ops = dispatch_command(command, state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == f"{verb}\n"
    assert new_state == state
    assert ops == []


class TestOsr2ControlState:
    def test_each_button_leaves_the_session_in_the_state_it_names(self, tmp_path: Path):
        config = _make_config(tmp_path)

        for state_name, command in OSR2_CONTROL_BUTTONS.items():
            after, ops = dispatch_command(command, _make_state(), config)

            assert after.osr2_control == state_name, command
            assert ops == []

    def test_a_park_tells_the_broker_alone_and_leaves_the_engines_to_the_arbiter(self, tmp_path: Path):
        config = _make_config(tmp_path)

        _after, ops = dispatch_command("robot_hand_park", _make_state(), config)

        assert config.broker_cmd_file.read_text(encoding="utf-8").split() == [osr2_hold.PARK]
        assert not config.genau_cmd_file.exists()
        assert ops == []

    def test_driving_again_calls_the_brokers_hold_off(self, tmp_path: Path):
        config = _make_config(tmp_path)
        parked, _ops = dispatch_command("robot_hand_park", _make_state(), config)

        after, _ops = dispatch_command("robot_hand_release", parked, config)

        assert after.osr2_control == OSR2_DRIVING
        assert config.broker_cmd_file.read_text(encoding="utf-8").split() == [
            osr2_hold.PARK, osr2_hold.RESUME]
        assert not config.genau_cmd_file.exists()


class TestTakingTheOsr2:
    @pytest.mark.parametrize(("command", "funestra", "had_it"), [
        ("portrait_take_osr2", FunTimeFunestra.PORTRAIT, FunTimeFunestra.MAIN),
        ("landscape_take_osr2", FunTimeFunestra.LANDSCAPE, FunTimeFunestra.PORTRAIT),
        ("main_take_osr2", FunTimeFunestra.MAIN, FunTimeFunestra.LANDSCAPE),
    ])
    def test_the_funestra_whose_button_was_pressed_has_it_and_the_others_do_not(
            self, command, funestra, had_it, tmp_path: Path):
        after, ops = dispatch_command(command, _make_state(osr2_funestra=had_it),
                                      _make_config(tmp_path))

        assert after.osr2_funestra == funestra
        assert ops == []

    def test_a_satellite_funestra_takes_it_in_origenerator_mode_too(self, tmp_path: Path):
        config = _make_config(tmp_path)
        state = _handed_over()

        assert not routes_to_origenerator("portrait_take_osr2")
        assert dispatch_command("portrait_take_osr2", state, config)[0].osr2_funestra == FunTimeFunestra.PORTRAIT

    def test_the_apps_resume_in_genau_mode_leaves_the_robot_hand_to_the_satellite_funestras_script(
            self, tmp_path: Path):
        config = _make_config(tmp_path)
        state = _make_state(main_mode=MainMode.GENAU, omni_paused=True,
                            osr2_funestra=FunTimeFunestra.PORTRAIT)

        after, _ops = dispatch_command("omnipause_toggle", state, config)

        assert after.omni_paused is False
        assert not config.genau_cmd_file.exists()

    def test_the_bare_nudge_speeds_the_satellite_funestra_whose_script_has_the_osr2(self, tmp_path: Path):
        config = _make_config(tmp_path)
        config.portrait.status_file.write_text("has_funscript=1\nfunscript_resting=0\n",
                                               encoding="utf-8")

        dispatch_command("speed_up", _make_state(main_mode=MainMode.GENAU,
                                                 osr2_funestra=FunTimeFunestra.PORTRAIT), config)

        assert config.portrait.cmd_file.read_text(encoding="utf-8") == "SPEED_UP\n"
        assert not config.genau_cmd_file.exists()

    def test_the_bare_nudge_speeds_the_robot_hand_while_a_satellite_funestra_has_it(self, tmp_path: Path):
        config = _make_config(tmp_path)
        _set_main_funestra_driving(config, driving=True)

        dispatch_command("speed_up", _make_state(osr2_funestra=FunTimeFunestra.PORTRAIT), config)

        assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_UP\n"
        assert not config.main_funestra_cmd_file.exists()


def test_genau_flick_commands_write_cmd_file(tmp_path: Path):
    for command, verb in (
        ("genau_flick_seconds_down", "FLICK_SECONDS_DOWN"),
        ("genau_flick_seconds_up", "FLICK_SECONDS_UP"),
        ("genau_flip_ends", "FLIP_ENDS"),
    ):
        config = _make_config(tmp_path / command)
        _new_state, ops = dispatch_command(command, _make_state(main_mode=MainMode.GENAU), config)

        assert config.genau_cmd_file.read_text(encoding="utf-8") == verb + "\n"
        assert ops == []


def test_marking_genaus_flick_trash_flashes_marked_trash_over_the_main_funestra(tmp_path: Path):
    config = _make_config(tmp_path)

    _, ops = dispatch_command("genau_trash_flick", _make_state(main_mode=MainMode.GENAU), config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "TRASH\n"
    assert [(op.op, op.key, op.source) for op in ops] == [("message", "Sent to Trash", "main")]


@pytest.mark.parametrize("command", [
    "genau_prev_flick",
    "genau_next_flick",
    "genau_trash_flick",
    "genau_flip_ends",
    "genau_flick_seconds_down",
    "genau_flick_seconds_up",
    "genau_flick_seconds_30",
])
def test_every_genau_command_is_ignored_with_video_in_the_main_funestra(
        tmp_path: Path, command: str):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO, active_funestra=2)

    new_state, ops = dispatch_command(command, state, config)

    assert not config.genau_cmd_file.exists()
    assert (new_state, ops) == (replace(state, active_funestra=FunTimeFunestra.MAIN),
                                [WindowOp(op=Op.IGNORED, key="in Kino mode")])


@pytest.mark.parametrize(("command", "verb"), [
    ("robot_hand_center_up", "CENTER_UP"),
    ("robot_hand_amplitude_down", "AMPLITUDE_DOWN"),
    ("robot_hand_cycle_shape", "CYCLE_SHAPE"),
    ("robot_hand_toggle_learned", "TOGGLE_LEARNED"),
    ("robot_hand_center_50", "CENTER 50"),
])
def test_the_robot_hands_commands_still_reach_it_with_video_in_the_main_funestra(
        tmp_path: Path, command: str, verb: str):
    config = _make_config(tmp_path)

    dispatch_command(command, _make_state(main_mode=MainMode.KINO), config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == verb + "\n"


def test_genau_flick_seconds_writes_a_numeric_cmd(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    dispatch_command("genau_flick_seconds_30", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "FLICK_SECONDS 30\n"


def test_robot_hand_cmd_reaches_genau_in_kino_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    new_state, ops = dispatch_command("robot_hand_speed_down", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED_DOWN\n"
    assert new_state == state
    assert ops == []


def test_genau_amp_writes_numeric_cmd_file(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    new_state, ops = dispatch_command("robot_hand_amp_50", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "AMP 50\n"
    assert new_state == state
    assert ops == []


def test_genau_center_writes_numeric_cmd_file(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    _new_state, _ops = dispatch_command("robot_hand_center_80", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "CENTER 80\n"


def test_genau_speed_writes_numeric_cmd_file(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    _new_state, _ops = dispatch_command("robot_hand_speed_30", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "SPEED 30\n"


def test_a_number_of_seconds_for_the_flick_reaches_genau_as_seconds_not_as_an_axis_level(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    dispatch_command("genau_flick_seconds_12", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "FLICK_SECONDS 12\n"


def test_a_level_that_is_not_a_whole_number_reaches_genau_as_nothing(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    dispatch_command("robot_hand_amp_loud", state, config)

    assert not config.genau_cmd_file.exists()


def test_robot_hand_numeric_cmd_reaches_genau_in_kino_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    new_state, ops = dispatch_command("robot_hand_amp_50", state, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8") == "AMP 50\n"
    assert new_state == state
    assert ops == []


def test_enter_omnipause_pauses_satellites_and_suspends(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert new_state.omni_paused is True
    assert any(op.op == "suspend_hotkeys" for op in ops)
    assert config.portrait.paused_file.read_text(encoding="utf-8") == "1"
    assert config.landscape.paused_file.read_text(encoding="utf-8") == "1"


def test_enter_omnipause_emits_disable_all_topmost(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    _new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert any(op.op == "disable_all_topmost" for op in ops)


def test_relief_omnipause_is_omnipause_and_the_retract_hold_together(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    new_state, ops = dispatch_command("relief_omnipause", state, config)

    assert (new_state.omni_paused, new_state.osr2_control) == (True, OSR2_RETRACTED)
    assert config.broker_cmd_file.read_text(encoding="utf-8").split() == ["PARK", "RETRACT"]
    assert any(op.op == "disable_all_topmost" for op in ops)
    assert any(op.op == "suspend_hotkeys" for op in ops)
    assert all(paused.read_text(encoding="utf-8") == "1" for paused in (
        config.portrait.paused_file, config.landscape.paused_file,
        config.main_funestra_paused_file, config.genau_paused_file,
        config.audio_paused_file))


def test_leaving_the_pause_relief_entered_keeps_the_osr2_retracted(tmp_path: Path):
    config = _make_config(tmp_path)
    state, _ops = dispatch_command("relief_omnipause", _make_state(main_mode=MainMode.GENAU),
                                   config)

    state, _ops = dispatch_command("omnipause_toggle", state, config)

    assert (state.omni_paused, state.osr2_control) == (False, OSR2_RETRACTED)
    assert config.broker_cmd_file.read_text(encoding="utf-8").split() == [
        "PARK", "RETRACT", "RETRACT"]


def test_omnipause_toggle_enter_emits_disable_all_topmost(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False)

    _new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert any(op.op == "disable_all_topmost" for op in ops)


def test_omnipause_toggle_leave_emits_restore_all_topmost(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    _new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert any(op.op == "restore_all_topmost" for op in ops)


def test_a_leave_omnipause_verb_no_producer_can_send_is_inert(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    new_state, ops = dispatch_command("leave_omnipause", state, config)

    assert ops == []
    assert new_state.omni_paused is True


def test_leaving_omnipause_brings_back_a_funestra_parked_from_the_hud_it_took_down(tmp_path: Path):
    config = _make_config(tmp_path)

    _state, ops = dispatch_command("omnipause_toggle", _make_state(omni_paused=True), config)

    assert any(op.op == "restore_parked" for op in ops)


def test_the_restack_and_the_raise_land_on_windows_already_back_on_screen(tmp_path: Path):
    config = _make_config(tmp_path)

    _state, ops = dispatch_command("omnipause_toggle", _make_state(omni_paused=True), config)
    order = [op.op for op in ops]

    assert order.index("restore_parked") < order.index("restore_all_topmost")
    assert order.index("restore_parked") < order.index("activate_window")


def test_entering_omnipause_parks_nothing_of_its_own(tmp_path: Path):
    config = _make_config(tmp_path)

    _state, ops = dispatch_command("enter_omnipause", _make_state(), config)

    assert not any(op.op in ("minimize_window", "restore_parked") for op in ops)


def test_enter_omnipause_does_not_remove_genau_topmost(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False, main_mode=MainMode.GENAU)

    _new_state, ops = dispatch_command("enter_omnipause", state, config)

    assert not any(op.op == "hide_window" and op.key == "genau" for op in ops)


def test_omnipause_toggle_enter_does_not_remove_genau_topmost(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=False, main_mode=MainMode.GENAU)

    _new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert not any(op.op == "hide_window" and op.key == "genau" for op in ops)


def test_leaving_omnipause_resumes_satellites_only(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True)

    new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert new_state.omni_paused is False
    assert any(op.op == "unsuspend_hotkeys" for op in ops)
    assert config.portrait.paused_file.read_text(encoding="utf-8") == "0"
    assert config.landscape.paused_file.read_text(encoding="utf-8") == "0"


def test_verbs_sent_to_a_satellite_in_one_burst_all_wait_in_its_file(tmp_path: Path):
    config = _make_config(tmp_path)

    send_satellite(config, FunTimeFunestra.PORTRAIT, "LOCK_OFF")
    send_satellite(config, FunTimeFunestra.PORTRAIT, "NEXT")

    assert _cmds(config, FunTimeFunestra.PORTRAIT) == ["LOCK_OFF", "NEXT"]


@pytest.mark.parametrize("main_mode", list(MainMode))
def test_leaving_omnipause_activates_the_window_kino_and_genau_share_in_either_mode(
        tmp_path: Path, main_mode):
    config = _make_config(tmp_path)
    state = _make_state(omni_paused=True, main_mode=main_mode)

    _new_state, ops = dispatch_command("omnipause_toggle", state, config)

    assert any(op.op == "activate_window" and op.key == "main_funestra" for op in ops)


@pytest.mark.parametrize("main_mode", list(MainMode))
def test_the_main_funestras_nudge_pair_seeks_the_main_funestra_in_either_mode(
        tmp_path: Path, main_mode: MainMode):
    config = _make_config(tmp_path)
    _publish_status(config.main_funestra_status_file, duration_ms=60_000, position_ms=30_000)
    state = _make_state(main_mode=main_mode)

    _state, back_ops = dispatch_command("main_nudge_prev", state, config)
    _state, forward_ops = dispatch_command("main_nudge_next", state, config)

    assert back_ops == forward_ops == []
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "SEEK_BACK\nSEEK_FWD\n"


@pytest.mark.parametrize(("command", "status_of", "expected_message"), [
    ("main_nudge_next", lambda config: config.main_funestra_status_file,
     ("This video is 10s or shorter", SOURCE_MAIN)),
    ("portrait_nudge_prev", lambda config: config.satellite(FunTimeFunestra.PORTRAIT).status_file,
     ("This video is 10s or shorter", SOURCE_PORTRAIT)),
])
def test_a_ten_second_step_on_a_video_of_ten_seconds_or_shorter_does_nothing_and_says_why(
        tmp_path: Path, command, status_of, expected_message):
    config = _make_config(tmp_path)
    _publish_status(status_of(config), duration_ms=10_000)

    _state, ops = dispatch_command(command, _make_state(), config)

    assert [(op.op, op.key, op.source) for op in ops] == [("message", *expected_message)]
    assert not config.main_funestra_cmd_file.exists()
    assert _cmds(config, FunTimeFunestra.PORTRAIT) == []


@pytest.mark.parametrize(("command", "status_of", "position_ms", "expected_message"), [
    ("portrait_nudge_prev", lambda config: config.satellite(FunTimeFunestra.PORTRAIT).status_file,
     6_000, ("Less than 10s into this video", SOURCE_PORTRAIT)),
    ("portrait_nudge_next", lambda config: config.satellite(FunTimeFunestra.PORTRAIT).status_file,
     54_000, ("Less than 10s left in this video", SOURCE_PORTRAIT)),
    ("main_nudge_prev", lambda config: config.main_funestra_status_file,
     6_000, ("Less than 10s into this video", SOURCE_MAIN)),
])
def test_a_ten_second_step_with_less_than_ten_seconds_to_go_stays_put_and_says_why(
        tmp_path: Path, command, status_of, position_ms, expected_message):
    config = _make_config(tmp_path)
    _publish_status(status_of(config), duration_ms=60_000, position_ms=position_ms)

    _state, ops = dispatch_command(command, _make_state(), config)

    assert [(op.op, op.key, op.source) for op in ops] == [("message", *expected_message)]
    assert not config.main_funestra_cmd_file.exists()
    assert _cmds(config, FunTimeFunestra.PORTRAIT) == []


@pytest.mark.parametrize(("command", "position_ms"), [
    ("portrait_nudge_prev", 10_000),
    ("portrait_nudge_next", 49_999),
])
def test_a_ten_second_step_with_just_ten_seconds_to_go_reaches_the_hosted_app(
        tmp_path: Path, command, position_ms):
    config = _make_config(tmp_path)
    _publish_status(config.satellite(FunTimeFunestra.PORTRAIT).status_file,
                    duration_ms=60_000, position_ms=position_ms)

    _state, ops = dispatch_command(command, _make_state(), config)

    assert ops == []
    assert _origenerator_cmds(config) == [command]
    assert _cmds(config, FunTimeFunestra.PORTRAIT) == []


def test_main_funestra_record_commands_write_main_funestra_cmd_in_main_funestra_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    for command, expected in [
        ("main_funestra_record_down", "RECORD_DOWN"),
        ("main_funestra_record_up", "RECORD_UP"),
        ("main_funestra_record_tap", "RECORD_TAP"),
        ("main_funestra_loop_cancel", "LOOP_CANCEL"),
    ]:
        _new_state, ops = dispatch_command(command, state, config)
        assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == expected + "\n"
        assert ops == []
        config.main_funestra_cmd_file.unlink()


def test_main_funestra_record_commands_work_in_kino_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    _new_state, ops = dispatch_command("main_funestra_record_tap", state, config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "RECORD_TAP\n"
    assert ops == []


def test_main_funestra_record_commands_are_ignored_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    _new_state, ops = dispatch_command("main_funestra_record_tap", state, config)

    assert not config.main_funestra_cmd_file.exists()
    assert ops == [WindowOp(op=Op.IGNORED, key="in Genau mode")]


def test_an_unknown_command_leaves_the_session_exactly_as_it_was(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    new_state, ops = dispatch_command("bogus_command", state, config)

    assert new_state == state
    assert ops == []


def test_unknown_command_warns_instead_of_dying_silently(tmp_path: Path, caplog):
    config = _make_config(tmp_path)

    with caplog.at_level(logging.WARNING, logger="fun_time.command_dispatch"):
        dispatch_command("bogus_command", _make_state(), config)

    assert any("bogus_command" in record.message for record in caplog.records)


def test_an_active_command_with_no_main_funestra_meaning_is_ignored_on_the_main_funestra(
        tmp_path: Path, caplog):
    config = _make_config(tmp_path)

    with caplog.at_level(logging.WARNING, logger="fun_time.command_dispatch"):
        new_state, ops = dispatch_command("active_trash", _make_state(), config)

    assert ops == [WindowOp(op=Op.IGNORED, key="on the Main Funestra")]
    assert new_state == _make_state()
    assert not any("active_trash" in record.message for record in caplog.records)


def test_the_ten_second_genaumacher_save_is_asked_for_not_run_on_the_tick(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.KINO)

    with patch("fun_time.genaumacher_save.subprocess") as mock_subprocess:
        new_state, ops = dispatch_command("genaumacher_save", state, config)

    mock_subprocess.run.assert_not_called()
    assert new_state == state
    assert ops == [WindowOp(op="save_clip")]


def test_genaumacher_save_is_ignored_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(main_mode=MainMode.GENAU)

    _new_state, ops = dispatch_command("genaumacher_save", state, config)

    assert ops == [WindowOp(op=Op.IGNORED, key="in Genau mode")]


def test_the_genaumacher_sibling_is_found_beside_the_primary_not_beside_a_worktree():
    _genaumacher_project_dir.cache_clear()
    try:
        resolved = _genaumacher_project_dir()
    finally:
        _genaumacher_project_dir.cache_clear()

    assert resolved.name == "genaumacher"
    assert "worktrees" not in resolved.parts


def test_the_genaumacher_sibling_falls_back_to_this_checkout_without_git(tmp_path):
    genaumacher_save._genaumacher_project_dir.cache_clear()
    try:
        with patch("fun_time.config.PROJECT_DIR", tmp_path / "fun_time"):
            resolved = genaumacher_save._genaumacher_project_dir()
    finally:
        genaumacher_save._genaumacher_project_dir.cache_clear()

    assert resolved == tmp_path / "genaumacher"


class _BuildsRecorded:
    def __init__(self) -> None:
        self.asked: list[MainListBuild] = []

    def build(self, build: MainListBuild) -> None:
        self.asked.append(build)


def _recording(config: BridgeConfig) -> tuple[BridgeConfig, _BuildsRecorded]:
    builds = _BuildsRecorded()
    return replace(config, main_list_builds=builds), builds


@pytest.mark.parametrize(("command", "state", "asked"), [
    ("main_latest", BridgeState(), {"latest": True, "start_at_top": True}),
    ("main_shuffle", BridgeState(main_latest=True), {"latest": False, "start_at_top": True}),
    ("main_projection_vr", BridgeState(), {"start_at_top": False}),
    ("main_reset", BridgeState(main_latest=True), {"latest": False, "start_at_top": True}),
    ("main_fmode_on", BridgeState(), {"scripted_filter": True}),
])
def test_every_main_list_rebuild_is_handed_to_the_sessions_builder(tmp_path, command, state, asked):
    config, builds = _recording(replace(_make_config(tmp_path), vr_library_dirs=str(tmp_path / "vr")))

    dispatch_command(command, state, config)

    assert len(builds.asked) == 1
    build = builds.asked[0]
    assert {field: getattr(build, field, getattr(build.settings, field, None))
            for field in asked} == asked
    assert not (config.state_dir / "main_player_playlist.tsv").exists()


def test_main_latest_reloads_the_main_funestra_newest_first(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    state, ops = dispatch_command("main_latest", BridgeState(), config)

    assert state.main_latest is True
    assert builds.asked[-1].settings.latest is True
    assert ops[0].op == "message"

    state, _ops = dispatch_command("main_shuffle", state, config)
    assert state.main_latest is False
    assert builds.asked[-1].settings.latest is False


def test_shuffle_deals_the_main_funestra_a_new_order_every_time_it_is_asked(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    once, _ops = dispatch_command("main_shuffle", BridgeState(), config)
    twice, _ops = dispatch_command("main_shuffle", once, config)

    assert once.main_shuffle_seed != twice.main_shuffle_seed
    assert [build.settings.shuffle_seed for build in builds.asked] == [
        once.main_shuffle_seed, twice.main_shuffle_seed]


def test_narrowing_the_main_funestra_keeps_its_order(tmp_path):
    config, builds = _recording(_vr_config(tmp_path))

    state, _ops = dispatch_command("main_projection_vr", _make_state(main_shuffle_seed=7), config)

    assert state.main_shuffle_seed == 7
    assert builds.asked[-1].settings.shuffle_seed == 7


def test_a_main_reorder_reaches_genau_when_genau_is_the_one_showing(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    state, ops = dispatch_command("main_latest", _make_state(main_mode=MainMode.GENAU), config)

    assert config.genau_cmd_file.read_text(encoding="utf-8").split() == ["LATEST"]
    assert builds.asked == []
    assert [op.key for op in ops if op.op == "message"] == ["Latest"]

    dispatch_command("main_shuffle", state, config)
    assert config.genau_cmd_file.read_text(encoding="utf-8").split() == ["LATEST", "SHUFFLE"]
    assert builds.asked == []


def test_a_genau_reorder_does_not_make_the_next_main_rebuild_a_latest_one(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    state, _ops = dispatch_command("main_latest", _make_state(main_mode=MainMode.GENAU), config)

    assert (state.genau_latest, state.main_latest) == (True, False)
    dispatch_command("main_fmode_on", replace(state, main_mode=MainMode.KINO), config)
    assert builds.asked[-1].settings.latest is False

    state, _ops = dispatch_command("main_shuffle", state, config)
    assert state.genau_latest is False


def test_a_main_reorder_in_kino_mode_still_reaches_main_funestra(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    state, _ops = dispatch_command("main_latest", _make_state(main_mode=MainMode.KINO), config)

    assert builds.asked[-1].settings.latest is True
    assert state.main_latest is True
    assert not config.genau_cmd_file.exists()


def test_a_length_press_hands_the_rebuild_what_it_left_to_say(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    state, ops = dispatch_command("main_funestra_length_clips", _make_state(), config)

    assert builds.asked[-1].says == "Clips only"
    assert ops == [WindowOp(op=Op.NARROWING_ANSWERS)]

    dispatch_command("main_funestra_length_mixed", state, config)

    assert builds.asked[-1].says == "Clips + Full"


def test_an_orientation_press_narrows_the_main_funestras_list_and_says_so(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    state, ops = dispatch_command("main_orientation_portrait", _make_state(), config)

    assert (state.main_plays_portrait, state.main_plays_landscape) == (True, False)
    assert builds.asked[-1].settings.plays_landscape is False
    assert builds.asked[-1].says == "Portrait only"
    assert ops == [WindowOp(op=Op.NARROWING_ANSWERS)]


def test_an_orientation_press_in_genau_mode_tells_genau_on_the_main_funestras_channel(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    state, ops = dispatch_command(
        "main_orientation_landscape", _make_state(main_mode=MainMode.GENAU), config)

    assert (state.genau_plays_portrait, state.genau_plays_landscape) == (False, True)
    assert (state.main_plays_portrait, state.main_plays_landscape) == (True, True)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines() == [
        "SET_GENAU_ORIENTATIONS landscape"]
    assert builds.asked == []
    assert ops == [WindowOp(op=Op.NARROWING_ANSWERS)]


def test_neither_orientation_holds_genaus_flick_as_neither_shape_does(tmp_path, monkeypatch):
    monkeypatch.setattr("fun_time.content.genau_flick_noun", lambda: Noun(one="pip", many="pips"))
    config = _make_config(tmp_path)
    only_portrait = _make_state(main_mode=MainMode.GENAU, genau_plays_landscape=False)

    _state, ops = dispatch_command("main_orientation_none", only_portrait, config)

    assert config.genau_cmd_file.read_text(encoding="utf-8").splitlines() == ["LOCK_ON"]
    assert [op.key for op in ops] == ["Including no other pips"]


def test_a_narrowed_orientation_is_something_main_reset_puts_back(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    state, _ops = dispatch_command(
        "main_reset", _make_state(main_plays_landscape=False), config)

    assert (state.main_plays_portrait, state.main_plays_landscape) == (True, True)
    assert builds.asked[-1].settings.plays_landscape is True


def _vr_config(tmp_path):
    config = _make_config(tmp_path)
    config.vr_library_dirs = str(tmp_path / "primary" / "vr")
    return config


def test_a_shape_press_narrows_the_browse_and_rebuilds_the_playlist(tmp_path):
    config, builds = _recording(_vr_config(tmp_path))

    state, ops = dispatch_command("main_projection_vr", _make_state(), config)

    assert (state.main_plays_vr, state.main_plays_flat) == (True, False)
    assert builds.asked[-1].settings.plays_flat is False
    assert builds.asked[-1].says == "VR only"
    assert ops == [WindowOp(op=Op.NARROWING_ANSWERS)]


def test_a_narrowing_carries_on_from_the_video_on_screen(tmp_path):
    config, builds = _recording(_vr_config(tmp_path))
    on_screen = str(tmp_path / "primary" / "scene one.mp4")
    config.main_funestra_status_file.parent.mkdir(parents=True, exist_ok=True)
    config.main_funestra_status_file.write_text(f"video={on_screen}\n", encoding="utf-8")

    dispatch_command("main_projection_vr", _make_state(), config)

    assert builds.asked[-1].on_screen == on_screen


def test_asking_for_the_shapes_already_running_reshuffles_nothing(tmp_path):
    config, builds = _recording(_vr_config(tmp_path))

    _state, ops = dispatch_command("main_projection_both", _make_state(), config)

    assert builds.asked == []
    assert ops == []


@pytest.mark.parametrize(("command", "state"), [
    ("main_projection_none", BridgeState()),
    ("main_funestra_length_none", BridgeState()),
    ("main_funestra_length_clips", BridgeState(main_plays_flat=False)),
])
def test_every_press_that_leaves_no_video_sends_the_one_on_screen_never_an_empty_list(
        tmp_path, caplog, command, state):
    config = _vr_config(tmp_path)
    on_screen = tmp_path / "primary" / "vr" / "scene one.mp4"
    on_screen.parent.mkdir(parents=True)
    on_screen.write_bytes(b"")
    config.main_funestra_status_file.write_text(f"video={on_screen}\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        _state, ops = dispatch_command(command, state, config)

    sent = config.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines()
    assert config.main_list.file.read_text(encoding="utf-8").splitlines() == [str(on_screen)]
    assert (sent[-1], sent.count("LOCK_ON")) == ("LOCK_ON", 1)
    assert [op.key for op in ops if op.op == "message"] + [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ] == ["Including no other videos"]


def test_a_session_with_one_shape_of_video_ignores_the_filter(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    state, ops = dispatch_command("main_projection_vr", _make_state(), config)

    assert state.main_plays_vr is True
    assert state.main_plays_flat is True
    assert builds.asked == []
    assert ops == []


def _genau_vr_config(tmp_path):
    config = _vr_config(tmp_path)
    config.genau_vr_flicks = str(tmp_path / "vr_flicks")
    return config


def test_a_shape_press_in_genau_mode_narrows_genaus_flicks(tmp_path):
    config, builds = _recording(_genau_vr_config(tmp_path))

    state, ops = dispatch_command(
        "main_projection_vr", _make_state(main_mode=MainMode.GENAU), config)

    assert (state.genau_plays_vr, state.genau_plays_flat) == (True, False)
    assert (state.main_plays_vr, state.main_plays_flat) == (True, True)
    assert config.genau_cmd_file.read_text(encoding="utf-8").splitlines() == ["SHAPES vr"]
    assert builds.asked == []
    assert ops == [WindowOp(op=Op.NARROWING_ANSWERS)]


def test_putting_genaus_other_shape_back_asks_it_for_both(tmp_path):
    config = _genau_vr_config(tmp_path)
    narrowed = _make_state(main_mode=MainMode.GENAU, genau_plays_vr=False)

    state, ops = dispatch_command("main_projection_both", narrowed, config)

    assert (state.genau_plays_vr, state.genau_plays_flat) == (True, True)
    assert config.genau_cmd_file.read_text(encoding="utf-8").splitlines() == ["SHAPES vr flat"]
    assert ops == [WindowOp(op=Op.NARROWING_ANSWERS)]


def test_neither_of_genaus_shapes_holds_its_flick(tmp_path, monkeypatch):
    monkeypatch.setattr("fun_time.content.genau_flick_noun", lambda: Noun(one="pip", many="pips"))
    config = _genau_vr_config(tmp_path)
    only_flat = _make_state(main_mode=MainMode.GENAU, genau_plays_vr=False)

    state, ops = dispatch_command("main_projection_none", only_flat, config)

    assert (state.genau_plays_vr, state.genau_plays_flat) == (False, False)
    assert config.genau_cmd_file.read_text(encoding="utf-8").splitlines() == ["LOCK_ON"]
    assert [op.key for op in ops] == ["Including no other pips"]


def test_genau_holding_its_flick_for_want_of_shapes_is_not_a_lock_of_his(tmp_path):
    config = _genau_vr_config(tmp_path)
    config.regen_metadata_root = tmp_path / "metadata"
    flick = tmp_path / "flicks" / "made up flick.mp4"
    _show_on_genau(config, flick, locked=False)
    only_flat = _make_state(main_mode=MainMode.GENAU, genau_plays_vr=False,
                            rfb_mode=RfbMode.ORIGENERATOR, origenerator_ready=True)

    _state, ops = dispatch_command("main_projection_none", only_flat, config)

    assert Op.FOLLOW_THE_LOCK not in [op.op for op in ops]
    assert _origenerator_cmds(config) == []
    assert not is_favorite(flick, config.regen_metadata_root)


def test_asking_genau_for_the_shapes_it_already_plays_sends_it_nothing(tmp_path):
    config = _genau_vr_config(tmp_path)

    _state, ops = dispatch_command(
        "main_projection_both", _make_state(main_mode=MainMode.GENAU), config)

    assert ops == []
    assert not config.genau_cmd_file.exists()


def test_a_genau_with_only_flat_flicks_ignores_the_filter(tmp_path):
    config = _vr_config(tmp_path)

    state, ops = dispatch_command(
        "main_projection_vr", _make_state(main_mode=MainMode.GENAU), config)

    assert (state.genau_plays_vr, state.genau_plays_flat) == (True, True)
    assert ops == []
    assert not config.genau_cmd_file.exists()


def test_main_reset_puts_back_the_defaults_the_main_funestra_holds_itself(tmp_path):
    config = _make_config(tmp_path)

    dispatch_command("main_reset", _make_state(), config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines() == [
        "LOOP_CANCEL", "LOCK_OFF", "SET_SPEED 1"]


def test_main_reset_does_nothing_to_a_main_funestra_already_at_every_default(tmp_path):
    config = _make_config(tmp_path)
    config.main_funestra_status_file.parent.mkdir(parents=True, exist_ok=True)
    config.main_funestra_status_file.write_text(
        "video=C:/v/scene one.mp4\nlocked=0\nloop_state=normal\nspeed=1.0\n"
        "length_mode=mixed\ncompilation=\n",
        encoding="utf-8",
    )

    _state, ops = dispatch_command("main_reset", _make_state(), config)

    assert ops == []
    assert not config.main_funestra_cmd_file.exists()


@pytest.mark.parametrize("off_default", [
    {"main_scripted_filter": True}, {"main_latest": True}, {"main_plays_flat": False},
    {"main_length_mode": LengthMode.CLIPS},
])
def test_main_reset_rebuilds_an_off_default_browse_as_a_fresh_shuffle_from_the_top(
        tmp_path, off_default):
    config, builds = _recording(_vr_config(tmp_path))

    state, _ops = dispatch_command(
        "main_reset", _make_state(main_shuffle_seed=7, **off_default), config)

    assert (state.main_scripted_filter, state.main_latest,
            state.main_plays_vr, state.main_plays_flat) == (False, False, True, True)
    assert state.main_shuffle_seed != 7
    assert [(build.settings, build.start_at_top) for build in builds.asked] == [
        (MainListSettings(shuffle_seed=state.main_shuffle_seed), True)]


def test_main_reset_inside_a_compilation_takes_the_main_list_back_up(tmp_path):
    config, builds = _recording(_make_config(tmp_path))
    _on_screen(config, "C:/v/clip one.mp4", compilation="Vol6")

    dispatch_command("main_reset", _make_state(), config)

    assert [build.start_at_top for build in builds.asked] == [True]


def test_main_reset_does_not_reshuffle_a_funestra_already_at_its_defaults(tmp_path):
    config, builds = _recording(_vr_config(tmp_path))

    dispatch_command("main_reset", _make_state(), config)

    assert builds.asked == []


def test_main_reset_leaves_the_browse_alone_over_a_shape_filter_the_session_does_not_offer(
    tmp_path
):
    config, builds = _recording(_make_config(tmp_path))

    dispatch_command("main_reset", _make_state(main_plays_flat=False), config)

    assert builds.asked == []


def test_main_reset_leaves_what_the_main_funestra_holds_alone_while_genau_owns_the_slot(
        tmp_path):
    config, _builds = _recording(_make_config(tmp_path))

    state, _ops = dispatch_command(
        "main_reset", _make_state(main_mode=MainMode.GENAU, main_scripted_filter=True), config)

    assert state.main_scripted_filter is False
    assert not config.main_funestra_cmd_file.exists()


def test_main_reset_flashes_reset_over_the_main_funestra(tmp_path):
    _state, ops = dispatch_command("main_reset", _make_state(), _make_config(tmp_path))

    assert ops == [WindowOp(op="message", key="Reset", source="main")]


def test_main_reset_makes_the_main_funestra_the_one_a_bare_word_reaches(tmp_path):
    config, _builds = _recording(_make_config(tmp_path))

    state, _ops = dispatch_command("main_reset", _make_state(active_funestra=2), config)

    assert state.active_funestra == 1


def test_main_latest_starts_at_the_top_where_the_newest_arrivals_sit(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    dispatch_command("main_latest", BridgeState(), config)

    assert builds.asked[-1].start_at_top is True


def _up(**fields) -> BridgeState:
    return BridgeState(origenerator_ready=True, **fields)


def _handed_over(*_funestras: FunTimeFunestra, **fields) -> BridgeState:
    return _up(**fields)


def test_a_spoken_trash_reaches_a_hosted_satellite_with_when_it_was_said(tmp_path):
    config = replace(_make_config(tmp_path),
                     origenerator_said_at_suffix=" @{said_at_epoch_seconds}")
    on_the_wall, said_two_seconds_ago = time.time() - 2.0, time.monotonic() - 2.0

    dispatch_command("portrait_trash", _handed_over(FunTimeFunestra.PORTRAIT), config,
                     spoken_at=said_two_seconds_ago)

    [line] = _origenerator_cmds(config)
    command, _, stamp = line.partition(" @")
    assert command == "portrait_trash"
    assert float(stamp) == pytest.approx(on_the_wall, abs=0.1)


def test_a_spoken_lock_reaches_a_hosted_satellite_with_no_time_to_go_back_to_again(tmp_path):
    config = replace(_make_config(tmp_path),
                     origenerator_said_at_suffix=" @{said_at_epoch_seconds}")
    _set_current(config, FunTimeFunestra.PORTRAIT, "C:\\pictures\\moved on to.png")

    dispatch_command("portrait_lock_on", _handed_over(FunTimeFunestra.PORTRAIT), config,
                     target_path="C:\\pictures\\meant.png", spoken_at=time.monotonic() - 2.0)

    assert _origenerator_cmds(config) == ["portrait_lock_on"]


def _origenerator_cmds(config: BridgeConfig) -> list[str]:
    cmd_file = config.origenerator_cmd_file
    if not cmd_file.exists():
        return []
    return [line.strip() for line in cmd_file.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestASatelliteTheAppHasNotTaken:
    def test_kino_mode_switches_the_main_funestra_alone(self, tmp_path):
        config = _make_config(tmp_path)
        held = _handed_over(main_mode=MainMode.GENAU)

        state, _ops = dispatch_command("kino_activate", held, config)

        assert state.main_mode is MainMode.KINO
        assert (state.portrait, state.landscape) == (held.portrait, held.landscape)
        assert _origenerator_cmds(config) == []
        assert [_cmds(config, funestra) for funestra in FunTimeFunestra.SATELLITES] == [[], []]

    def test_a_press_on_a_satellite_the_app_has_not_taken_yet_is_told_to_the_app(self, tmp_path):
        config = _make_config(tmp_path)
        _state, _ = dispatch_command("landscape_next",
                                    _handed_over(FunTimeFunestra.PORTRAIT), config)
        assert _origenerator_cmds(config) == ["landscape_next"]
        assert _cmds(config, FunTimeFunestra.LANDSCAPE) == []

    def test_an_act_said_to_both_satellites_reaches_the_app_for_each_of_them(self, tmp_path):
        config = _make_config(tmp_path)

        dispatch_command("filter_satellites_scene_one", _up(), config)

        assert _origenerator_cmds(config) == ["filter_portrait_scene_one",
                                              "filter_landscape_scene_one"]


class TestTheRandomFavesBrowsersPlace:
    def test_origenerator_takes_the_browsers_place_and_leaves_the_funestras_alone(
            self, tmp_path):
        config = _make_config(tmp_path)
        state, ops = dispatch_command("origenerator_activate", _up(), config)
        assert state.rfb_mode is RfbMode.ORIGENERATOR
        assert [(op.op, op.key) for op in ops] == [
            ("show_window", "origenerator"),
            ("activate_window", "origenerator"),
            ("restack_rfb", ""),
        ]
        assert _origenerator_cmds(config) == []
        assert _cmds(config, FunTimeFunestra.PORTRAIT) == []

    def test_the_browser_takes_its_place_back(self, tmp_path):
        config = _make_config(tmp_path)
        state, ops = dispatch_command(
            "rfb_activate", _handed_over(rfb_mode=RfbMode.ORIGENERATOR), config)
        assert state.rfb_mode is RfbMode.RFB
        assert [(op.op, op.key) for op in ops] == [
            ("hide_window", "origenerator"), ("restack_rfb", "")]
        assert _origenerator_cmds(config) == []

    def test_asking_for_what_already_holds_the_place_moves_no_window(self, tmp_path):
        config = _make_config(tmp_path)
        state, ops = dispatch_command("rfb_activate", _up(), config)
        assert (state.rfb_mode, ops) == (RfbMode.RFB, [])

    def test_a_hosted_app_still_booting_keeps_the_browser_in_its_place(self, tmp_path):
        config = _make_config(tmp_path)
        state, ops = dispatch_command("origenerator_activate", BridgeState(), config)
        assert state.rfb_mode is RfbMode.RFB
        assert [(op.op, op.key) for op in ops] == [
            ("message", "Origenerator is still starting")]


class TestOrigeneratorTransport:
    def test_satellite_transport_reaches_the_hosted_app_not_the_funestra(self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over()
        state, _ = dispatch_command("portrait_next", state, config)
        assert _origenerator_cmds(config) == ["portrait_next"]
        assert _cmds(config, 2) == []

    def test_a_ten_second_step_a_hosted_satellite_cannot_take_says_why_and_reaches_no_one(
            self, tmp_path):
        config = _make_config(tmp_path)
        _publish_status(config.portrait.status_file, duration_ms=60_000, position_ms=6_000)

        _state, ops = dispatch_command("portrait_nudge_prev", _handed_over(), config)

        assert [(op.op, op.key, op.source) for op in ops] == [
            ("message", "Less than 10s into this video", SOURCE_PORTRAIT)]
        assert _origenerator_cmds(config) == []
        assert _cmds(config, 2) == []

    def test_skip_ahead_on_a_hosted_satellite_without_the_osr2_says_so_and_reaches_no_one(
            self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over(osr2_funestra=FunTimeFunestra.MAIN)

        _state, ops = dispatch_command("portrait_funscript_jump", state, config)

        assert ops == [WindowOp(op="message", key="The portrait Satellite doesn't have the OSR2",
                                level=logging.WARNING)]
        assert _origenerator_cmds(config) == []

    def test_skip_ahead_reaches_the_hosted_app_for_the_satellite_that_has_the_osr2(self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over(osr2_funestra=FunTimeFunestra.PORTRAIT)

        dispatch_command("portrait_funscript_jump", state, config)

        assert _origenerator_cmds(config) == ["portrait_funscript_jump"]

    def test_every_press_on_a_hosted_satellite_routes_as_it_was_posted(self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over()
        presses = ("portrait_prev", "portrait_trash", "portrait_reset",
                   "portrait_fmode", "portrait_enhanced", "portrait_seed_loop",
                   "portrait_more_seeds", "portrait_play_video|C:/fixtures/one.png",
                   "landscape_lock_video|C:/fixtures/two.png", "landscape_no_loop",
                   "portrait_clip_seconds_up", "landscape_clip_seconds_down",
                   "portrait_foot_press|30|7", "landscape_foot_wheel|-1|10|5")
        for command in presses:
            state, _ = dispatch_command(command, state, config)
        assert _origenerator_cmds(config) == list(presses)
        assert _cmds(config, 2) == []
        assert _cmds(config, 3) == []

    def test_a_satellite_filter_goes_to_the_hosted_app(self, tmp_path):
        config = _make_config(tmp_path)

        dispatch_command("filter_portrait_scene_one", _handed_over(), config)

        assert _playlist(config, FunTimeFunestra.PORTRAIT) == []
        assert _origenerator_cmds(config) == ["filter_portrait_scene_one"]


    def test_the_funestras_own_controls_stay_the_sessions(self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over()

        state, _ops = dispatch_command("portrait_speed_up", state, config)
        state, minimize_ops = dispatch_command("portrait_minimize", state, config)

        assert _origenerator_cmds(config) == []
        assert _cmds(config, 2) == ["SPEED_UP"]
        assert minimize_ops

    def test_the_apps_f_mode_turns_each_hosted_shows_own_f_mode_on(self, tmp_path):
        config = _make_config(tmp_path)
        for funestra in FunTimeFunestra.SATELLITES:
            publish_the_show(config, funestra, f_mode=False)
        state = _handed_over()

        with patch("fun_time.f_mode.apply_fmode") as fmode:
            fmode.return_value = SimpleNamespace(funestras=(FunTimeFunestra.MAIN,), log_message="")
            state, _ = dispatch_command("fmode_toggle", state, config)

        assert fmode.call_args.kwargs["funestras"] == (FunTimeFunestra.MAIN,)
        assert _origenerator_cmds(config) == ["portrait_fmode_on", "landscape_fmode_on"]

    def test_the_apps_f_mode_lifts_the_hosted_shows_once_every_funestra_is_in_it(self, tmp_path):
        config = _make_config(tmp_path)
        for funestra in FunTimeFunestra.SATELLITES:
            publish_the_show(config, funestra, f_mode=True)
        state = _handed_over(main_scripted_filter=True)

        state, _ops, mock_fmode = _dispatch_fmode("fmode_toggle", state, config)

        assert mock_fmode.call_args.kwargs["funestras"] == (FunTimeFunestra.MAIN,)
        assert state.main_scripted_filter is False
        assert _origenerator_cmds(config) == ["portrait_fmode_off", "landscape_fmode_off"]

    def test_a_hosted_show_already_in_the_asked_for_f_mode_is_sent_nothing(self, tmp_path):
        config = _make_config(tmp_path)
        publish_the_show(config, FunTimeFunestra.PORTRAIT, f_mode=True)
        publish_the_show(config, FunTimeFunestra.LANDSCAPE, f_mode=False)

        _dispatch_fmode("fmode_on", _handed_over(), config)

        assert _origenerator_cmds(config) == ["landscape_fmode_on"]

    def test_a_satellite_with_no_show_up_has_no_say_in_the_apps_f_mode(self, tmp_path):
        config = _make_config(tmp_path)
        publish_the_show(config, FunTimeFunestra.PORTRAIT, f_mode=True)
        config.satellite(FunTimeFunestra.LANDSCAPE).origenerator_hud_file.write_text("", encoding="utf-8")
        state = _handed_over(main_scripted_filter=True)

        state, _ops, _mock = _dispatch_fmode("fmode_toggle", state, config)

        assert state.main_scripted_filter is False
        assert _origenerator_cmds(config) == ["portrait_fmode_off"]

    def test_fun_time_hears_for_the_hosted_app_and_posts_it_the_words_to_match(self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over()
        for command in ("landscape_say_favorites", "portrait_say_play_slideshow",
                        "landscape_say_fix_teeth", "portrait_say_enhanced_only"):
            state, _ = dispatch_command(command, state, config)
        assert _origenerator_cmds(config) == [
            "LANDSCAPE_SAY:favorites",
            "PORTRAIT_SAY:play slideshow",
            "LANDSCAPE_SAY:fix teeth",
            "PORTRAIT_SAY:enhanced only",
        ]

    def test_no_filter_reaches_the_hosted_show_as_clear_filter(self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over()

        state, _ = dispatch_command("portrait_no_filter", state, config)

        assert _origenerator_cmds(config) == ["PORTRAIT_SAY:clear filter"]

    def test_every_phrase_the_hosted_app_answers_reaches_it_said_to_either_satellite(self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over()
        phrases = ("fabricated order", "fix alpha hands")
        heard = build_voice_commands(filter_commands={}, clip_jump_phrases=(),
                                     origenerator_phrases=frozenset(phrases))

        for satellite in ("portrait", "landscape"):
            for phrase in phrases:
                state, _ = dispatch_command(heard[f"{satellite} {phrase}"], state, config)

        assert _origenerator_cmds(config) == [
            "PORTRAIT_SAY:fabricated order", "PORTRAIT_SAY:fix alpha hands",
            "LANDSCAPE_SAY:fabricated order", "LANDSCAPE_SAY:fix alpha hands",
        ]

class TestALockOnAHostedSide:
    @pytest.mark.parametrize("funestra_locked", [False, True])
    def test_a_pressed_lock_locks_the_funestra_and_leaves_the_rest_to_the_hosted_app(
            self, funestra_locked, tmp_path):
        config = replace(_make_config(tmp_path), regen_metadata_root=tmp_path / "metadata")
        image = tmp_path / "scene one.png"
        image.write_text("x", encoding="utf-8")
        _set_current(config, FunTimeFunestra.PORTRAIT, str(image), locked=funestra_locked)

        _state, ops = dispatch_command("portrait_lock", _handed_over(), config)

        assert _cmds(config, FunTimeFunestra.PORTRAIT) == ["LOCK_ON"]
        assert _origenerator_cmds(config) == ["portrait_lock"]
        assert ops == []
        assert not metadata_path_for(image, config.regen_metadata_root).exists()
        assert not (config.state_dir / "watch_stats.json").exists()

    def test_a_spoken_lock_brings_back_the_image_up_when_he_began_and_locks_that(
            self, tmp_path):
        config = _make_config(tmp_path)
        meant = "C:\\images\\meant.png"
        _set_current(config, FunTimeFunestra.PORTRAIT, "C:\\images\\moved on to.png")

        dispatch_command("portrait_lock_on", _handed_over(), config, target_path=meant)

        assert _cmds(config, FunTimeFunestra.PORTRAIT) == [f"PLAY_FILE {meant}", "LOCK_ON"]
        assert _origenerator_cmds(config) == ["portrait_lock_on"]

    def _said_to_a_hosted_side(self, command, tmp_path, *, held_by_a_request_or_a_run):
        config = _make_config(tmp_path)
        _set_current(config, FunTimeFunestra.PORTRAIT, "C:\\images\\up.png",
                     locked=held_by_a_request_or_a_run)
        _state, ops = dispatch_command(
            command, _handed_over(), config)
        return ops, _cmds(config, FunTimeFunestra.PORTRAIT), _origenerator_cmds(config)

    @pytest.mark.parametrize("held_by_a_request_or_a_run", [False, True])
    def test_a_funestra_a_request_or_a_run_holds_still_hands_a_spoken_lock_on(
            self, held_by_a_request_or_a_run, tmp_path):
        assert self._said_to_a_hosted_side(
            "portrait_lock_on", tmp_path,
            held_by_a_request_or_a_run=held_by_a_request_or_a_run,
        ) == ([], ["LOCK_ON"], ["portrait_lock_on"])

    @pytest.mark.parametrize("held_by_a_request_or_a_run", [False, True])
    def test_a_letting_go_said_is_handed_on_and_never_steps_the_funestra_on(
            self, held_by_a_request_or_a_run, tmp_path):
        assert self._said_to_a_hosted_side(
            "portrait_lock_off", tmp_path,
            held_by_a_request_or_a_run=held_by_a_request_or_a_run,
        ) == ([], [], ["portrait_lock_off"])

    def test_the_hosted_apps_own_word_for_its_lock_is_the_satellites_spoken_lock(self, tmp_path):
        config = replace(_make_config(tmp_path),
                         origenerator_lock_phrases=frozenset({"hold"}))
        meant = "C:\\images\\meant.png"
        _set_current(config, FunTimeFunestra.PORTRAIT, "C:\\images\\moved on to.png")

        state, _ = dispatch_command("portrait_say_hold", _handed_over(), config,
                                    target_path=meant)
        dispatch_command("portrait_say_fabricated_order", state, config)

        assert _cmds(config, FunTimeFunestra.PORTRAIT) == [f"PLAY_FILE {meant}", "LOCK_ON"]
        assert _origenerator_cmds(config) == ["portrait_lock_on", "PORTRAIT_SAY:fabricated order"]

class TestOmniPauseWithOrigenerator:
    def test_enter_freezes_the_hosted_app_too(self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over()
        state, _ = dispatch_command("enter_omnipause", state, config)
        assert state.omni_paused
        assert config.origenerator_paused_file.read_text(encoding="utf-8") == "1"

    def test_leave_sets_the_funestras_going_again_handed_over(self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over(omni_paused=True)
        state, _ = dispatch_command("omnipause_toggle", state, config)
        assert not state.omni_paused
        assert config.origenerator_paused_file.read_text(encoding="utf-8") == "0"
        assert config.portrait.paused_file.read_text(encoding="utf-8") == "0"
        assert config.landscape.paused_file.read_text(encoding="utf-8") == "0"


class TestWhereEachHudSits:
    def test_a_satellite_key_moves_that_funestras_hud_to_that_corner(self, tmp_path):
        config = _make_config(tmp_path)

        state, _ops = dispatch_command("portrait_hud_right", BridgeState(), config)
        state, _ops = dispatch_command("portrait_hud_down", state, config)

        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_corner is HudCorner.LOWER_RIGHT
        assert state.satellite(FunTimeFunestra.LANDSCAPE).hud_corner is HudCorner.UPPER_LEFT

    def test_the_main_funestras_keys_move_its_hud_toward_a_side_as_a_satellites_do(self, tmp_path):
        config = _make_config(tmp_path)

        state, _ops = dispatch_command("main_hud_right", BridgeState(), config)
        state, _ops = dispatch_command("main_hud_down", state, config)
        assert state.main_hud_corner is HudCorner.LOWER_RIGHT
        state, _ops = dispatch_command("main_hud_left", state, config)
        assert state.main_hud_corner is HudCorner.LOWER_LEFT
        state, _ops = dispatch_command("main_hud_up", state, config)
        assert state.main_hud_corner is HudCorner.UPPER_LEFT
        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_corner is HudCorner.UPPER_LEFT

    def test_the_ring_the_main_funestras_hud_walked_is_gone(self, tmp_path):
        config = _make_config(tmp_path)

        for retired in ("main_hud_clockwise", "main_hud_counterclockwise"):
            state, _ops = dispatch_command(retired, BridgeState(), config)
            assert state == BridgeState(), retired

    def test_in_the_headset_the_same_keys_put_the_hud_against_a_satellite(self, tmp_path):
        config = _make_config(tmp_path, vr_main_funestra=True)

        state, _ops = dispatch_command("landscape_hud_up", BridgeState(), config)
        state, _ops = dispatch_command("main_hud_left", state, config)

        assert state.satellite(FunTimeFunestra.LANDSCAPE).hud_edge is HudEdge.UPPER
        assert state.main_hud_edge is HudEdge.LEFT
        assert state.satellite(FunTimeFunestra.LANDSCAPE).hud_corner is HudCorner.UPPER_LEFT
        assert state.main_hud_corner is HudCorner.UPPER_LEFT

        state, _ops = dispatch_command("main_hud_up", state, config)
        assert state.main_hud_edge is HudEdge.UPPER

    def test_a_hud_starts_against_the_lower_satellite_in_the_headset(self):
        assert BridgeState().main_hud_edge is HudEdge.LOWER
        assert BridgeState().satellite(FunTimeFunestra.PORTRAIT).hud_edge is HudEdge.LOWER


class TestAClickInACorner:
    def test_it_moves_that_funestras_hud_there_and_opens_it(self, tmp_path):
        config = _make_config(tmp_path)
        state, _ops = dispatch_command("portrait_hud_minimize", BridgeState(), config)

        state, _ops = dispatch_command("portrait_hud_restore_at|lower_right", state, config)

        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_corner is HudCorner.LOWER_RIGHT
        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_minimized is False

    def test_on_the_main_funestra_it_moves_the_main_hud_there_and_opens_it(self, tmp_path):
        config = _make_config(tmp_path)
        state, _ops = dispatch_command("main_hud_minimize", BridgeState(), config)

        state, _ops = dispatch_command("main_hud_restore_at|upper_right", state, config)

        assert state.main_hud_corner is HudCorner.UPPER_RIGHT
        assert state.main_hud_minimized is False

    def test_in_origenerator_mode_the_show_wearing_the_hud_is_told_both(self, tmp_path):
        config = _make_config(tmp_path)

        dispatch_command("landscape_hud_restore_at|lower_left",
                         _handed_over(), config)

        assert _origenerator_cmds(config) == ["landscape_hud_corner|lower_left",
                                              "landscape_hud_minimized|0"]

    def test_in_the_headset_it_names_a_satellite_and_hangs_the_hud_there_open(self, tmp_path):
        config = _make_config(tmp_path, vr_main_funestra=True)
        state, _ops = dispatch_command("portrait_hud_minimize", BridgeState(), config)

        state, _ops = dispatch_command("portrait_hud_restore_at|left", state, config)
        state, _ops = dispatch_command("main_hud_restore_at|upper", state, config)

        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_edge is HudEdge.LEFT
        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_minimized is False
        assert state.main_hud_edge is HudEdge.UPPER

    @pytest.mark.parametrize("place", ["left", "", "middle"])
    def test_a_place_that_is_no_corner_leaves_the_hud_as_it_was(self, tmp_path, place):
        config = _make_config(tmp_path)
        state, _ops = dispatch_command("portrait_hud_minimize", BridgeState(), config)

        moved, ops = dispatch_command(f"portrait_hud_restore_at|{place}", state, config)

        assert (moved, ops) == (state, [])


class TestMinimizingAHud:
    def test_a_funestras_minus_collapses_its_hud_and_its_plus_brings_it_back(self, tmp_path):
        config = _make_config(tmp_path)

        state, _ops = dispatch_command("landscape_hud_minimize", BridgeState(), config)
        assert state.satellite(FunTimeFunestra.LANDSCAPE).hud_minimized is True
        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_minimized is False

        state, _ops = dispatch_command("landscape_hud_restore", state, config)
        assert state.satellite(FunTimeFunestra.LANDSCAPE).hud_minimized is False

    def test_the_main_funestras_hud_collapses_the_same_way(self, tmp_path):
        config = _make_config(tmp_path)

        state, _ops = dispatch_command("main_hud_minimize", BridgeState(), config)
        assert state.main_hud_minimized is True

        state, _ops = dispatch_command("main_hud_restore", state, config)
        assert state.main_hud_minimized is False

    def test_a_bare_minimize_hud_reaches_whichever_funestra_was_addressed_last(self):
        assert parse_command("active_hud_minimize").aimed_at_the_active(1).text == "main_hud_minimize"
        assert parse_command("active_hud_restore").aimed_at_the_active(1).text == "main_hud_restore"
        assert (parse_command("active_hud_minimize").aimed_at_the_active(3).text
                == "landscape_hud_minimize")


class TestNamingAFunestraAddressesIt:
    @pytest.mark.parametrize(("command", "funestra"), [
        ("portrait_hud_minimize", FunTimeFunestra.PORTRAIT),
        ("landscape_hud_restore", FunTimeFunestra.LANDSCAPE),
        ("portrait_hud_left", FunTimeFunestra.PORTRAIT),
        ("landscape_hud_restore_at|lower_left", FunTimeFunestra.LANDSCAPE),
        ("portrait_minimize", FunTimeFunestra.PORTRAIT),
        ("landscape_minimize", FunTimeFunestra.LANDSCAPE),
        ("portrait_crown", FunTimeFunestra.PORTRAIT),
        ("main_hud_minimize", FunTimeFunestra.MAIN),
        ("main_hud_clockwise", FunTimeFunestra.MAIN),
        ("main_hud_restore_at|upper_right", FunTimeFunestra.MAIN),
        ("main_minimize", FunTimeFunestra.MAIN),
        ("main_crown", FunTimeFunestra.MAIN),
        ("main_nudge_next", FunTimeFunestra.MAIN),
        ("main_nudge_prev", FunTimeFunestra.MAIN),
        ("main_latest", FunTimeFunestra.MAIN),
        ("main_shuffle", FunTimeFunestra.MAIN),
        ("main_kino_activate", FunTimeFunestra.MAIN),
        ("main_funestra_cycle_version", FunTimeFunestra.MAIN),
        ("genau_next_flick", FunTimeFunestra.MAIN),
    ])
    def test_it_becomes_the_funestra_a_bare_phrase_reaches(self, tmp_path, command, funestra):
        before = FunTimeFunestra.LANDSCAPE if funestra is FunTimeFunestra.MAIN else FunTimeFunestra.MAIN

        state, _ops = dispatch_command(command, _make_state(active_funestra=before),
                                       _make_config(tmp_path))

        assert state.active_funestra == funestra


class TestTheEnhancementLevel:
    def test_the_main_funestra_narrows_its_list_and_says_so(self, tmp_path):
        config, builds = _recording(_make_config(tmp_path))

        state, ops = dispatch_command("main_enhancement_enhanced", _make_state(), config)

        assert state.main_enhancement is EnhancementLevel.ENHANCED
        assert builds.asked[-1].settings.enhancement is EnhancementLevel.ENHANCED
        assert ops == [WindowOp(op="message", key="Enhanced only", source="main", level=MESSAGE)]

    def test_asking_the_main_funestra_for_the_level_it_plays_rebuilds_nothing(self, tmp_path):
        config, builds = _recording(_make_config(tmp_path))

        _state, ops = dispatch_command("main_enhancement_both", _make_state(), config)

        assert ops == []
        assert builds.asked == []

    def test_in_genau_mode_the_main_funestras_level_is_ignored(self, tmp_path):
        config, builds = _recording(_make_config(tmp_path))

        state, ops = dispatch_command(
            "main_enhancement_original", _make_state(main_mode=MainMode.GENAU), config)

        assert ops == [WindowOp(op=Op.IGNORED, key="in Genau mode")]
        assert state.main_enhancement is EnhancementLevel.BOTH
        assert builds.asked == []

    def test_a_reset_puts_the_main_funestras_level_back(self, tmp_path):
        config, builds = _recording(_make_config(tmp_path))
        narrowed = _make_state(main_enhancement=EnhancementLevel.ORIGINAL)

        state, _ops = dispatch_command("main_reset", narrowed, config)

        assert state.main_enhancement is EnhancementLevel.BOTH
        assert builds.asked[-1].settings.enhancement is EnhancementLevel.BOTH


    def test_said_to_a_satellite_in_origenerator_mode_each_level_is_said_to_the_hosted_app(
            self, tmp_path):
        config = _make_config(tmp_path)
        state = _handed_over()

        state, _ops = dispatch_command("portrait_enhancement_enhanced", state, config)
        state, _ops = dispatch_command("portrait_enhancement_both", state, config)
        state, ops = dispatch_command("landscape_enhancement_original", state, config)

        assert _origenerator_cmds(config) == [
            "PORTRAIT_SAY:enhanced only", "PORTRAIT_SAY:enhanced and original",
            "LANDSCAPE_SAY:original only"]
        assert ops == []


class TestAHudSaidToACorner:
    def test_it_moves_that_funestras_hud_there_and_leaves_it_collapsed_if_it_was(self, tmp_path):
        config = _make_config(tmp_path)
        state, _ops = dispatch_command("portrait_hud_minimize", BridgeState(), config)

        state, _ops = dispatch_command("portrait_hud_lower_right", state, config)
        state, _ops = dispatch_command("main_hud_upper_right", state, config)

        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_corner is HudCorner.LOWER_RIGHT
        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_minimized is True
        assert state.main_hud_corner is HudCorner.UPPER_RIGHT

    def test_in_origenerator_mode_the_slideshow_wearing_the_hud_is_told(self, tmp_path):
        config = _make_config(tmp_path)

        dispatch_command("landscape_hud_upper_right", _handed_over(FunTimeFunestra.LANDSCAPE), config)

        assert _origenerator_cmds(config) == ["landscape_hud_corner|upper_right"]

    def test_in_the_headset_an_upper_corner_hangs_it_over_the_top_and_a_lower_one_under(self, tmp_path):
        config = _make_config(tmp_path, vr_main_funestra=True)

        state, _ops = dispatch_command("portrait_hud_upper_left", BridgeState(), config)
        state, _ops = dispatch_command("main_hud_lower_right", state, config)

        assert state.satellite(FunTimeFunestra.PORTRAIT).hud_edge is HudEdge.UPPER
        assert state.main_hud_edge is HudEdge.LOWER

    def test_a_bare_corner_reaches_whichever_funestra_was_addressed_last(self):
        assert parse_command("active_hud_upper_left").aimed_at_the_active(1).text == "main_hud_upper_left"
        assert parse_command("active_hud_lower_right").aimed_at_the_active(2).text == "portrait_hud_lower_right"


class TestASatellitesSound:
    def test_a_satellite_opens_muted_at_full_volume(self):
        assert BridgeState().satellite(FunTimeFunestra.PORTRAIT).muted is True
        assert BridgeState().satellite(FunTimeFunestra.PORTRAIT).volume == 100

    def test_unmute_and_the_steps_reach_that_satellite_alone(self, tmp_path):
        config = _make_config(tmp_path)

        state, ops = dispatch_command("portrait_unmute", _make_state(), config)
        assert config.portrait.cmd_file.read_text(encoding="utf-8") == "SET_VOLUME 100 0\n"
        assert ops == [WindowOp(op="message", key="Volume 100%", source=SOURCE_PORTRAIT)]

        state, ops = dispatch_command("portrait_volume_down", state, config)
        assert state.satellite(FunTimeFunestra.PORTRAIT).volume == 90
        assert config.portrait.cmd_file.read_text(encoding="utf-8").splitlines()[-1] == "SET_VOLUME 90 0"
        assert ops == [WindowOp(op="message", key="Volume 90%", source=SOURCE_PORTRAIT)]
        assert not config.main_funestra_cmd_file.exists()
        assert not config.landscape.cmd_file.exists()

    def test_a_mute_keeps_the_level_and_a_step_lifts_the_mute(self, tmp_path):
        config = _make_config(tmp_path)
        state = _make_state().with_satellite(FunTimeFunestra.LANDSCAPE, volume=40, muted=False)

        state, ops = dispatch_command("landscape_mute", state, config)
        assert (state.satellite(FunTimeFunestra.LANDSCAPE).volume,
                state.satellite(FunTimeFunestra.LANDSCAPE).muted) == (40, True)
        assert config.landscape.cmd_file.read_text(encoding="utf-8") == "SET_VOLUME 40 1\n"
        assert ops == [WindowOp(op="message", key="Muted", source=SOURCE_LANDSCAPE)]

        state, _ops = dispatch_command("landscape_volume_up", state, config)
        assert (state.satellite(FunTimeFunestra.LANDSCAPE).volume,
                state.satellite(FunTimeFunestra.LANDSCAPE).muted) == (50, False)

    def test_in_origenerator_mode_the_sound_is_still_the_funestras_own(self, tmp_path):
        config = _make_config(tmp_path)

        dispatch_command("portrait_mute", _handed_over(FunTimeFunestra.PORTRAIT), config)

        assert _origenerator_cmds(config) == []
        assert _cmds(config, FunTimeFunestra.PORTRAIT) == ["SET_VOLUME 100 1"]

    def test_bare_sound_words_reach_whichever_funestra_was_addressed_last(self):
        assert parse_command("active_mute").aimed_at_the_active(1).text == "audio_mute"
        assert parse_command("active_unmute").aimed_at_the_active(1).text == "audio_unmute"
        assert parse_command("active_volume_down").aimed_at_the_active(1).text == "audio_volume_down"
        assert parse_command("active_volume_up").aimed_at_the_active(3).text == "landscape_volume_up"


def test_the_hosted_apps_own_words_said_bare_with_the_main_funestra_targeted_reach_it(tmp_path):
    config = _make_config(tmp_path)

    _new_state, ops = dispatch_command(say_command("active", "populate on"), _up(), config)

    assert ops == []
    assert _origenerator_cmds(config) == ["PORTRAIT_SAY:populate on"]



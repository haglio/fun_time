from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest
from app_support import osr2_hold
from funestra.hud_placement import HudCorner, HudEdge
from funestra.main_hud import OSR2_CONTROL_BUTTONS, OSR2_DRIVING, OSR2_PARKED, OSR2_RETRACTED
from funestra.modes import LengthMode, MainMode, SatellitesMode

from fun_time import genaumacher_save
from fun_time.bridge_records import BridgeConfig, Op, WindowOp
from fun_time.command_dispatch import dispatch_command, routes_to_origenerator
from fun_time.command_value import parse_command
from fun_time.config import SatelliteChannel
from fun_time.content import Noun, WebProvider, load_content, load_web_providers
from fun_time.crown import Crown
from fun_time.event_log import FAVORITE, MESSAGE, SOURCE_LANDSCAPE, SOURCE_MAIN, SOURCE_PORTRAIT
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.genaumacher_save import _genaumacher_project_dir
from fun_time.lock_hud import nav_map_cells
from fun_time.loopback_server import omnipause_url
from fun_time.main_list import MainListSettings
from fun_time.main_list_builds import MainListBuild
from fun_time.media_actions import make_web_url_from_path
from fun_time.media_metadata import (
    is_favorite,
    load_metadata,
    mark_favorite,
    metadata_path_for,
    normalize_path_key,
)
from fun_time.mode_plan import EnhancementLevel
from fun_time.modes import write_playlist_file
from fun_time.origenerator_switches import RfbMode, hosted_satellites
from fun_time.satellite_commands import _discard, _toggle_lock
from fun_time.satellite_groups import _satellite_group_index, cancel_lock
from fun_time.shared_state import BridgeState, SatelliteState
from fun_time.voice_commands import VOICE_COMMANDS, build_voice_commands
from fun_time.watch_stats import load_watch_stats
from main_funestra.genau_verbs import genau_f_mode
from tests.hosted_shows import publish_the_show


def _make_config(tmp_path: Path, *, vr_main_funestra: bool = False) -> BridgeConfig:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    trash_dir = tmp_path / "trash"
    trash_dir.mkdir(exist_ok=True)
    return BridgeConfig(
        vr_main_funestra=vr_main_funestra,
        portrait=SatelliteChannel.in_state_dir(state_dir, FunTimeFunestra.PORTRAIT,
                                               sources=str(tmp_path / "portrait")),
        landscape=SatelliteChannel.in_state_dir(state_dir, FunTimeFunestra.LANDSCAPE,
                                                sources=str(tmp_path / "landscape")),
        trash_dir=trash_dir,
        state_dir=state_dir,
        main_sources=str(tmp_path / "primary"),
        broker_mode_file=state_dir / "broker_mode.txt",
        genau_cmd_file=state_dir / "genau_cmd.txt",
        genau_paused_file=state_dir / "genau_paused.txt",
        genau_status_file=state_dir / "genau_status.txt",
        audio_paused_file=state_dir / "audio_paused.txt",
        audio_volume_file=state_dir / "audio_volume.txt",
        main_funestra_cmd_file=state_dir / "main_player_cmd.txt",
        main_funestra_paused_file=state_dir / "main_player_paused.txt",
        main_funestra_status_file=state_dir / "main_player_status.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
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


def _tab_page(op_key: str) -> str:
    assert op_key.startswith("file:///"), op_key
    return Path(url2pathname(urlparse(op_key).path)).read_text(encoding="utf-8")


def test_portrait_lock_opens_a_landing_page_not_the_site(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=False))


    path = rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4"
    _set_current(config, 2, path)
    with patch("fun_time.lock.mark_favorite"):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    assert _cmds(config, 2) == ["LOCK_ON"]
    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert len(rfb_ops) == 1
    assert f'"{make_web_url_from_path(path, load_web_providers())}"' in _tab_page(rfb_ops[0].key)


def test_lock_landing_page_plays_the_locked_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=False))

    video = tmp_path / "videos" / load_web_providers()[0].marker / "abc_123.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")

    _set_current(config, 2, str(video))
    with patch("fun_time.lock.mark_favorite"):
        _, ops = dispatch_command("portrait_lock", state, config)

    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert f'"{video.as_uri()}"' in _tab_page(rfb_ops[0].key)


def test_locking_the_same_video_twice_reuses_one_landing_page(tmp_path: Path):
    config = _make_config(tmp_path)

    keys = []
    for _ in range(2):
        _set_current(config, 2, rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4")
        with patch("fun_time.lock.mark_favorite"):
            _, ops = dispatch_command("portrait_lock", _make_state(portrait=SatelliteState(locked=False)), config)
        keys += [op.key for op in ops if op.op == "open_rfb_tab"]

    assert keys[0] == keys[1]
    assert len(list((config.state_dir / "rfb_tabs").glob("*.html"))) == 1


def test_portrait_lock_records_a_lock_watch_event(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=False))
    video = tmp_path / "clip.mp4"
    video.write_text("x", encoding="utf-8")

    _set_current(config, 2, str(video))
    with patch("fun_time.lock.mark_favorite"):
        dispatch_command("portrait_lock", state, config)

    stats = load_watch_stats(config.state_dir / "watch_stats.json")
    assert stats[normalize_path_key(str(video))]["locks"] == 1


def test_portrait_unlock_records_no_watch_event(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=True))

    _set_current(config, 2, str(tmp_path / "clip.mp4"))
    dispatch_command("portrait_lock", state, config)

    assert _cmds(config, 2) == ["LOCK_OFF", "NEXT"]
    assert not (config.state_dir / "watch_stats.json").exists()


def test_the_locks_landing_page_polls_the_port_this_session_serves_on(tmp_path: Path):
    config = replace(_make_config(tmp_path), loopback_port=8771)
    state = _make_state(portrait=SatelliteState(locked=False))


    _set_current(config, 2, rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4")
    with patch("fun_time.lock.mark_favorite"):
        _new_state, ops = dispatch_command("portrait_lock", state, config)

    [rfb_op] = [op for op in ops if op.op == "open_rfb_tab"]
    assert omnipause_url(8771) in _tab_page(rfb_op.key)


def test_a_lock_on_a_video_no_website_made_asks_origenerator_to_go_to_it(tmp_path: Path):
    config = _origenerator_config(tmp_path)
    video = r"C:\videos\made here\gen_00042_.mp4"
    _set_current(config, 2, video)

    dispatch_command("portrait_lock", _up(), config)

    assert _origenerator_cmds(config) == [f"GO_TO|{video}"]


def test_a_lock_on_a_video_a_website_made_leaves_origenerator_out_of_it(tmp_path: Path):
    config = _origenerator_config(tmp_path)
    _set_current(config, 2, rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4")

    _state, ops = dispatch_command("portrait_lock", _up(), config)

    assert [op.op for op in ops] == ["open_rfb_tab"]
    assert _origenerator_cmds(config) == []


def test_a_lock_on_a_video_a_website_made_puts_the_browser_back_over_origenerator(
        tmp_path: Path):
    config = _origenerator_config(tmp_path)
    _set_current(config, 2, rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4")

    state, ops = dispatch_command("portrait_lock", _up(rfb_mode=RfbMode.ORIGENERATOR), config)

    assert state.rfb_mode is RfbMode.RFB
    assert [op.op for op in ops] == ["hide_window", "restack_rfb", "open_rfb_tab"]


def test_a_lock_asks_nothing_of_an_origenerator_still_starting(tmp_path: Path):
    config = _origenerator_config(tmp_path)
    _set_current(config, 2, r"C:\videos\made here\gen_00042_.mp4")

    dispatch_command("portrait_lock", _make_state(origenerator_ready=False), config)

    assert _origenerator_cmds(config) == []


def test_portrait_lock_no_open_rfb_tab_op_for_unknown_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=False))

    _set_current(config, 2, r"C:\videos\other\xyz.mp4")
    with patch("fun_time.lock.mark_favorite"):
        new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    assert not any(op.op == "open_rfb_tab" for op in ops)


def test_portrait_lock_no_open_rfb_tab_op_when_unlocking(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=True))

    _set_current(config, 2, r"C:\videos\provider2\abc_123.mp4")
    new_state, ops = dispatch_command("portrait_lock", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert not any(op.op == "open_rfb_tab" for op in ops)


def test_landscape_lock_emits_open_rfb_tab_op_for_known_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(locked=False))


    path = rf"C:\videos\{load_web_providers()[0].marker}\def_456.mp4"
    _set_current(config, 3, path)
    with patch("fun_time.lock.mark_favorite"):
        new_state, ops = dispatch_command("landscape_lock", state, config)

    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).locked is True
    assert _cmds(config, 3) == ["LOCK_ON"]
    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert len(rfb_ops) == 1
    assert f'"{make_web_url_from_path(path, load_web_providers())}"' in _tab_page(rfb_ops[0].key)


def test_landscape_lock_emits_regen_url_when_metadata_present(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos" / "2D" / "AI"
    metadata_root = tmp_path / "videos" / "metadata"
    rel = Path("2_outbox") / "upscaled_by_orientation" / "landscape" / "provider"
    video = media_root / rel / "vid_topaz.mp4"
    meta_file = metadata_root / "2D" / "AI" / rel / "vid_topaz.json"
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps({"video": {"prompt": "hi", "model": "Realism"}}), encoding="utf-8")
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")

    config = _make_config(tmp_path)
    config.regen_media_root = media_root
    config.regen_metadata_root = metadata_root
    state = _make_state(landscape=SatelliteState(locked=False))

    providers = (WebProvider(marker="provider", gallery_url="https://example.com/image/{id}"),)

    _set_current(config, 3, str(video))
    with patch("fun_time.lock.mark_favorite"), patch(
        "fun_time.lock.load_web_providers", return_value=providers
    ):
        _new_state, ops = dispatch_command("landscape_lock", state, config)

    rfb_ops = [op for op in ops if op.op == "open_rfb_tab"]
    assert len(rfb_ops) == 1
    assert '"https://example.com/video#ft=' in _tab_page(rfb_ops[0].key)


def test_portrait_lock_toggles_lock_on(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=False))

    _set_current(config, 2, "C:\\clips\\portrait.mp4")
    with patch("fun_time.lock.mark_favorite"):
        new_state, _ops = dispatch_command("portrait_lock", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
    assert _cmds(config, 2) == ["LOCK_ON"]


def test_portrait_lock_toggles_lock_off(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=True))

    _set_current(config, 2, "C:\\clips\\portrait.mp4")
    new_state, _ops = dispatch_command("portrait_lock", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert _cmds(config, 2) == ["LOCK_OFF", "NEXT"]


def test_landscape_lock_toggles_lock_on(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(locked=False))

    _set_current(config, 3, "C:\\clips\\landscape.mp4")
    with patch("fun_time.lock.mark_favorite"):
        new_state, _ops = dispatch_command("landscape_lock", state, config)

    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).locked is True
    assert _cmds(config, 3) == ["LOCK_ON"]


@pytest.mark.parametrize(("command", "funestra"), [
    ("portrait_lock_on", FunTimeFunestra.PORTRAIT), ("landscape_lock_on", FunTimeFunestra.LANDSCAPE)])
def test_a_spoken_lock_locks_an_unlocked_satellite(command, funestra, tmp_path: Path):
    config = _make_config(tmp_path)
    _set_current(config, funestra, "C:\\clips\\scene one.mp4")

    with patch("fun_time.lock.mark_favorite"):
        new_state, _ops = dispatch_command(command, _make_state(), config)

    assert new_state.satellite(funestra).locked is True
    assert _cmds(config, funestra) == ["LOCK_ON"]


@pytest.mark.parametrize(("command", "funestra"), [
    ("portrait_lock_off", FunTimeFunestra.PORTRAIT), ("landscape_lock_off", FunTimeFunestra.LANDSCAPE)])
def test_a_spoken_unlock_unlocks_a_locked_satellite(command, funestra, tmp_path: Path):
    config = _make_config(tmp_path)
    _set_current(config, funestra, "C:\\clips\\scene one.mp4")
    locked = SatelliteState(locked=True)

    new_state, _ops = dispatch_command(
        command, _make_state(portrait=locked, landscape=locked), config)

    assert new_state.satellite(funestra).locked is False
    assert _cmds(config, funestra) == ["LOCK_OFF", "NEXT"]


@pytest.mark.parametrize(("command", "locked"), [
    ("portrait_lock_on", True), ("portrait_lock_off", False),
    ("landscape_lock_on", True), ("landscape_lock_off", False)])
def test_a_spoken_lock_or_unlock_leaves_a_satellite_already_that_way_alone(
        command, locked, tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=locked),
                        landscape=SatelliteState(locked=locked))

    new_state, ops = dispatch_command(command, state, config)

    assert [new_state.satellite(satellite).locked for satellite in FunTimeFunestra.SATELLITES] == [locked] * 2
    assert ops == []
    assert _cmds(config, FunTimeFunestra.PORTRAIT) == _cmds(config, FunTimeFunestra.LANDSCAPE) == []


def test_portrait_lock_returns_to_the_video_that_was_playing_when_spoken(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=False))
    meant = "C:\\clips\\meant.mp4"
    now_playing = "C:\\clips\\advanced_to.mp4"

    _set_current(config, 2, now_playing)
    with patch("fun_time.lock.mark_favorite") as favs:
        new_state, _ops = dispatch_command("portrait_lock", state, config, target_path=meant)

    assert _cmds(config, 2) == [f"PLAY_FILE {meant}", "LOCK_ON"]
    assert favs.call_args[0][0] == meant
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is True


def test_portrait_trash_discards_the_video_that_was_playing_when_spoken(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=False))
    meant = "C:\\clips\\meant.mp4"
    now_playing = "C:\\clips\\advanced_to.mp4"
    _set_current(config, 2, now_playing)
    with (
        patch("fun_time.satellite_commands.unfavorite") as favs,
        patch("fun_time.satellite_commands.move_to_trash") as trash,
    ):
        dispatch_command("portrait_trash", state, config, target_path=meant)

    assert _cmds(config, 2) == [f"PLAY_FILE {meant}", "TRASH"]
    assert favs.call_args[0][0] == meant
    assert trash.call_args[0][1] == Path(meant)


def test_portrait_trash_unlocks_and_discards(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=True))

    _set_current(config, 2, "C:\\clips\\portrait.mp4")
    with (
        patch("fun_time.satellite_commands.unfavorite"),
        patch("fun_time.satellite_commands.move_to_trash"),
    ):
        new_state, _ops = dispatch_command("portrait_trash", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert _cmds(config, 2) == ["LOCK_OFF", "TRASH"]


def test_portrait_trash_queues_a_trash_verb_and_condemns_the_video(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=False))

    _set_current(config, 2, "C:\\clips\\portrait.mp4")
    with (
        patch("fun_time.satellite_commands.unfavorite") as favs,
        patch("fun_time.satellite_commands.move_to_trash") as trash,
    ):
        dispatch_command("portrait_trash", state, config)

    assert _cmds(config, 2) == ["TRASH"]
    assert favs.call_args[0][0] == "C:\\clips\\portrait.mp4"
    assert trash.call_args[0][1] == Path("C:\\clips\\portrait.mp4")


def test_portrait_prev_cancels_lock_and_queues_prev(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=True))

    new_state, _ops = dispatch_command("portrait_prev", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert _cmds(config, 2) == ["LOCK_OFF", "PREV"]


def test_portrait_next_queues_next(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=False))

    dispatch_command("portrait_next", state, config)

    assert _cmds(config, 2) == ["NEXT"]


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


def _upscaled_with_an_original(tmp_path: Path) -> tuple[Path, Path, Path]:
    media_root = tmp_path / "videos" / "2D" / "AI"
    upscale = (media_root / "2_outbox" / "upscaled_by_orientation" / "portrait"
               / "provider" / "clip_topaz.mp4")
    original = media_root / "1_sorted" / "provider" / "portrait" / "clip.mp4"
    for path in (upscale, original):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    return media_root, upscale, original


def test_a_satellite_version_step_sends_the_videos_renditions_to_that_funestra(tmp_path: Path):
    media_root, upscale, original = _upscaled_with_an_original(tmp_path)
    config = _make_config(tmp_path)
    config.regen_media_root = media_root
    _set_current(config, FunTimeFunestra.PORTRAIT, str(upscale))

    dispatch_command("portrait_cycle_version", _make_state(), config)
    dispatch_command("portrait_cycle_version_back", _make_state(), config)

    assert _cmds(config, FunTimeFunestra.PORTRAIT) == [
        f"NEXT_VERSION {upscale}|{original}",
        f"PREV_VERSION {upscale}|{original}",
    ]


def test_a_spoken_version_step_goes_back_to_the_video_the_speaker_saw(tmp_path: Path):
    media_root, upscale, original = _upscaled_with_an_original(tmp_path)
    config = _make_config(tmp_path)
    config.regen_media_root = media_root
    _set_current(config, FunTimeFunestra.PORTRAIT, str(tmp_path / "moved on.mp4"))

    dispatch_command("portrait_cycle_version", _make_state(), config, target_path=str(upscale))

    assert _cmds(config, FunTimeFunestra.PORTRAIT) == [
        f"PLAY_FILE {upscale}",
        f"NEXT_VERSION {upscale}|{original}",
    ]


def test_a_satellite_version_step_says_so_when_the_video_has_only_itself(tmp_path: Path):
    media_root, upscale, original = _upscaled_with_an_original(tmp_path)
    original.unlink()
    config = _make_config(tmp_path)
    config.regen_media_root = media_root
    _set_current(config, FunTimeFunestra.PORTRAIT, str(upscale))

    _state, ops = dispatch_command("portrait_cycle_version", _make_state(), config)

    assert _cmds(config, FunTimeFunestra.PORTRAIT) == []
    assert [op.key for op in ops] == ["No other version"]


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


def test_a_length_button_is_ignored_in_genau_mode(tmp_path: Path):
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


def test_a_satellites_skip_ahead_reaches_it_while_it_has_the_osr2(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(osr2_funestra=FunTimeFunestra.LANDSCAPE)

    dispatch_command("landscape_funscript_jump", state, config)

    assert config.landscape.cmd_file.read_text(encoding="utf-8") == "JUMP_TO_FUNSCRIPT\n"


def test_a_satellites_skip_ahead_is_refused_while_another_funestra_has_the_osr2(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(osr2_funestra=FunTimeFunestra.MAIN)

    _, ops = dispatch_command("portrait_funscript_jump", state, config)

    assert not config.portrait.cmd_file.exists()
    assert ops == [WindowOp(op="message", key="The portrait Satellite doesn't have the OSR2",
                            level=logging.WARNING)]


def test_next_funscripted_writes_next_funscripted(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_next_funscripted", _make_state(main_mode=MainMode.KINO), config)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == "NEXT_FUNSCRIPTED\n"


def test_video_nav_inert_in_genau_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    dispatch_command("main_funestra_compilation", _make_state(main_mode=MainMode.GENAU), config)
    assert not config.main_funestra_cmd_file.exists()


def test_funscript_nav_inert_in_genau_mode(tmp_path: Path):
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


@pytest.mark.parametrize(("command", "verb"), [
    ("portrait_funscript_jump", "JUMP_TO_FUNSCRIPT"),
    ("landscape_next_funscripted", "NEXT_FUNSCRIPTED"),
])
def test_a_satellites_funscript_move_goes_to_that_satellite_as_the_nudges_do(
        tmp_path: Path, command: str, verb: str):
    config = _make_config(tmp_path)
    funestra = FunTimeFunestra.PORTRAIT if command.startswith("portrait") else FunTimeFunestra.LANDSCAPE

    dispatch_command(command, _make_state(osr2_funestra=funestra), config)

    assert _cmds(config, funestra) == [verb]


def test_landscape_prev_cancels_lock_and_queues_prev(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(locked=True))

    new_state, _ops = dispatch_command("landscape_prev", state, config)

    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
    assert _cmds(config, 3) == ["LOCK_OFF", "PREV"]


def test_landscape_next_drives_its_own_satellite_not_portrait(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(locked=True))

    new_state, _ops = dispatch_command("landscape_next", state, config)

    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
    assert _cmds(config, 3) == ["LOCK_OFF", "NEXT"]
    assert _cmds(config, 2) == []


def test_landscape_trash_discards_from_its_own_satellite_not_portrait(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(locked=True))

    _set_current(config, 3, "C:\\clips\\landscape.mp4")
    with (
        patch("fun_time.satellite_commands.unfavorite"),
        patch("fun_time.satellite_commands.move_to_trash"),
    ):
        new_state, _ops = dispatch_command("landscape_trash", state, config)

    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
    assert _cmds(config, 3) == ["LOCK_OFF", "TRASH"]
    assert _cmds(config, 2) == []


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


def test_a_fresh_session_starts_with_the_primary_active():
    assert BridgeState().active_funestra == 1


def test_portrait_command_sets_active_funestra_to_portrait(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(active_funestra=3)

    new_state, _ops = dispatch_command("portrait_next", state, config)

    assert new_state.active_funestra == 2


def test_landscape_command_sets_active_funestra_to_landscape(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(locked=False), active_funestra=2)

    _set_current(config, 3, "C:\\clips\\l.mp4")
    with patch("fun_time.lock.mark_favorite"):
        new_state, _ops = dispatch_command("landscape_lock", state, config)

    assert new_state.active_funestra == 3


def test_primary_nav_sets_active_funestra_to_primary(tmp_path: Path):
    config = _make_config(tmp_path)

    for command in ("main_next", "main_prev"):
        new_state, _ops = dispatch_command(command, _make_state(active_funestra=3), config)
        assert new_state.active_funestra == 1, command


def test_naming_a_funestras_f_mode_makes_it_the_active_one(tmp_path: Path):
    config = _make_config(tmp_path)

    for suffix in ("", "_on", "_off"):
        new_state, _ops = dispatch_command(
            f"main_fmode{suffix}", _make_state(active_funestra=3), config)
        assert new_state.active_funestra == 1, suffix
        new_state, _ops = dispatch_command(
            f"portrait_fmode{suffix}", _make_state(active_funestra=1), config)
        assert new_state.active_funestra == 2, suffix


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


def test_minimize_parks_that_funestras_window(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=True))

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


def test_fmode_toggle_enables_every_funestra_from_disabled(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, _ops, mock_fmode = _dispatch_fmode("fmode_toggle", _make_state(), config)

    assert mock_fmode.call_args.kwargs["funestras"] == (
        FunTimeFunestra.MAIN, FunTimeFunestra.PORTRAIT, FunTimeFunestra.LANDSCAPE)
    assert (new_state.main_scripted_filter, new_state.satellite(FunTimeFunestra.PORTRAIT).favorites_filter,
            new_state.satellite(FunTimeFunestra.LANDSCAPE).favorites_filter) == (True, True, True)


def test_fmode_toggle_narrows_the_rest_rather_than_lifting_what_is_narrowed(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(favorites_filter=True))

    new_state, _ops, mock_fmode = _dispatch_fmode("fmode_toggle", state, config)

    assert (new_state.main_scripted_filter, new_state.satellite(FunTimeFunestra.PORTRAIT).favorites_filter,
            new_state.satellite(FunTimeFunestra.LANDSCAPE).favorites_filter) == (True, True, True)
    assert mock_fmode.call_args.kwargs["funestras"] == (FunTimeFunestra.MAIN, FunTimeFunestra.LANDSCAPE)


def test_fmode_toggle_lifts_it_once_every_funestra_is_in_it(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(favorites_filter=True), portrait=SatelliteState(favorites_filter=True), main_scripted_filter=True)

    new_state, _ops, _mock = _dispatch_fmode("fmode_toggle", state, config)

    assert (new_state.main_scripted_filter, new_state.satellite(FunTimeFunestra.PORTRAIT).favorites_filter,
            new_state.satellite(FunTimeFunestra.LANDSCAPE).favorites_filter) == (False, False, False)


def _f_modes(state: BridgeState) -> tuple[bool, bool, bool]:
    return (state.main_scripted_filter, state.satellite(FunTimeFunestra.PORTRAIT).favorites_filter,
            state.satellite(FunTimeFunestra.LANDSCAPE).favorites_filter)


@pytest.mark.parametrize("funestra", list(FunTimeFunestra))
def test_a_named_funestra_goes_into_f_mode_alone(tmp_path: Path, funestra: FunTimeFunestra):
    config = _make_config(tmp_path)

    new_state, _ops, mock_fmode = _dispatch_fmode(
        f"{funestra.label}_fmode", _make_state(), config)

    assert list(_f_modes(new_state)).count(True) == 1
    assert _f_modes(new_state)[list(FunTimeFunestra).index(funestra)] is True
    assert mock_fmode.call_args.kwargs["funestras"] == (funestra,)


def test_f_mode_on_genau_keeps_its_favorites_and_leaves_kinos_list_alone(tmp_path: Path):
    config = _make_config(tmp_path)

    state, ops, mock_fmode = _dispatch_fmode("main_fmode", _make_state(main_mode=MainMode.GENAU), config)

    assert (state.genau_favorites_filter, state.main_scripted_filter) == (True, False)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8") == f"{genau_f_mode(True)}\n"
    assert mock_fmode.call_args.kwargs["funestras"] == ()
    assert ops == [WindowOp(op="message", key="F-Mode enabled", source=SOURCE_MAIN, level=FAVORITE)]


def test_the_on_and_off_forms_assert_a_state_rather_than_toggling(tmp_path: Path):
    config = _make_config(tmp_path)

    stays_on, _ops, _mock = _dispatch_fmode(
        "portrait_fmode_on", _make_state(portrait=SatelliteState(favorites_filter=True)), config)
    stays_off, _ops, _mock = _dispatch_fmode(
        "portrait_fmode_off", _make_state(portrait=SatelliteState(favorites_filter=False)), config)

    assert stays_on.satellite(FunTimeFunestra.PORTRAIT).favorites_filter is True
    assert stays_off.satellite(FunTimeFunestra.PORTRAIT).favorites_filter is False


def test_asserting_the_state_a_funestra_is_already_in_rebuilds_nothing(tmp_path: Path):
    config = _make_config(tmp_path)

    _state, _ops, mock_fmode = _dispatch_fmode(
        "portrait_fmode_on", _make_state(portrait=SatelliteState(favorites_filter=True)), config)

    assert mock_fmode.call_args.kwargs["funestras"] == ()


def test_fmode_flashes_a_green_confirmation_when_it_turns_on(tmp_path: Path):
    config = _make_config(tmp_path)

    _state, ops, _mock = _dispatch_fmode("fmode_toggle", _make_state(), config)

    assert ops == [WindowOp(op="message", key="F-Mode enabled", source="system",
                            level=FAVORITE)]


def test_fmode_flashes_a_plain_white_message_when_it_turns_off(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(favorites_filter=True), portrait=SatelliteState(favorites_filter=True), main_scripted_filter=True)

    _state, ops, _mock = _dispatch_fmode("fmode_toggle", state, config)

    assert ops == [
        WindowOp(op="message", key="F-Mode disabled", source="system", level=MESSAGE),
    ]


def test_a_sided_fmode_flashes_on_that_funestras_own_display(tmp_path: Path):
    config = _make_config(tmp_path)

    _state, ops, _mock = _dispatch_fmode("landscape_fmode", _make_state(), config)

    assert ops == [WindowOp(op="message", key="F-Mode enabled", source="landscape",
                            level=FAVORITE)]


@pytest.mark.parametrize("funestra", FunTimeFunestra.SATELLITES)
def test_favorites_said_to_a_satellite_in_kino_mode_flips_its_f_mode_as_a_show_does(
        tmp_path: Path, funestra: FunTimeFunestra):
    config = _make_config(tmp_path)

    on, ops, mock_fmode = _dispatch_fmode(f"{funestra.label}_say_favorites", _make_state(), config)
    off, _ops, _mock = _dispatch_fmode(f"{funestra.label}_say_favorites", on, config)

    assert mock_fmode.call_args.kwargs["funestras"] == (funestra,)
    assert (on.satellite(funestra).favorites_filter, off.satellite(funestra).favorites_filter) == (
        True, False)
    assert ops == [WindowOp(op="message", key="F-Mode enabled", source=funestra.label,
                            level=FAVORITE)]


def test_fmode_passes_each_satellites_current_order(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(latest=False), portrait=SatelliteState(latest=True))

    _state, _ops, mock_fmode = _dispatch_fmode("fmode_toggle", state, config)

    satellites = mock_fmode.call_args.kwargs["satellites"]
    assert satellites[FunTimeFunestra.PORTRAIT].recent is True
    assert satellites[FunTimeFunestra.LANDSCAPE].recent is False


def test_fmode_passes_the_metadata_root_for_group_collapse(tmp_path: Path):
    config = replace(_make_config(tmp_path), regen_metadata_root=tmp_path / "metadata")

    _state, _ops, mock_fmode = _dispatch_fmode("fmode_toggle", _make_state(), config)

    assert mock_fmode.call_args.kwargs["main_build"].main_list.metadata_root == tmp_path / "metadata"


def _filter_result(count=1, applied=True, message="ok"):
    return type("R", (), {"count": count, "applied": applied, "log_message": message})()


def test_filter_command_scopes_to_one_satellite(tmp_path: Path):
    config = replace(
        _make_config(tmp_path),
        regen_media_root=tmp_path / "media",
        regen_metadata_root=tmp_path / "metadata",
    )
    state = _make_state()

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        new_state, ops = dispatch_command("filter_portrait_alpha", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).filter == "alpha"
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).filter == ""
    assert mock_filter.call_count == 1
    kwargs = mock_filter.call_args.kwargs
    assert kwargs["funestra"] is FunTimeFunestra.PORTRAIT
    assert kwargs["build"].filter_query == "alpha"
    assert kwargs["channel"].cmd_file == config.satellite(2).cmd_file
    assert kwargs["build"].sources == config.portrait.sources
    assert any(op.op == "message" for op in ops)


def test_no_loop_returns_to_browse_keeping_the_filter(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(filter="alpha"))

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=["C:/v/x.mp4"]) as mock_browse:
        new_state, ops = dispatch_command("portrait_no_loop", state, config)

    assert mock_browse.call_args.args[0].filter_query == "alpha"
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).filter == "alpha"
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)
    assert [op.key for op in ops if op.op == "message"] == ["Loop off"]


def test_play_video_command_switches_the_satellite_to_the_path(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()
    path = r"C:\vids\portrait\pick_me.mp4"

    new_state, ops = dispatch_command(f"portrait_play_video|{path}", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {path}"]
    assert new_state.active_funestra == 2
    assert [op.source for op in ops if op.op == "message"] == ["portrait"]


def test_a_video_sent_to_a_satellite_screen_carries_its_funscript(tmp_path: Path):
    config = _make_config(tmp_path)
    video = tmp_path / "videos" / "videos" / "portrait" / "pick_me.mp4"
    script = tmp_path / "videos" / "scripts" / "scripts" / "portrait" / "pick_me.funscript"
    script.parent.mkdir(parents=True)
    script.write_text("{}", encoding="utf-8")

    dispatch_command(f"portrait_play_video|{video}", _make_state(), config)

    assert _cmds(config, 2) == [f"PLAY_FILE {video}\t{script}"]


def test_lock_video_command_when_already_locked_switches_and_stays_locked(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=True))
    path = r"C:\vids\portrait\lock_me.mp4"

    new_state, _ops = dispatch_command(f"portrait_lock_video|{path}", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {path}"]
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is True


def test_a_double_click_on_a_satellite_already_locked_favorites_the_video_it_picks(
        tmp_path: Path):
    config = _make_config(tmp_path)
    config.regen_metadata_root = tmp_path / "metadata"
    _set_current(config, 2, str(tmp_path / "vids" / "portrait" / "held.mp4"))
    picked = str(tmp_path / "vids" / "portrait" / "lock_me.mp4")

    dispatch_command(f"portrait_lock_video|{picked}",
                     _make_state(portrait=SatelliteState(locked=True)), config)

    assert is_favorite(picked, config.regen_metadata_root)


def _nav_config(tmp_path: Path) -> tuple[BridgeConfig, dict[str, str]]:
    return _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
        "subject_a_zeta": _cycle_meta("111", "Zeta Massage"),
    })


def test_nav_down_switches_to_the_first_action_and_freezes_the_anchor(tmp_path: Path):
    config, paths = _nav_config(tmp_path)
    state = _make_state()

    _set_current(config, 2, paths["subject_a"])
    new_state, ops = dispatch_command("portrait_nav_down", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_a_zeta']}"]
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == paths["subject_a"]
    assert new_state.active_funestra == 2
    assert [op.source for op in ops if op.op == "message"] == ["portrait"]


def test_nav_continues_down_the_column_from_the_frozen_anchor(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_a_beta": _cycle_meta("111", "Beta Massage"),
        "subject_a_zeta": _cycle_meta("111", "Zeta Massage"),
    })
    state = _make_state(portrait=SatelliteState(nav_anchor=paths["subject_a"]))

    _set_current(config, 2, paths["subject_a_beta"])
    new_state, _ops = dispatch_command("portrait_nav_down", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_a_zeta']}"]
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == paths["subject_a"]


def test_nav_past_the_last_action_wraps_round_to_the_anchor(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_a_zeta": _cycle_meta("111", "Zeta Massage"),
    })
    state = _make_state(portrait=SatelliteState(nav_anchor=paths["subject_a"]))

    _set_current(config, 2, paths["subject_a_zeta"])
    new_state, _ops = dispatch_command("portrait_nav_down", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_a']}"]
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == paths["subject_a"]


def test_nav_onto_an_axis_with_nowhere_to_go_is_a_dead_end(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state(portrait=SatelliteState(nav_anchor=paths["subject_a"]))

    _set_current(config, 2, paths["subject_a"])
    _new_state, ops = dispatch_command("portrait_nav_down", state, config)

    assert _cmds(config, 2) == []
    dead_end = [op for op in ops if op.op == "message"]
    assert dead_end
    assert dead_end[0].level == logging.WARNING


def test_nav_down_from_a_seed_dives_into_that_seeds_own_acts(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
        "subject_b_zeta": _cycle_meta("222", "Zeta Massage"),
    })
    state = _make_state(portrait=SatelliteState(nav_anchor=paths["subject_a"]))

    _set_current(config, 2, paths["subject_b"])
    new_state, _ops = dispatch_command("portrait_nav_down", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_b_zeta']}"]
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == paths["subject_b"]


def test_nav_down_from_a_seed_with_no_other_acts_is_a_dead_end(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_a_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state(portrait=SatelliteState(nav_anchor=paths["subject_a"]))

    _set_current(config, 2, paths["subject_b"])
    new_state, ops = dispatch_command("portrait_nav_down", state, config)

    assert _cmds(config, 2) == []
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == paths["subject_a"]
    dead_end = [op for op in ops if op.op == "message"]
    assert dead_end
    assert dead_end[0].level == logging.WARNING


def test_nav_re_anchors_after_the_satellite_drifts_off_the_map(tmp_path: Path):
    config, paths = _nav_config(tmp_path)
    state = _make_state(portrait=SatelliteState(nav_anchor="C:/vids/portrait/gone.mp4"))

    _set_current(config, 2, paths["subject_a"])
    new_state, _ops = dispatch_command("portrait_nav_down", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_a_zeta']}"]
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == paths["subject_a"]


def test_a_non_nav_satellite_command_clears_the_nav_anchor(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(nav_anchor="C:/vids/portrait/anchor.mp4"))

    new_state, _ops = dispatch_command("portrait_next", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == ""


@pytest.mark.parametrize("command", ["main_next", "main_prev", "main_lock"])
def test_a_main_funestra_command_leaves_both_satellite_anchors_alone(
        tmp_path: Path, command: str):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(nav_anchor="C:/vids/landscape/anchor.mp4"), portrait=SatelliteState(nav_anchor="C:/vids/portrait/anchor.mp4"))

    new_state, _ops = dispatch_command(command, state, config)

    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).nav_anchor == "C:/vids/landscape/anchor.mp4"
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == "C:/vids/portrait/anchor.mp4"


def test_landscape_nav_sets_the_active_funestra(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    new_state, _ops = dispatch_command("landscape_nav_down", state, config)

    assert new_state.active_funestra == 3


def test_filter_command_both_scope_rebuilds_each_satellite(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        new_state, _ops = dispatch_command("filter_satellites_beta_gamma", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).filter == "beta gamma"
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).filter == "beta gamma"
    assert {call.kwargs["funestra"] for call in mock_filter.call_args_list} == set(FunTimeFunestra.SATELLITES)


def test_filter_command_both_scope_messages_each_satellite_under_its_own_source(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        _new_state, ops = dispatch_command("filter_satellites_beta_gamma", state, config)

    messages = [op for op in ops if op.op == "message"]
    assert [op.source for op in messages] == ["portrait", "landscape"]


def test_zero_match_filter_is_not_recorded_in_state(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state()

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=0, applied=False)
        new_state, _ops = dispatch_command("filter_portrait_alpha", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).filter == ""


def test_clear_filter_command_resets_only_its_scope(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(filter="twirling"), portrait=SatelliteState(filter="alpha"))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        new_state, _ops = dispatch_command("portrait_no_filter", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).filter == ""
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).filter == "twirling"
    assert mock_filter.call_args.kwargs["build"].filter_query == ""


def test_fmode_passes_active_filters(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(filter="twirling"), portrait=SatelliteState(filter="alpha"))

    _state, _ops, mock_fmode = _dispatch_fmode("fmode_toggle", state, config)

    satellites = mock_fmode.call_args.kwargs["satellites"]
    assert satellites[FunTimeFunestra.PORTRAIT].filter_query == "alpha"
    assert satellites[FunTimeFunestra.LANDSCAPE].filter_query == "twirling"


def test_recents_passes_the_satellites_filter_and_roots(tmp_path: Path):
    config = replace(
        _make_config(tmp_path),
        regen_media_root=tmp_path / "media",
        regen_metadata_root=tmp_path / "metadata",
    )
    state = _make_state(landscape=SatelliteState(filter="twirling"), portrait=SatelliteState(filter="alpha"))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        dispatch_command("portrait_latest", state, config)

    kwargs = mock_filter.call_args.kwargs
    assert kwargs["build"].recent is True
    assert kwargs["build"].filter_query == "alpha"


def test_recents_reorders_only_the_satellite_it_names(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        state, ops = dispatch_command("portrait_latest", _make_state(), config)

    assert [call.kwargs["funestra"] for call in mock_filter.call_args_list] == [FunTimeFunestra.PORTRAIT]
    assert state.satellite(FunTimeFunestra.PORTRAIT).latest is True
    assert state.satellite(FunTimeFunestra.LANDSCAPE).latest is False
    assert [op.source for op in ops if op.op == "message"] == ["portrait"]


def test_shuffle_puts_one_satellite_back_without_touching_the_other(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(latest=True), portrait=SatelliteState(latest=True))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        new_state, _ops = dispatch_command("landscape_shuffle", state, config)

    assert mock_filter.call_args.kwargs["build"].recent is False
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).latest is False
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).latest is True


def test_a_sided_reorder_drops_that_satellites_lock_and_loop(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=True, loop="seed", nav_map_anchor="C:/v/a.mp4", widen_video="C:/v/a.mp4"))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        new_state, _ops = dispatch_command("portrait_latest", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).loop == ""
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_map_anchor == ""
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).widen_video == ""


def test_a_reorder_starts_the_satellite_at_the_top_of_the_new_order(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        dispatch_command("portrait_latest", _make_state(), config)

    assert mock_filter.call_args.kwargs["start_at_top"] is True


def test_a_filter_leaves_the_video_on_screen_where_it_is(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        dispatch_command("filter_portrait_alpha", _make_state(), config)

    assert mock_filter.call_args.kwargs["start_at_top"] is False


def test_reset_returns_the_satellite_to_every_default(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(locked=True, filter="alpha", latest=True, favorites_filter=True, loop="seed", nav_map_anchor="C:/v/a.mp4", widen_video="C:/v/a.mp4", nav_anchor="C:/v/a.mp4"))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        new_state, _ops = dispatch_command("portrait_reset", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).filter == ""
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).latest is False
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).favorites_filter is False
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).loop == ""
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_map_anchor == ""
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).widen_video == ""
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == ""
    assert mock_filter.call_args.kwargs["build"].filter_query == ""
    assert mock_filter.call_args.kwargs["build"].recent is False
    assert mock_filter.call_args.kwargs["start_at_top"] is True
    assert "LOCK_OFF" in _cmds(config, 2)


def test_reset_rebuilds_the_satellite_wide_rather_than_still_in_f_mode(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(favorites_filter=True))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        dispatch_command("portrait_reset", state, config)

    assert mock_filter.call_args.kwargs["build"].favorites_filter is False


def test_reset_leaves_a_satellite_that_is_already_at_its_defaults_alone(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        state, ops = dispatch_command("portrait_reset", _make_state(), config)

    assert mock_filter.call_count == 0
    assert ops == []
    assert _cmds(config, 2) == []
    assert state.active_funestra == 2


def test_a_satellite_whose_own_sound_was_turned_up_has_nothing_to_reset(tmp_path: Path):
    config = _make_config(tmp_path)
    heard = _make_state().with_satellite(FunTimeFunestra.PORTRAIT, volume=40, muted=False)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        _state, ops = dispatch_command("portrait_reset", heard, config)

    assert (mock_filter.call_count, ops) == (0, [])


@pytest.mark.parametrize("narrowed", [
    SatelliteState(locked=True),
    SatelliteState(filter="alpha"),
    SatelliteState(favorites_filter=True),
    SatelliteState(latest=True),
    SatelliteState(loop="seed"),
    SatelliteState(nav_map_anchor="C:/v/a.mp4"),
    SatelliteState(widen_video="C:/v/a.mp4"),
])
def test_reset_still_fires_for_any_one_thing_left_narrowing_the_satellite(
    tmp_path: Path, narrowed: SatelliteState
):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        _state, ops = dispatch_command(
            "portrait_reset", _make_state(portrait=narrowed), config)

    assert mock_filter.call_count == 1, narrowed
    assert [op.key for op in ops] == ["Reset"]


def _publish_status(status_file: Path, **fields) -> None:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text("".join(f"{key}={value}\n" for key, value in
                                   {"video": "C:/v/a.mp4", **fields}.items()),
                           encoding="utf-8")


def _publish_speed(config: BridgeConfig, funestra: FunTimeFunestra, speed: float) -> None:
    _publish_status(config.satellite(funestra).status_file, speed=speed)


def test_reset_puts_the_satellites_speed_back_to_normal_as_the_main_funestras_does(tmp_path: Path):
    config = _make_config(tmp_path)
    _publish_speed(config, FunTimeFunestra.PORTRAIT, 1.0)
    state = _make_state(portrait=SatelliteState(locked=True))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        dispatch_command("portrait_reset", state, config)

    assert _cmds(config, FunTimeFunestra.PORTRAIT)[-1] == "SET_SPEED 1"


def test_a_satellite_whose_speed_alone_is_off_resets_it_without_reshuffling(tmp_path: Path):
    config = _make_config(tmp_path)
    _publish_speed(config, FunTimeFunestra.PORTRAIT, 1.5)
    remembering_the_rate = _make_state().with_satellite(FunTimeFunestra.PORTRAIT, speed=1.5)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        _state, ops = dispatch_command("portrait_reset", remembering_the_rate, config)

    assert mock_filter.call_count == 0
    assert _cmds(config, FunTimeFunestra.PORTRAIT) == ["SET_SPEED 1"]
    assert [op.key for op in ops] == ["Reset"]


def test_reset_skips_only_the_satellite_that_has_nothing_to_put_back(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(filter="alpha"))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        state, portrait_ops = dispatch_command("portrait_reset", state, config)
        state, landscape_ops = dispatch_command("landscape_reset", state, config)

    assert portrait_ops == []
    assert [op.key for op in landscape_ops] == ["Reset"]
    assert mock_filter.call_count == 1


def test_reset_leaves_the_other_satellite_alone(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(landscape=SatelliteState(locked=True, filter="alpha", latest=True, favorites_filter=True))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        new_state, _ops = dispatch_command("portrait_reset", state, config)

    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).locked is True
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).filter == "alpha"
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).latest is True
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).favorites_filter is True


def test_no_filter_drops_the_filter_and_nothing_else(tmp_path: Path):
    config = _make_config(tmp_path)
    state = _make_state(portrait=SatelliteState(filter="alpha", latest=True))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(count=10)
        new_state, _ops = dispatch_command("portrait_no_filter", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).filter == ""
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).latest is True
    assert mock_filter.call_args.kwargs["build"].filter_query == ""
    assert mock_filter.call_args.kwargs["build"].recent is True


def _cycle_meta(image_seed: str, action: str) -> dict:
    return {
        "video": {"prompt": f"do {action}", "action": action, "seed": "5"},
        "source_image": {"positive_prompt": "subject at the beach", "seed": image_seed},
    }


def _make_grouped_config(
    tmp_path: Path, videos: dict[str, dict | None], funestra: int = 2,
) -> tuple[BridgeConfig, dict[str, str]]:
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    satellite_dir = media_root / ("portrait" if funestra == 2 else "landscape")
    satellite_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, meta in videos.items():
        video = satellite_dir / f"{name}.mp4"
        video.write_text("x", encoding="utf-8")
        paths[name] = str(video)
        if meta is not None:
            sidecar = metadata_path_for(video, metadata_root)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(meta), encoding="utf-8")
    config = _make_config(tmp_path)
    config = replace(
        config,
        regen_media_root=media_root,
        regen_metadata_root=metadata_root,
        **{FunTimeFunestra(funestra).label: replace(config.satellite(funestra),
                                                   sources=str(satellite_dir))},
    )
    return config, paths


def _subject_meta(*, prompt: str = "x, y, z", image_seed: str = "1",
                  action: str = "Alpha", quality: str = "Best") -> dict:
    return {
        "video": {"prompt": f"do {action}", "action": action, "seed": "9"},
        "source_image": {"positive_prompt": prompt, "seed": image_seed, "quality": quality},
    }


def test_portrait_cycle_action_swaps_to_next_action_of_the_group(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
        "other_subject": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_zeta"])
    _new_state, ops = dispatch_command("portrait_cycle_action", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_alpha']}"]
    messages = [(op.key, op.source) for op in ops if op.op == "message"]
    assert messages == [("Action: Alpha", "portrait")]


def test_portrait_cycle_action_cycles_the_video_that_was_playing_when_spoken(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
        "other_subject": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["other_subject"])
    dispatch_command("portrait_cycle_action", state, config, target_path=paths["subject_zeta"])

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_alpha']}"]


def test_portrait_cycle_action_messages_when_video_has_no_siblings(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "loner": _cycle_meta("111", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["loner"])
    _new_state, ops = dispatch_command("portrait_cycle_action", state, config)

    assert _cmds(config, 2) == []
    dead_end = [op for op in ops if op.op == "message"]
    assert [op.key for op in dead_end] == ["No other actions"]
    assert dead_end[0].level == logging.WARNING


def test_a_successful_cycle_action_message_is_an_ordinary_white_notice(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_zeta"])
    _new_state, ops = dispatch_command("portrait_cycle_action", state, config)

    messages = [op for op in ops if op.op == "message"]
    assert messages
    assert all(op.level == MESSAGE for op in messages)


def test_portrait_cycle_action_keeps_an_active_lock(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
    })
    state = _make_state(portrait=SatelliteState(locked=True))

    _set_current(config, 2, paths["subject_zeta"])
    new_state, _ops = dispatch_command("portrait_cycle_action", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_alpha']}"]


def test_portrait_cycle_seed_keeps_an_active_lock(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state(portrait=SatelliteState(locked=True))

    _set_current(config, 2, paths["subject_a"])
    new_state, _ops = dispatch_command("portrait_cycle_seed", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_b']}"]


def test_portrait_cycle_seed_jumps_to_sister_seed_in_playlist(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
        "subject_a_other_action": _cycle_meta("111", "Zeta Massage"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_a"])
    dispatch_command("portrait_cycle_seed", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_b']}"]


def test_portrait_cycle_seed_swaps_in_library_sister_when_none_in_playlist(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "subject_b": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_a"])
    _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['subject_b']}"]
    assert [op.key for op in ops if op.op == "message"] == ["Next seed"]


def test_portrait_cycle_seed_messages_without_seed_siblings(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_a": _cycle_meta("111", "Alpha"),
        "no_meta": None,
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_a"])
    _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    assert _cmds(config, 2) == []
    dead_end = [op for op in ops if op.op == "message"]
    assert [op.key for op in dead_end] == ["No other seeds"]
    assert dead_end[0].level == logging.WARNING


def _scene_meta(*, image_seed: str, quality: str) -> dict:
    return {
        "video": {"prompt": "beach", "action": "Alpha", "seed": "5"},
        "source_image": {"positive_prompt": "subject at the beach", "seed": image_seed, "quality": quality},
    }


def test_portrait_cycle_seed_no_longer_auto_widens(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_best": _scene_meta(image_seed="111", quality="Best"),
        "subject_draft": _scene_meta(image_seed="222", quality="Draft"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_best"])
    _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    assert _cmds(config, 2) == []
    dead = [op for op in ops if op.op == "message"]
    assert [op.key for op in dead] == ["No other seeds"]
    assert dead[0].level == logging.WARNING


def test_more_seeds_widens_the_display_without_switching_the_video(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "cur": _subject_meta(prompt="a, b", action="Gamma"),
        "other": _subject_meta(prompt="a, c", action="Gamma"),
    })
    c = paths["cur"]

    _set_current(config, 2, c)
    state, ops = dispatch_command("portrait_more_seeds", _make_state(), config)

    assert not any(cmd.startswith("PLAY_FILE") for cmd in _cmds(config, 2))
    assert _playlist(config, 2)[0] == c
    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == c
    assert [op.key for op in ops if op.op == "message"] == ["More seeds"]


def test_more_seeds_dead_ends_when_nothing_else_does_this_act(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "cur": _subject_meta(prompt="a", action="Zeta"),
        "other": _subject_meta(prompt="a", image_seed="2", action="Alpha"),
    })
    c = paths["cur"]

    _set_current(config, 2, c)
    state, ops = dispatch_command("portrait_more_seeds", _make_state(), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == ""
    messages = [op for op in ops if op.op == "message"]
    assert [op.key for op in messages] == ["Widening net failed"]
    assert messages[0].level == logging.WARNING


def test_more_seeds_reports_widening_failed_when_the_library_holds_one_video(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {"only": _subject_meta()})

    _set_current(config, 2, paths["only"])
    state, ops = dispatch_command("portrait_more_seeds", _make_state(), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == ""
    messages = [op for op in ops if op.op == "message"]
    assert [op.key for op in messages] == ["Widening net failed"]
    assert messages[0].level == logging.WARNING


def test_more_seeds_starts_looping_the_seeds_it_widened(tmp_path: Path):
    config, a, a2, b = _widened_loop_config(tmp_path)

    _set_current(config, 2, a)
    state, ops = dispatch_command("portrait_more_seeds", _make_state(), config)

    assert sorted(_playlist(config, 2)) == sorted([a, a2, b])
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)
    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == "seed"
    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == a
    assert [op.key for op in ops if op.op == "message"] == ["More seeds"]


def _other_act_widen_config(tmp_path: Path) -> tuple[BridgeConfig, str, str, str, str]:
    config, paths = _make_grouped_config(tmp_path, {
        "a": _subject_meta(image_seed="1"),
        "a2": _subject_meta(image_seed="2"),
        "b": _subject_meta(image_seed="3", action="Theta"),
        "c": _subject_meta(prompt="q", image_seed="1"),
    })
    return config, paths["a"], paths["a2"], paths["b"], paths["c"]


def test_more_seeds_never_queues_a_video_of_another_action(tmp_path: Path):
    config, a, a2, b, c = _other_act_widen_config(tmp_path)

    _set_current(config, 2, a)
    state, _ops = dispatch_command("portrait_more_seeds", _make_state(), config)

    assert sorted(_playlist(config, 2)) == sorted([a, a2, c])
    assert b not in _playlist(config, 2)
    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == a


def test_more_seeds_during_a_seed_loop_widens_the_running_loop(tmp_path: Path):
    config, a, a2, b = _widened_loop_config(tmp_path)
    state = _make_state(portrait=SatelliteState(loop="seed"))

    _set_current(config, 2, a)
    new_state, ops = dispatch_command("portrait_more_seeds", state, config)

    assert sorted(_playlist(config, 2)) == sorted([a, a2, b])
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).widen_video == a
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).loop == "seed"
    assert [op.key for op in ops if op.op == "message"] == ["More seeds"]


def test_portrait_cycle_seed_stays_within_the_current_action(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "a_alpha": _cycle_meta("200", "Alpha"),
        "b_alpha": _cycle_meta("300", "Alpha"),
        "c_gamma": _cycle_meta("250", "Gamma"),
    })
    state = _make_state()

    _set_current(config, 2, paths["a_alpha"])
    _new_state, ops = dispatch_command("portrait_cycle_seed", state, config)

    assert _cmds(config, 2) == [f"PLAY_FILE {paths['b_alpha']}"]
    assert [op.key for op in ops if op.op == "message"] == ["Next seed"]


def test_landscape_cycle_commands_target_the_landscape_funestra(tmp_path: Path):
    config, _paths = _make_grouped_config(tmp_path, {})
    media_root = config.regen_media_root
    landscape_dir = media_root / "landscape"
    landscape_dir.mkdir(parents=True, exist_ok=True)
    videos: dict[str, str] = {}
    for name, meta in (
        ("subject_zeta", _cycle_meta("111", "Zeta Massage")),
        ("subject_alpha", _cycle_meta("111", "Alpha")),
    ):
        video = landscape_dir / f"{name}.mp4"
        video.write_text("x", encoding="utf-8")
        videos[name] = str(video)
        sidecar = metadata_path_for(video, config.regen_metadata_root)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(meta), encoding="utf-8")
    config = replace(config, landscape=replace(config.landscape, sources=str(landscape_dir)))
    state = _make_state(landscape=SatelliteState(locked=True))

    _set_current(config, 3, videos["subject_zeta"])
    new_state, _ops = dispatch_command("landscape_cycle_action", state, config)

    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).locked is True, "cycling must not release the landscape lock"
    assert _cmds(config, 3) == [f"PLAY_FILE {videos['subject_alpha']}"]
    assert _cmds(config, 2) == []


def _sidecar_video(config: BridgeConfig, path: str) -> dict:
    return load_metadata(metadata_path_for(path, config.regen_metadata_root)).get("video", {})


def test_portrait_wrong_action_strikes_the_act_out_of_the_sidecar(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_zeta"])
    _new_state, ops = dispatch_command("portrait_wrong_action", state, config)

    video = _sidecar_video(config, paths["subject_zeta"])
    assert "action" not in video
    assert video["wrong_action"] == "Zeta Massage"
    assert [(op.key, op.source, op.level) for op in ops if op.op == "message"] == [
        ("Action removed: Zeta Massage", "portrait", MESSAGE)
    ]
    assert _cmds(config, 2) == [], "the clip keeps playing — only its metadata changed"


def test_wrong_action_reports_a_dead_end_when_the_video_has_no_act(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {"unlabeled": {"video": {"prompt": "p"}}})
    state = _make_state()

    _set_current(config, 2, paths["unlabeled"])
    _new_state, ops = dispatch_command("portrait_wrong_action", state, config)

    assert [(op.key, op.level) for op in ops if op.op == "message"] == [
        ("No action to remove", logging.WARNING)
    ]
    assert "wrong_action" not in _sidecar_video(config, paths["unlabeled"])


def test_wrong_action_judges_the_video_that_was_playing_when_it_was_spoken(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "newcomer": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["newcomer"])
    dispatch_command("portrait_wrong_action", state, config, target_path=paths["subject_zeta"])

    assert "action" not in _sidecar_video(config, paths["subject_zeta"])
    assert _sidecar_video(config, paths["newcomer"])["action"] == "Alpha"


def test_landscape_wrong_action_strikes_the_landscape_video(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "on_portrait": _cycle_meta("111", "Zeta Massage"),
        "on_landscape": _cycle_meta("222", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["on_portrait"])
    _set_current(config, 3, paths["on_landscape"])
    _new_state, ops = dispatch_command("landscape_wrong_action", state, config)

    assert "action" not in _sidecar_video(config, paths["on_landscape"])
    assert _sidecar_video(config, paths["on_portrait"])["action"] == "Zeta Massage"
    assert [op.source for op in ops if op.op == "message"] == ["landscape"]


def test_wrong_action_rebuilds_the_grouping_index_it_just_invalidated(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "subject_zeta": _cycle_meta("111", "Zeta Massage"),
        "subject_alpha": _cycle_meta("111", "Alpha"),
    })
    state = _make_state()

    _set_current(config, 2, paths["subject_zeta"])
    dispatch_command("portrait_cycle_action", state, config)
    dispatch_command("portrait_wrong_action", state, config)


    index = _satellite_group_index(2, config, paths["subject_zeta"])
    assert index.act_of(paths["subject_zeta"]) == ""


def test_recents_stays_newest_first_and_resets_the_lock(tmp_path: Path):
    config = _make_config(tmp_path)
    config.regen_media_root = tmp_path / "media"
    config.regen_metadata_root = tmp_path / "metadata"
    state = _make_state(portrait=SatelliteState(latest=True, locked=True))

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        new_state, _ops = dispatch_command("portrait_latest", state, config)

    assert new_state.satellite(FunTimeFunestra.PORTRAIT).latest is True
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    kwargs = mock_filter.call_args.kwargs
    assert kwargs["build"].recent is True
    assert kwargs["build"].favorites_filter is False
    assert kwargs["channel"].cmd_file == config.portrait.cmd_file
    assert kwargs["regen_metadata_root"] == config.regen_metadata_root


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


def test_the_motion_rate_reaches_the_robot_hand_in_either_mode(tmp_path: Path):
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


def test_the_primary_is_told_the_mute_as_well_as_the_level(tmp_path: Path):
    config = _make_config(tmp_path)

    dispatch_command("audio_mute", _make_state(volume=70), config)

    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 70 1"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "0", "the sink stays dumb"


def test_setting_the_volume_outright_lands_on_both_sinks(tmp_path: Path):
    config = _make_config(tmp_path)

    new_state, ops = dispatch_command(
        "audio_set_volume|35", _make_state(volume=70, muted=True), config)

    assert (new_state.volume, new_state.muted) == (35, False)
    assert config.main_funestra_cmd_file.read_text(encoding="utf-8").strip() == "SET_VOLUME 35 0"
    assert config.audio_volume_file.read_text(encoding="utf-8") == "35"
    assert ops == [WindowOp(op="message", key="Volume 35%", source="main")]


def test_the_level_reaches_both_funestras_that_draw_it(tmp_path: Path):
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


def test_mute_and_unmute_are_idempotent_not_a_toggle(tmp_path: Path):
    config = _make_config(tmp_path)

    still_muted, _ops = dispatch_command("audio_mute", _make_state(muted=True), config)
    assert still_muted.muted is True

    still_audible, _ops = dispatch_command("audio_unmute", _make_state(muted=False), config)
    assert still_audible.muted is False


def test_stepping_the_volume_lifts_a_mute(tmp_path: Path):
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

    def test_a_park_sends_the_device_home(self, tmp_path: Path):
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
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()

        assert not routes_to_origenerator("portrait_take_osr2", state, config)
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


def test_leaving_omnipause_brings_back_the_funestras_a_button_parked(tmp_path: Path):
    config = _make_config(tmp_path)

    _state, ops = dispatch_command("omnipause_toggle", _make_state(omni_paused=True), config)

    assert any(op.op == "restore_parked" for op in ops)


def test_the_restore_comes_before_the_bands_and_the_focus(tmp_path: Path):
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


@pytest.mark.parametrize("main_mode", list(MainMode))
def test_leaving_omnipause_brings_the_main_funestra_forward_in_either_mode(
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


def test_a_satellites_nudge_pair_seeks_that_satellite_and_leaves_its_lock_alone(
        tmp_path: Path):
    config = _make_config(tmp_path)
    for funestra in FunTimeFunestra.SATELLITES:
        _publish_status(config.satellite(funestra).status_file, duration_ms=60_000,
                        position_ms=30_000)
    state = _make_state(portrait=SatelliteState(locked=True))

    state, forward_ops = dispatch_command("portrait_nudge_next", state, config)
    state, back_ops = dispatch_command("portrait_nudge_prev", state, config)
    dispatch_command("landscape_nudge_next", state, config)

    assert forward_ops == back_ops == []
    assert _cmds(config, FunTimeFunestra.PORTRAIT) == ["SEEK_FWD", "SEEK_BACK"]
    assert _cmds(config, FunTimeFunestra.LANDSCAPE) == ["SEEK_FWD"]
    assert state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    assert not config.main_funestra_cmd_file.exists()


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


@pytest.mark.parametrize(("command", "position_ms", "verb"), [
    ("portrait_nudge_prev", 10_000, "SEEK_BACK"),
    ("portrait_nudge_next", 49_999, "SEEK_FWD"),
])
def test_a_ten_second_step_with_just_ten_seconds_to_go_still_steps(
        tmp_path: Path, command, position_ms, verb):
    config = _make_config(tmp_path)
    _publish_status(config.satellite(FunTimeFunestra.PORTRAIT).status_file,
                    duration_ms=60_000, position_ms=position_ms)

    _state, ops = dispatch_command(command, _make_state(), config)

    assert ops == []
    assert _cmds(config, FunTimeFunestra.PORTRAIT) == [verb]


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


def test_a_hosted_shows_words_in_kino_mode_say_kino_mode_ignored_them(tmp_path: Path, caplog):
    config = _make_config(tmp_path)

    with caplog.at_level(logging.WARNING, logger="fun_time.command_dispatch"):
        _new_state, ops = dispatch_command("portrait_say_experiments", _make_state(), config)

    assert ops == [WindowOp(op=Op.IGNORED, key="in Kino mode")]
    assert not any("portrait_say_experiments" in record.message for record in caplog.records)


def test_genaumacher_save_raises_a_save_video_op_and_runs_nothing_inline(tmp_path: Path):
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


def _loop_config(tmp_path: Path, *, axis: str, funestra: int = 2) -> tuple[BridgeConfig, str, str]:
    if axis == "action":
        videos = {"a": _subject_meta(action="Alpha"), "b": _subject_meta(action="Twirling")}
    else:
        videos = {"a": _subject_meta(image_seed="1"), "b": _subject_meta(image_seed="2")}
    config, paths = _make_grouped_config(tmp_path, videos, funestra=funestra)
    return config, paths["a"], paths["b"]


def _widened_loop_config(tmp_path: Path) -> tuple[BridgeConfig, str, str, str]:
    config, paths = _make_grouped_config(tmp_path, {
        "a": _subject_meta(image_seed="1"),
        "a2": _subject_meta(image_seed="2"),
        "b": _subject_meta(image_seed="3", quality="Draft"),
    })
    return config, paths["a"], paths["a2"], paths["b"]


def test_action_loop_reshapes_the_queue_to_the_subjects_action_group(tmp_path: Path):
    config, a, b = _loop_config(tmp_path, axis="action")

    _set_current(config, 2, a)
    _state, ops = dispatch_command("portrait_action_loop", _make_state(), config)

    assert sorted(_playlist(config, 2)) == sorted([a, b])
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)
    assert any(op.op == "message" and op.source == "portrait" for op in ops)


def test_seed_loop_reshapes_the_queue_to_the_current_acts_seed_family(tmp_path: Path):
    config, a, b = _loop_config(tmp_path, axis="seed", funestra=3)

    _set_current(config, 3, a)
    dispatch_command("landscape_seed_loop", _make_state(), config)

    assert sorted(_playlist(config, 3)) == sorted([a, b])
    assert "RELOAD_PLAYLIST" in _cmds(config, 3)


def test_a_widened_seed_loop_loops_the_loose_family_and_keeps_the_widen_anchor(tmp_path: Path):
    config, a, a2, b = _widened_loop_config(tmp_path)
    state = _make_state(portrait=SatelliteState(widen_video=a))

    _set_current(config, 2, a)
    new_state, _ops = dispatch_command("portrait_seed_loop", state, config)

    assert sorted(_playlist(config, 2)) == sorted([a, a2, b])
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).loop == "seed"
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).widen_video == a


def test_a_non_widened_seed_loop_clears_a_stale_widen_anchor(tmp_path: Path):
    config, a, b = _loop_config(tmp_path, axis="seed")
    state = _make_state(portrait=SatelliteState(widen_video="C:/somewhere/else.mp4"))

    _set_current(config, 2, a)
    new_state, _ops = dispatch_command("portrait_seed_loop", state, config)

    assert sorted(_playlist(config, 2)) == sorted([a, b])
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).loop == "seed"
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).widen_video == ""


def test_a_video_marked_trash_stops_being_drawn_on_the_map(tmp_path: Path):
    config, a, b = _loop_config(tmp_path, axis="seed")
    _set_current(config, 2, a)
    assert nav_map_cells(_satellite_group_index(FunTimeFunestra.PORTRAIT, config, b), b)[0] == [a]

    _discard(2, _make_state(), config)

    index = _satellite_group_index(FunTimeFunestra.PORTRAIT, config, b)
    assert nav_map_cells(index, b)[0] == []


def test_marking_videos_trash_until_one_is_left_turns_the_loop_into_a_lock(tmp_path: Path):
    config, a, _b = _loop_config(tmp_path, axis="seed")
    _set_current(config, 2, a)
    state, _ops = dispatch_command("portrait_seed_loop", _make_state(), config)

    state, ops = _discard(2, state, config)

    assert _cmds(config, 2) == ["RELOAD_PLAYLIST", "TRASH", "LOCK_ON"]
    assert state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == ""
    assert [(op.op, op.key, op.source, op.level) for op in ops
            if op.key == "Locked"] == [("message", "Locked", "portrait", MESSAGE)]


def test_loop_with_one_video_becomes_a_single_video_lock(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {"only": _subject_meta()})
    only = Path(paths["only"])

    _set_current(config, 2, str(only))
    new_state, ops = dispatch_command("portrait_action_loop", _make_state(), config)

    assert _playlist(config, 2) == []
    assert _cmds(config, 2) == ["LOCK_ON"]
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    messages = [op for op in ops if op.op == "message"]
    assert [op.key for op in messages] == ["Locked"]
    assert [op.level for op in messages] == [FAVORITE]


def test_a_loop_of_one_video_puts_it_in_the_favorites_as_every_lock_does(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {"only": _subject_meta()})
    _set_current(config, 2, paths["only"])

    dispatch_command("portrait_action_loop", _make_state(), config)

    assert is_favorite(paths["only"], config.regen_metadata_root)


def test_action_loop_records_the_loop_axis_in_state(tmp_path: Path):
    config, a, _b = _loop_config(tmp_path, axis="action")

    _set_current(config, 2, a)
    state, _ops = dispatch_command("portrait_action_loop", _make_state(), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == "action"
    assert state.satellite(FunTimeFunestra.LANDSCAPE).loop == ""


def test_seed_loop_records_the_loop_axis_in_state(tmp_path: Path):
    config, a, _b = _loop_config(tmp_path, axis="seed", funestra=3)

    _set_current(config, 3, a)
    state, _ops = dispatch_command("landscape_seed_loop", _make_state(), config)

    assert state.satellite(FunTimeFunestra.LANDSCAPE).loop == "seed"
    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == ""


def test_a_loop_anchors_on_the_video_it_started_on(tmp_path: Path):
    config, a, b = _loop_config(tmp_path, axis="seed")

    _set_current(config, 2, b)
    state, _ops = dispatch_command("portrait_seed_loop", _make_state(), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).nav_map_anchor == b
    assert _playlist(config, 2) == [b, a]


def test_single_video_lock_clears_a_prior_loop(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {"only": _subject_meta()})
    only = Path(paths["only"])

    _set_current(config, 2, str(only))
    state, _ops = dispatch_command(
        "portrait_action_loop",
        _make_state(portrait=SatelliteState(loop="seed", widen_video=str(only))), config,
    )

    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == ""
    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == ""


def test_locking_holds_your_place_in_a_loop_rather_than_ending_it(tmp_path: Path):
    config = _make_config(tmp_path)
    video = r"C:\videos\provider2\abc_123.mp4"

    _set_current(config, 2, video)
    with patch("fun_time.lock.mark_favorite"):
        state, _ops = dispatch_command(
            "portrait_lock",
            _make_state(portrait=SatelliteState(locked=False, loop="action", nav_map_anchor=video, widen_video=video)),
            config,
        )

    assert state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == "action"
    assert state.satellite(FunTimeFunestra.PORTRAIT).nav_map_anchor == video
    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == video


def test_unlocking_leaves_the_loop_running(tmp_path: Path):
    config = _make_config(tmp_path)
    video = r"C:\videos\provider2\abc_123.mp4"

    _set_current(config, 2, video)
    state, _ops = dispatch_command(
        "portrait_lock",
        _make_state(portrait=SatelliteState(locked=True, loop="seed", nav_map_anchor=video)),
        config,
    )

    assert state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == "seed"
    assert "LOCK_OFF" in _cmds(config, 2)


def test_an_applied_filter_clears_a_running_loop(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        state, _ops = dispatch_command(
            "filter_portrait_alpha",
            _make_state(portrait=SatelliteState(loop="seed", widen_video="C:/v/anchor.mp4")), config,
        )

    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == ""
    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == ""


def test_a_zero_match_filter_leaves_a_running_loop_alone(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=False)
        state, _ops = dispatch_command(
            "filter_portrait_alpha",
            _make_state(portrait=SatelliteState(loop="seed", widen_video="C:/v/anchor.mp4")), config,
        )

    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == "seed"
    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == "C:/v/anchor.mp4"


def test_no_loop_clears_the_loop_but_leaves_the_map_where_it_hangs(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=["C:/v/x.mp4"]):
        state, _ops = dispatch_command(
            "portrait_no_loop",
            _make_state(portrait=SatelliteState(loop="action", nav_map_anchor="C:/v/anchor.mp4", widen_video="C:/v/anchor.mp4")), config,
        )

    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == ""
    assert state.satellite(FunTimeFunestra.PORTRAIT).nav_map_anchor == "C:/v/anchor.mp4"
    assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == "C:/v/anchor.mp4"


def test_no_loop_reshapes_the_queue_to_the_browse_in_place(tmp_path: Path):
    config = _make_config(tmp_path)
    browse = [r"C:\v\one.mp4", r"C:\v\two.mp4"]

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_no_loop", _make_state(), config)

    assert _playlist(config, 2) == browse
    assert "RELOAD_PLAYLIST" in _cmds(config, 2)


def test_no_loop_keeps_the_video_on_screen_by_heading_the_restored_browse(tmp_path: Path):
    config = _make_config(tmp_path)
    playing = r"C:\v\seed_4.mp4"
    browse = [r"C:\v\one.mp4", r"C:\v\two.mp4"]
    _set_current(config, 2, playing)

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_no_loop", _make_state(portrait=SatelliteState(loop="seed")), config)

    assert _playlist(config, 2) == [playing, *browse]


def test_no_loop_leaves_a_browse_that_already_holds_the_video_untouched(tmp_path: Path):
    config = _make_config(tmp_path)
    browse = [r"C:\v\one.mp4", r"C:\v\two.mp4", r"C:\v\three.mp4"]
    _set_current(config, 2, r"C:\v\two.mp4")

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_no_loop", _make_state(portrait=SatelliteState(loop="seed")), config)

    assert _playlist(config, 2) == browse


def test_no_loop_leaves_the_queue_alone_when_the_browse_is_empty(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=[]):
        state, ops = dispatch_command("portrait_no_loop", _make_state(portrait=SatelliteState(loop="seed")), config)

    assert _playlist(config, 2) == []
    assert "RELOAD_PLAYLIST" not in _cmds(config, 2)
    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == ""
    assert [op.key for op in ops if op.op == "message"] == ["Loop off"]


@pytest.mark.parametrize("axis", ["seed", "action"])
def test_next_leaves_a_loop_down_to_one_video_for_the_browse(tmp_path: Path, axis: str):
    config = _make_config(tmp_path)
    playing = r"C:\v\seed_4.mp4"
    browse = [r"C:\v\one.mp4", r"C:\v\two.mp4"]
    _set_current(config, 2, playing, playlist_length=1)

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=browse):
        state, ops = dispatch_command("portrait_next", _make_state(portrait=SatelliteState(loop=axis)), config)

    assert _playlist(config, 2) == [playing, *browse]
    assert _cmds(config, 2) == ["RELOAD_PLAYLIST", "NEXT"]
    assert state.portrait.loop == ""
    assert ops == []


def test_next_in_a_loop_of_several_videos_moves_on_within_the_loop(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_current(config, 2, "C:/v/seed_4.mp4", playlist_length=2)

    with patch("fun_time.satellite_groups.satellite_browse_paths") as browse:
        state, _ops = dispatch_command("portrait_next", _make_state(portrait=SatelliteState(loop="seed")), config)

    browse.assert_not_called()
    assert _cmds(config, 2) == ["NEXT"]
    assert state.portrait.loop == "seed"


def _cycle_config(tmp_path: Path, *, seed_family: bool = True,
                  action_group: bool = True, funestra: int = 2) -> tuple[BridgeConfig, str, str, str]:
    videos = {
        "a": _subject_meta(image_seed="1", action="Alpha"),
        "b": (_subject_meta(image_seed="1", action="Beta") if action_group
              else _subject_meta(prompt="another scene", image_seed="7", action="Beta")),
        "c": _subject_meta(image_seed="2", action="Alpha" if seed_family else "Gamma"),
    }
    config, paths = _make_grouped_config(tmp_path, videos, funestra=funestra)
    return config, paths["a"], paths["b"], paths["c"]


def test_the_loop_key_locks_the_video_on_screen_when_nothing_is_looping(tmp_path: Path):
    config, a, _b, _c = _cycle_config(tmp_path)

    _set_current(config, 2, a)
    state, ops = dispatch_command("portrait_loop", _make_state(), config)

    assert _cmds(config, 2) == ["LOCK_ON"]
    assert (state.portrait.locked, state.portrait.loop) == (True, "")
    assert is_favorite(a, config.regen_metadata_root)
    assert [(op.key, op.level) for op in ops if op.op == "message"] == [("Locked", FAVORITE)]


def test_the_loop_key_steps_a_seed_loop_on_to_the_action_loop(tmp_path: Path):
    config, a, b, _c = _cycle_config(tmp_path)

    _set_current(config, 2, a)
    state, _ops = dispatch_command("portrait_loop", _make_state(portrait=SatelliteState(loop="seed")), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == "action"
    assert _playlist(config, 2) == [a, b]


def test_the_loop_key_steps_an_action_loop_off(tmp_path: Path):
    config, a, _b, _c = _cycle_config(tmp_path)
    browse = [r"C:\v\one.mp4", r"C:\v\two.mp4"]

    _set_current(config, 2, a)
    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=browse):
        state, ops = dispatch_command("portrait_loop", _make_state(portrait=SatelliteState(loop="action")), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == ""
    assert _playlist(config, 2) == [a, *browse]
    assert [op.key for op in ops if op.op == "message"] == ["Loop off"]


def test_four_presses_of_the_loop_key_come_back_round_to_off(tmp_path: Path):
    config, a, _b, _c = _cycle_config(tmp_path)
    _set_current(config, 2, a)

    state = _make_state()
    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=[a]):
        stops = []
        for _press in range(4):
            state, _ops = dispatch_command("portrait_loop", state, config)
            stops.append((state.portrait.locked, state.portrait.loop))

    assert stops == [(True, ""), (False, "seed"), (False, "action"), (False, "")]


def test_the_loop_key_steps_from_the_lock_over_a_seed_row_of_one_to_the_actions(tmp_path: Path):
    config, a, b, _c = _cycle_config(tmp_path, seed_family=False)

    _set_current(config, 2, a)
    state, _ops = dispatch_command("portrait_loop", _make_state(portrait=SatelliteState(locked=True)),
                                   config)

    assert (state.portrait.locked, state.portrait.loop) == (False, "action")
    assert _playlist(config, 2) == [a, b]
    assert _cmds(config, 2) == ["LOCK_OFF", "RELOAD_PLAYLIST"]


def test_with_nothing_to_loop_the_loop_key_locks_then_lets_go_without_moving_on(tmp_path: Path):
    config, a, _b, _c = _cycle_config(tmp_path, seed_family=False, action_group=False)

    _set_current(config, 2, a)
    with patch("fun_time.satellite_groups.satellite_browse_paths") as browse:
        state, _ops = dispatch_command("portrait_loop", _make_state(), config)
        state, ops = dispatch_command("portrait_loop", state, config)

    browse.assert_not_called()
    assert _cmds(config, 2) == ["LOCK_ON", "LOCK_OFF"]
    assert state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert [op.key for op in ops if op.op == "message"] == ["Unlocked"]


def test_the_loop_keys_off_stop_lets_go_of_a_lock_taken_inside_the_loop(tmp_path: Path):
    config, a, _b, _c = _cycle_config(tmp_path)
    browse = [r"C:\v\one.mp4", r"C:\v\two.mp4"]

    _set_current(config, 2, a)
    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=browse):
        state, ops = dispatch_command(
            "portrait_loop", _make_state(portrait=SatelliteState(loop="action", locked=True)), config)

    assert (state.portrait.locked, state.portrait.loop) == (False, "")
    assert _cmds(config, 2) == ["LOCK_OFF", "RELOAD_PLAYLIST"]
    assert [op.key for op in ops if op.op == "message"] == ["Loop off"]


def test_the_loop_key_ends_a_loop_the_video_has_drifted_out_of(tmp_path: Path):
    config, a, _b, _c = _cycle_config(tmp_path, seed_family=False, action_group=False)

    _set_current(config, 2, a)
    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=[a]):
        state, ops = dispatch_command("portrait_loop", _make_state(portrait=SatelliteState(loop="seed")), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == ""
    assert [op.key for op in ops if op.op == "message"] == ["Loop off"]


def test_the_loop_key_drives_only_its_own_satellite(tmp_path: Path):
    config, a, _b, _c = _cycle_config(tmp_path, funestra=3)

    _set_current(config, 3, a)
    state, _ops = dispatch_command("landscape_loop", _make_state(), config)

    assert (state.landscape.locked, state.portrait.locked) == (True, False)
    assert (_cmds(config, 3), _cmds(config, 2)) == (["LOCK_ON"], [])


def test_the_loop_key_does_nothing_with_no_video_on_screen(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_groups.satellite_browse_paths") as browse:
        state, ops = dispatch_command("portrait_loop", _make_state(portrait=SatelliteState(loop="seed")), config)

    browse.assert_not_called()
    assert state.satellite(FunTimeFunestra.PORTRAIT).loop == "seed"
    assert ops == []


def test_a_reorder_clears_only_its_own_satellites_loop(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result(applied=True)
        state, _ops = dispatch_command(
            "portrait_latest",
            _make_state(landscape=SatelliteState(loop="action", widen_video="C:/v/l.mp4"), portrait=SatelliteState(loop="seed", widen_video="C:/v/p.mp4")),
            config,
        )

    assert (state.satellite(FunTimeFunestra.PORTRAIT).loop, state.satellite(FunTimeFunestra.PORTRAIT).widen_video) == ("", "")
    assert (state.satellite(FunTimeFunestra.LANDSCAPE).loop, state.satellite(FunTimeFunestra.LANDSCAPE).widen_video) == ("action", "C:/v/l.mp4")


def test_fmode_clears_the_loop_of_every_satellite_it_rebuilds(tmp_path: Path):
    config = _make_config(tmp_path)

    state, _ops, _mock = _dispatch_fmode(
        "fmode_toggle",
        _make_state(landscape=SatelliteState(loop="action", widen_video="C:/v/l.mp4"), portrait=SatelliteState(loop="seed", widen_video="C:/v/p.mp4")),
        config,
    )

    assert (state.satellite(FunTimeFunestra.PORTRAIT).loop, state.satellite(FunTimeFunestra.LANDSCAPE).loop) == ("", "")
    assert (state.satellite(FunTimeFunestra.PORTRAIT).widen_video, state.satellite(FunTimeFunestra.LANDSCAPE).widen_video) == ("", "")


def test_a_sided_fmode_leaves_the_other_satellites_loop_running(tmp_path: Path):
    config = _make_config(tmp_path)

    state, _ops, _mock = _dispatch_fmode(
        "portrait_fmode",
        _make_state(landscape=SatelliteState(loop="action", widen_video="C:/v/l.mp4"), portrait=SatelliteState(loop="seed", widen_video="C:/v/p.mp4")),
        config,
    )

    assert (state.satellite(FunTimeFunestra.PORTRAIT).loop, state.satellite(FunTimeFunestra.PORTRAIT).widen_video) == ("", "")
    assert (state.satellite(FunTimeFunestra.LANDSCAPE).loop, state.satellite(FunTimeFunestra.LANDSCAPE).widen_video) == ("action", "C:/v/l.mp4")


def test_lock_action_filters_to_the_current_videos_action(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "clip": _subject_meta(action="Beta Gamma"),
    })

    _set_current(config, 2, paths["clip"])
    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        new_state, _ops = dispatch_command("portrait_lock_action", _make_state(), config)

    assert mock_filter.call_args.kwargs["build"].filter_query == "beta gamma"
    assert new_state.satellite(FunTimeFunestra.PORTRAIT).filter == "beta gamma"
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).filter == ""


def test_action_loop_groups_the_video_that_was_playing_when_spoken(tmp_path: Path):
    config, meant, sibling = _loop_config(tmp_path, axis="action")

    _set_current(config, 2, "C:/v/advanced_to.mp4")
    dispatch_command("portrait_action_loop", _make_state(), config, target_path=meant)

    assert sorted(_playlist(config, 2)) == sorted([meant, sibling])


def test_lock_action_filters_to_the_action_of_the_video_playing_when_spoken(tmp_path: Path):
    config, paths = _make_grouped_config(tmp_path, {
        "meant": _subject_meta(action="Beta Gamma"),
        "advanced_to": _subject_meta(image_seed="2", action="Other"),
    })

    _set_current(config, 2, paths["advanced_to"])
    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        mock_filter.return_value = _filter_result()
        dispatch_command("portrait_lock_action", _make_state(), config,
                         target_path=paths["meant"])

    assert mock_filter.call_args.kwargs["build"].filter_query == "beta gamma"


def test_lock_action_without_metadata_says_so(tmp_path: Path):
    config = _make_config(tmp_path)

    _set_current(config, 3, "C:/v/clip.mp4")
    with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
        new_state, ops = dispatch_command("landscape_lock_action", _make_state(), config)

    mock_filter.assert_not_called()
    assert new_state.satellite(FunTimeFunestra.LANDSCAPE).filter == ""
    assert any(
        op.op == "message" and "No action metadata" in op.key and op.source == "landscape"
        for op in ops
    )


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


def test_a_main_reorder_in_genau_mode_is_remembered_under_genaus_own_flag(tmp_path):
    config, _builds = _recording(_make_config(tmp_path))

    state, _ops = dispatch_command("main_latest", _make_state(main_mode=MainMode.GENAU), config)

    assert state.genau_latest is True
    assert state.main_latest is False

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


def test_asking_for_the_shapes_already_running_rebuilds_nothing(tmp_path):
    config, builds = _recording(_vr_config(tmp_path))

    _state, ops = dispatch_command("main_projection_both", _make_state(), config)

    assert builds.asked == []
    assert ops == []


@pytest.mark.parametrize(("command", "state"), [
    ("main_projection_none", BridgeState()),
    ("main_funestra_length_none", BridgeState()),
    ("main_funestra_length_clips", BridgeState(main_plays_flat=False)),
])
def test_every_press_that_leaves_no_video_ends_the_same_way(tmp_path, caplog, command, state):
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
    config.origenerator_enabled = True
    config.origenerator_cmd_file = config.state_dir / "origenerator_cmd.txt"
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


def test_reordering_the_main_funestra_starts_it_at_the_top(tmp_path):
    config, builds = _recording(_make_config(tmp_path))

    dispatch_command("main_latest", BridgeState(), config)

    assert builds.asked[-1].start_at_top is True


def _origenerator_config(tmp_path: Path) -> BridgeConfig:
    config = _make_config(tmp_path)
    return replace(
        config,
        origenerator_enabled=True,
        origenerator_cmd_file=config.state_dir / "origenerator_cmd.txt",
        origenerator_paused_file=config.state_dir / "origenerator_paused.txt",
    )


def _up(**fields) -> BridgeState:
    return BridgeState(origenerator_ready=True, **fields)


def _in_origenerator_mode(*funestras: FunTimeFunestra, **fields) -> BridgeState:
    state = _up(**fields)
    for funestra in funestras or FunTimeFunestra.SATELLITES:
        state = state.with_satellite(funestra, mode=SatellitesMode.ORIGENERATOR,
                                     handed_over=True)
    return state


def test_the_satellites_the_hosted_app_holds_are_named_by_their_satellites(tmp_path):
    config = _origenerator_config(tmp_path)

    assert hosted_satellites(_in_origenerator_mode(FunTimeFunestra.PORTRAIT), config) == (
        frozenset({"portrait"}))
    assert hosted_satellites(_in_origenerator_mode(FunTimeFunestra.PORTRAIT),
                             _make_config(tmp_path)) == frozenset()


def test_a_spoken_trash_reaches_a_hosted_satellite_with_when_it_was_said(tmp_path):
    config = replace(_origenerator_config(tmp_path),
                     origenerator_said_at_suffix=" @{said_at_epoch_seconds}")
    said_two_seconds_ago = time.monotonic() - 2.0

    dispatch_command("portrait_trash", _in_origenerator_mode(FunTimeFunestra.PORTRAIT), config,
                     spoken_at=said_two_seconds_ago)

    [line] = _origenerator_cmds(config)
    command, _, stamp = line.partition(" @")
    assert command == "portrait_trash"
    assert float(stamp) == pytest.approx(time.time() - 2.0, abs=0.5)


def test_a_spoken_lock_reaches_a_hosted_satellite_with_no_time_to_go_back_to_again(tmp_path):
    config = replace(_origenerator_config(tmp_path),
                     origenerator_said_at_suffix=" @{said_at_epoch_seconds}")
    _set_current(config, FunTimeFunestra.PORTRAIT, "C:\\pictures\\moved on to.png")

    dispatch_command("portrait_lock_on", _in_origenerator_mode(FunTimeFunestra.PORTRAIT), config,
                     target_path="C:\\pictures\\meant.png", spoken_at=time.monotonic() - 2.0)

    assert _origenerator_cmds(config) == ["portrait_lock_on"]


def _modes(state: BridgeState) -> list[SatellitesMode]:
    return [state.satellite(funestra).mode for funestra in FunTimeFunestra.SATELLITES]


def _origenerator_cmds(config: BridgeConfig) -> list[str]:
    cmd_file = config.origenerator_cmd_file
    if not cmd_file.exists():
        return []
    return [line.strip() for line in cmd_file.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestEachSatellitesOwnMode:
    def test_a_satellites_origenerator_button_hands_that_funestra_alone_to_the_app(
            self, tmp_path):
        config = _origenerator_config(tmp_path)
        for funestra in FunTimeFunestra.SATELLITES:
            write_playlist_file(config.satellite(funestra).playlist_file,
                                [str(tmp_path / f"{funestra.label}.mp4")])
        state, ops = dispatch_command("portrait_origenerator_activate", _up(), config)
        assert _modes(state) == [SatellitesMode.ORIGENERATOR, SatellitesMode.KINO]
        assert state.rfb_mode is RfbMode.RFB
        assert [(op.op, op.key) for op in ops if op.op != "message"] == [
            ("hand_over_funestras", "portrait")]
        assert _origenerator_cmds(config) == ["portrait_open_slideshow"]
        portrait = config.satellite(FunTimeFunestra.PORTRAIT)
        assert not portrait.paused_file.exists()
        assert _cmds(config, FunTimeFunestra.PORTRAIT) == ["LOCK_OFF"]
        assert portrait.playlist_file.with_name(f"{portrait.playlist_file.stem}.kept.tsv").exists()
        assert _cmds(config, FunTimeFunestra.LANDSCAPE) == []

    def test_a_satellites_kino_button_asks_the_app_to_let_go_of_that_funestra_alone(
            self, tmp_path):
        config = _origenerator_config(tmp_path)
        state, ops = dispatch_command("landscape_kino_activate", _in_origenerator_mode(), config)
        assert _modes(state) == [SatellitesMode.ORIGENERATOR, SatellitesMode.KINO]
        assert _origenerator_cmds(config) == ["landscape_close_slideshow"]
        assert not config.landscape.paused_file.exists()
        assert [(op.op, op.key) for op in ops] == [("take_back_funestras", "landscape")]

    def test_a_button_for_the_mode_a_satellite_is_already_in_changes_nothing(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state, ops = dispatch_command("portrait_kino_activate", _up(), config)
        assert _modes(state) == [SatellitesMode.KINO, SatellitesMode.KINO]
        assert (ops, _origenerator_cmds(config)) == ([], [])

    def test_satellites_kino_mode_brings_both_satellites_home(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state, ops = dispatch_command(
            "satellites_kino_activate",
            _in_origenerator_mode(rfb_mode=RfbMode.ORIGENERATOR), config)
        assert _modes(state) == [SatellitesMode.KINO, SatellitesMode.KINO]
        assert state.rfb_mode is RfbMode.ORIGENERATOR
        assert _origenerator_cmds(config) == ["portrait_close_slideshow", "landscape_close_slideshow"]
        assert [(op.op, op.key) for op in ops] == [
            ("take_back_funestras", "portrait"), ("take_back_funestras", "landscape")]

    def test_origenerator_mode_said_of_both_puts_both_satellites_in_it_and_leaves_the_browser(
            self, tmp_path):
        config = _origenerator_config(tmp_path)
        state, _ops = dispatch_command("satellites_origenerator_activate", _up(), config)
        assert _modes(state) == [SatellitesMode.ORIGENERATOR, SatellitesMode.ORIGENERATOR]
        assert state.rfb_mode is RfbMode.RFB
        assert _origenerator_cmds(config) == ["portrait_open_slideshow", "landscape_open_slideshow"]

    def test_without_an_origenerator_the_switch_reports_and_stays(self, tmp_path):
        config = _make_config(tmp_path)
        state, ops = dispatch_command("portrait_origenerator_activate", _up(), config)
        assert _modes(state) == [SatellitesMode.KINO, SatellitesMode.KINO]
        assert any(op.op == "message" and op.level == logging.WARNING for op in ops)

    def test_a_hosted_app_still_booting_refuses_the_switch_and_says_so(self, tmp_path):
        config = _origenerator_config(tmp_path)

        for command in ("portrait_origenerator_activate", "satellites_origenerator_activate"):
            state, ops = dispatch_command(command, BridgeState(), config)
            assert _modes(state) == [SatellitesMode.KINO, SatellitesMode.KINO]
            assert [(op.op, op.key) for op in ops] == [
                ("message", "Origenerator is still starting")]
            assert _origenerator_cmds(config) == []

    def test_a_switch_clicked_while_paused_still_hands_the_funestra_to_the_hosted_app(
            self, tmp_path):
        config = _origenerator_config(tmp_path)
        write_playlist_file(config.satellite(FunTimeFunestra.LANDSCAPE).playlist_file,
                            [str(tmp_path / "landscape.mp4")])
        state, _ops = dispatch_command("landscape_origenerator_activate",
                                       _up(omni_paused=True), config)
        assert _modes(state) == [SatellitesMode.KINO, SatellitesMode.ORIGENERATOR]
        assert _origenerator_cmds(config) == ["landscape_open_slideshow"]
        assert _cmds(config, FunTimeFunestra.LANDSCAPE) == ["LOCK_OFF"]

    def test_a_press_on_the_satellite_still_in_kino_mode_stays_with_its_funestra(self, tmp_path):
        config = _origenerator_config(tmp_path)
        _state, _ = dispatch_command("landscape_next",
                                    _in_origenerator_mode(FunTimeFunestra.PORTRAIT), config)
        assert _origenerator_cmds(config) == []
        assert _cmds(config, FunTimeFunestra.LANDSCAPE) == ["NEXT"]

    def test_an_act_said_to_both_satellites_in_different_modes_reaches_each_where_its_mode_says(
            self, tmp_path):
        config = _origenerator_config(tmp_path)

        with patch("fun_time.satellite_commands.apply_satellite_filter") as rebuild:
            rebuild.return_value = _filter_result(count=10)
            state, _ = dispatch_command("filter_satellites_scene_one",
                                        _in_origenerator_mode(FunTimeFunestra.PORTRAIT), config)

        assert _origenerator_cmds(config) == ["filter_portrait_scene_one"]
        assert [call.kwargs["funestra"] for call in rebuild.call_args_list] == [
            FunTimeFunestra.LANDSCAPE]
        assert state.satellite(FunTimeFunestra.LANDSCAPE).filter == "scene one"


class TestTheRandomFavsBrowsersPlace:
    def test_origenerator_takes_the_browsers_place_and_leaves_the_funestras_alone(
            self, tmp_path):
        config = _origenerator_config(tmp_path)
        state, ops = dispatch_command("origenerator_activate", _up(), config)
        assert state.rfb_mode is RfbMode.ORIGENERATOR
        assert _modes(state) == [SatellitesMode.KINO, SatellitesMode.KINO]
        assert [(op.op, op.key) for op in ops] == [
            ("show_window", "origenerator"),
            ("activate_window", "origenerator"),
            ("restack_rfb", ""),
        ]
        assert _origenerator_cmds(config) == []
        assert _cmds(config, FunTimeFunestra.PORTRAIT) == []

    def test_the_browser_takes_its_place_back(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state, ops = dispatch_command(
            "rfb_activate", _in_origenerator_mode(rfb_mode=RfbMode.ORIGENERATOR), config)
        assert state.rfb_mode is RfbMode.RFB
        assert _modes(state) == [SatellitesMode.ORIGENERATOR, SatellitesMode.ORIGENERATOR]
        assert [(op.op, op.key) for op in ops] == [
            ("hide_window", "origenerator"), ("restack_rfb", "")]
        assert _origenerator_cmds(config) == []

    def test_asking_for_what_already_holds_the_place_moves_no_window(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state, ops = dispatch_command("rfb_activate", _up(), config)
        assert (state.rfb_mode, ops) == (RfbMode.RFB, [])

    def test_a_hosted_app_still_booting_keeps_the_browser_in_its_place(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state, ops = dispatch_command("origenerator_activate", BridgeState(), config)
        assert state.rfb_mode is RfbMode.RFB
        assert [(op.op, op.key) for op in ops] == [
            ("message", "Origenerator is still starting")]

    def test_without_an_origenerator_the_browser_keeps_its_place(self, tmp_path):
        config = _make_config(tmp_path)
        state, ops = dispatch_command("origenerator_activate", _up(), config)
        assert state.rfb_mode is RfbMode.RFB
        assert [(op.op, op.key) for op in ops] == [("message", "No Origenerator configured")]


class TestOrigeneratorTransport:
    def test_satellite_transport_reaches_the_hosted_app_not_the_funestra(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()
        state, _ = dispatch_command("portrait_next", state, config)
        assert _origenerator_cmds(config) == ["portrait_next"]
        assert _cmds(config, 2) == []

    def test_every_press_on_a_hosted_satellite_routes_as_it_was_posted(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()
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

    def test_a_satellite_filter_goes_to_the_hosted_app_rather_than_into_a_rebuild(self, tmp_path, rebuilt_satellite):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()

        state, _ = dispatch_command("filter_portrait_scene_one", state, config)

        rebuilt_satellite.assert_not_called()
        assert _origenerator_cmds(config) == ["filter_portrait_scene_one"]

    def test_an_act_said_to_no_satellite_narrows_both_hosted_shows(self, tmp_path, rebuilt_satellite):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()

        state, _ = dispatch_command("filter_satellites_scene_one", state, config)

        rebuilt_satellite.assert_not_called()
        assert _origenerator_cmds(config) == [
            "filter_portrait_scene_one", "filter_landscape_scene_one"]

    def test_the_funestras_own_controls_stay_the_sessions(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()

        state, _ops = dispatch_command("portrait_speed_up", state, config)
        state, minimize_ops = dispatch_command("portrait_minimize", state, config)

        assert _origenerator_cmds(config) == []
        assert _cmds(config, 2) == ["SPEED_UP"]
        assert minimize_ops

    def test_the_apps_f_mode_turns_each_hosted_shows_own_f_mode_on(self, tmp_path):
        config = _origenerator_config(tmp_path)
        for funestra in FunTimeFunestra.SATELLITES:
            publish_the_show(config, funestra, f_mode=False)
        state = _in_origenerator_mode()

        with patch("fun_time.f_mode.apply_fmode") as fmode:
            fmode.return_value = SimpleNamespace(funestras=(FunTimeFunestra.MAIN,), log_message="")
            state, _ = dispatch_command("fmode_toggle", state, config)

        assert fmode.call_args.kwargs["funestras"] == (FunTimeFunestra.MAIN,)
        assert _origenerator_cmds(config) == ["portrait_fmode_on", "landscape_fmode_on"]
        assert not state.satellite(FunTimeFunestra.PORTRAIT).favorites_filter

    def test_the_apps_f_mode_lifts_the_hosted_shows_once_every_funestra_is_in_it(self, tmp_path):
        config = _origenerator_config(tmp_path)
        for funestra in FunTimeFunestra.SATELLITES:
            publish_the_show(config, funestra, f_mode=True)
        state = _in_origenerator_mode(main_scripted_filter=True)

        state, _ops, mock_fmode = _dispatch_fmode("fmode_toggle", state, config)

        assert mock_fmode.call_args.kwargs["funestras"] == (FunTimeFunestra.MAIN,)
        assert state.main_scripted_filter is False
        assert _origenerator_cmds(config) == ["portrait_fmode_off", "landscape_fmode_off"]

    def test_a_hosted_show_already_in_the_asked_for_f_mode_is_sent_nothing(self, tmp_path):
        config = _origenerator_config(tmp_path)
        publish_the_show(config, FunTimeFunestra.PORTRAIT, f_mode=True)
        publish_the_show(config, FunTimeFunestra.LANDSCAPE, f_mode=False)

        _dispatch_fmode("fmode_on", _in_origenerator_mode(), config)

        assert _origenerator_cmds(config) == ["landscape_fmode_on"]

    def test_a_satellite_with_no_show_up_has_no_say_in_the_apps_f_mode(self, tmp_path):
        config = _origenerator_config(tmp_path)
        publish_the_show(config, FunTimeFunestra.PORTRAIT, f_mode=True)
        config.satellite(FunTimeFunestra.LANDSCAPE).origenerator_hud_file.write_text("", encoding="utf-8")
        state = _in_origenerator_mode(main_scripted_filter=True)

        state, _ops, _mock = _dispatch_fmode("fmode_toggle", state, config)

        assert state.main_scripted_filter is False
        assert _origenerator_cmds(config) == ["portrait_fmode_off"]

    def test_a_spoken_phrase_reaches_the_hosted_app_as_words(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()
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
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode(portrait=SatelliteState(filter="alpha"))

        state, _ = dispatch_command("portrait_no_filter", state, config)

        assert _origenerator_cmds(config) == ["PORTRAIT_SAY:clear filter"]
        assert state.satellite(FunTimeFunestra.PORTRAIT).filter == "alpha"

        with patch("fun_time.satellite_commands.apply_satellite_filter") as mock_filter:
            mock_filter.return_value = _filter_result(count=10)
            funestra_state, _ = dispatch_command(
                "portrait_no_filter", BridgeState(portrait=SatelliteState(filter="alpha")), config)
        assert funestra_state.satellite(FunTimeFunestra.PORTRAIT).filter == ""
        assert _origenerator_cmds(config) == ["PORTRAIT_SAY:clear filter"]

    def test_every_phrase_the_hosted_app_answers_reaches_it_said_to_either_satellite(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()
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

    def test_funestra_mode_routes_nothing_to_origenerator(self, tmp_path):
        config = _origenerator_config(tmp_path)
        _state, _ = dispatch_command("portrait_next", BridgeState(), config)
        assert _origenerator_cmds(config) == []
        assert _cmds(config, 2) == ["NEXT"]


class TestALockOnAHostedSide:
    @pytest.mark.parametrize("funestra_locked", [False, True])
    @pytest.mark.parametrize("kept_for_kino", [False, True])
    def test_a_pressed_lock_locks_the_funestra_and_leaves_the_rest_to_the_hosted_app(
            self, kept_for_kino, funestra_locked, tmp_path):
        config = replace(_origenerator_config(tmp_path), regen_metadata_root=tmp_path / "metadata")
        image = tmp_path / "scene one.png"
        image.write_text("x", encoding="utf-8")
        _set_current(config, FunTimeFunestra.PORTRAIT, str(image), locked=funestra_locked)

        state, ops = dispatch_command(
            "portrait_lock", _in_origenerator_mode(portrait=SatelliteState(locked=kept_for_kino)),
            config)

        assert _cmds(config, FunTimeFunestra.PORTRAIT) == ["LOCK_ON"]
        assert _origenerator_cmds(config) == ["portrait_lock"]
        assert (ops, state.satellite(FunTimeFunestra.PORTRAIT).locked) == ([], kept_for_kino)
        assert not metadata_path_for(image, config.regen_metadata_root).exists()
        assert not (config.state_dir / "watch_stats.json").exists()

    def test_a_spoken_lock_brings_back_the_image_up_when_he_began_and_locks_that(
            self, tmp_path):
        config = _origenerator_config(tmp_path)
        meant = "C:\\images\\meant.png"
        _set_current(config, FunTimeFunestra.PORTRAIT, "C:\\images\\moved on to.png")

        dispatch_command("portrait_lock_on", _in_origenerator_mode(), config, target_path=meant)

        assert _cmds(config, FunTimeFunestra.PORTRAIT) == [f"PLAY_FILE {meant}", "LOCK_ON"]
        assert _origenerator_cmds(config) == ["portrait_lock_on"]

    @pytest.mark.parametrize("funestra_locked", [False, True])
    @pytest.mark.parametrize(("command", "to_the_funestra"), [
        ("portrait_lock_on", ["LOCK_ON"]), ("portrait_lock_off", [])])
    def test_a_lock_or_a_letting_go_said_is_handed_on_whatever_the_funestra_says_of_its_lock(
            self, command, to_the_funestra, funestra_locked, tmp_path):
        config = _origenerator_config(tmp_path)
        _set_current(config, FunTimeFunestra.PORTRAIT, "C:\\images\\up.png", locked=funestra_locked)

        _state, ops = dispatch_command(
            command, _in_origenerator_mode(portrait=SatelliteState(locked=not funestra_locked)),
            config)

        assert (ops, _cmds(config, FunTimeFunestra.PORTRAIT), _origenerator_cmds(config)) == (
            [], to_the_funestra, [command])

    def test_the_hosted_apps_own_word_for_its_lock_is_the_satellites_spoken_lock(self, tmp_path):
        config = replace(_origenerator_config(tmp_path),
                         origenerator_lock_phrases=frozenset({"hold"}))
        meant = "C:\\images\\meant.png"
        _set_current(config, FunTimeFunestra.PORTRAIT, "C:\\images\\moved on to.png")

        state, _ = dispatch_command("portrait_say_hold", _in_origenerator_mode(), config,
                                    target_path=meant)
        dispatch_command("portrait_say_fabricated_order", state, config)

        assert _cmds(config, FunTimeFunestra.PORTRAIT) == [f"PLAY_FILE {meant}", "LOCK_ON"]
        assert _origenerator_cmds(config) == ["portrait_lock_on", "PORTRAIT_SAY:fabricated order"]

    def test_the_same_word_said_to_a_satellite_in_kino_mode_is_ignored_as_before(self, tmp_path):
        config = replace(_origenerator_config(tmp_path),
                         origenerator_lock_phrases=frozenset({"hold"}))
        _set_current(config, FunTimeFunestra.PORTRAIT, "C:\\clips\\scene one.mp4")

        state, ops = dispatch_command("portrait_say_hold",
                                      _in_origenerator_mode(FunTimeFunestra.LANDSCAPE), config)

        assert ops == [WindowOp(op=Op.IGNORED, key="in Kino mode")]
        assert (_cmds(config, FunTimeFunestra.PORTRAIT), _origenerator_cmds(config)) == ([], [])
        assert not state.satellite(FunTimeFunestra.PORTRAIT).locked


class TestOmniPauseWithOrigenerator:
    def test_enter_freezes_the_hosted_app_too(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()
        state, _ = dispatch_command("enter_omnipause", state, config)
        assert state.omni_paused
        assert config.origenerator_paused_file.read_text(encoding="utf-8") == "1"

    def test_leave_sets_the_funestras_going_again_in_origenerator_mode(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode(omni_paused=True)
        state, _ = dispatch_command("omnipause_toggle", state, config)
        assert not state.omni_paused
        assert config.origenerator_paused_file.read_text(encoding="utf-8") == "0"
        assert config.portrait.paused_file.read_text(encoding="utf-8") == "0"
        assert config.landscape.paused_file.read_text(encoding="utf-8") == "0"


def test_toggle_lock_locks_and_favorites_when_unlocked(tmp_path: Path, caplog):
    config = _make_config(tmp_path)
    _set_current(config, 2, "clip.mp4")

    with (
        patch("fun_time.lock.mark_favorite") as favs,
        caplog.at_level(logging.INFO, logger="fun_time.satellite_groups"),
    ):
        state, _ops = _toggle_lock(2, _make_state(portrait=SatelliteState(locked=False)), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).locked is True
    assert _cmds(config, 2) == ["LOCK_ON"]
    assert favs.call_args[0][0] == "clip.mp4"
    assert "Locked portrait satellite" in caplog.text


def test_toggle_lock_unlocks_and_advances_when_locked(tmp_path: Path, caplog):
    config = _make_config(tmp_path)
    _set_current(config, 3, "clip.mp4")

    with caplog.at_level(logging.INFO, logger="fun_time.satellite_commands"):
        state, _ops = _toggle_lock(3, _make_state(landscape=SatelliteState(locked=True)), config)

    assert state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
    assert _cmds(config, 3) == ["LOCK_OFF", "NEXT"]
    assert "Unlocked landscape satellite" in caplog.text


def test_cancel_lock_writes_unlock_only_when_currently_locked(tmp_path: Path):
    config = _make_config(tmp_path)

    state = cancel_lock(2, _make_state(portrait=SatelliteState(locked=True)), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert _cmds(config, 2) == ["LOCK_OFF"]


def test_cancel_lock_writes_nothing_when_not_locked(tmp_path: Path):
    config = _make_config(tmp_path)

    state = cancel_lock(2, _make_state(portrait=SatelliteState(locked=False)), config)

    assert state.satellite(FunTimeFunestra.PORTRAIT).locked is False
    assert _cmds(config, 2) == []


def test_locking_a_known_video_opens_an_rfb_tab(tmp_path: Path):
    config = _make_config(tmp_path)

    _set_current(config, 2, rf"C:\videos\{load_web_providers()[0].marker}\abc_123.mp4")

    with patch("fun_time.lock.mark_favorite"):
        _state, ops = _toggle_lock(2, _make_state(portrait=SatelliteState(locked=False)), config)

    assert any(op.op == "open_rfb_tab" for op in ops)


def test_unlocking_opens_no_rfb_tab(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_current(config, 2, r"C:\videos\provider2\abc_123.mp4")

    _state, ops = _toggle_lock(2, _make_state(portrait=SatelliteState(locked=True)), config)

    assert not any(op.op == "open_rfb_tab" for op in ops)


def test_discard_of_a_non_favorite_unlocks_advances_and_moves_to_trash(tmp_path: Path, caplog):
    config = _make_config(tmp_path)
    _set_current(config, 3, "odd.mp4")

    with (
        patch("fun_time.satellite_commands.unfavorite") as favs,
        patch("fun_time.satellite_commands.move_to_trash") as trash,
        caplog.at_level(logging.INFO, logger="fun_time.satellite_commands"),
    ):
        state, ops = _discard(3, _make_state(landscape=SatelliteState(locked=True)), config)

    assert state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
    assert _cmds(config, 3) == ["LOCK_OFF", "TRASH"]
    assert favs.call_args[0][0] == "odd.mp4"
    assert trash.call_args[0][1] == Path("odd.mp4")
    assert "Discarding from Funestra 3: odd.mp4" in caplog.text
    assert [(op.op, op.key, op.source) for op in ops] == [
        ("message", "Sent to Trash", "landscape")
    ]


def test_unlocked_discard_trashes_without_unlocking(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_current(config, 3, "odd.mp4")

    with (
        patch("fun_time.satellite_commands.unfavorite"),
        patch("fun_time.satellite_commands.move_to_trash"),
    ):
        state, _ops = _discard(3, _make_state(landscape=SatelliteState(locked=False)), config)

    assert state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
    assert _cmds(config, 3) == ["TRASH"]


def _favorite_video(tmp_path: Path, config, name: str) -> Path:
    video = tmp_path / "clips" / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    config.regen_metadata_root = tmp_path / "metadata"
    mark_favorite(video, config.regen_metadata_root, True)
    return video


def test_discard_of_a_favorite_only_takes_it_out_of_the_favorites(tmp_path: Path, caplog):
    config = _make_config(tmp_path)
    video = _favorite_video(tmp_path, config, "kept.mp4")
    _set_current(config, 3, str(video))

    with caplog.at_level(logging.INFO, logger="fun_time.satellite_commands"):
        state, ops = _discard(3, _make_state(landscape=SatelliteState(locked=False)), config)

    assert state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
    assert _cmds(config, 3) == ["NEXT"]
    assert not is_favorite(video, config.regen_metadata_root)
    assert video.exists()
    assert list(config.trash_dir.iterdir()) == []
    assert f"Removed from favorites on Funestra 3: {video}" in caplog.text
    assert [(op.op, op.key, op.source) for op in ops] == [
        ("message", "Unfavorited", "landscape")
    ]


def test_an_unfavoriting_is_said_in_the_favorites_green_and_a_condemning_in_white(
        tmp_path: Path):
    config = _make_config(tmp_path)
    kept = _favorite_video(tmp_path, config, "kept.mp4")
    odd = tmp_path / "clips" / "odd.mp4"
    odd.write_bytes(b"")

    _set_current(config, 3, str(kept))
    _state, demoted = _discard(3, _make_state(), config)
    _set_current(config, 3, str(odd))
    _state, condemned = _discard(3, _make_state(), config)

    assert [(op.key, op.level) for op in demoted] == [("Unfavorited", FAVORITE)]
    assert [(op.key, op.level) for op in condemned] == [("Sent to Trash", MESSAGE)]


def test_discarding_a_locked_favorite_lets_the_satellite_go(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_current(config, 3, str(_favorite_video(tmp_path, config, "kept.mp4")))

    state, _ops = _discard(3, _make_state(landscape=SatelliteState(locked=True)), config)

    assert state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
    assert _cmds(config, 3) == ["LOCK_OFF", "NEXT"]


def test_demoting_a_favorite_the_funestra_already_left_does_not_drag_it_back(tmp_path: Path):
    config = _make_config(tmp_path)
    video = _favorite_video(tmp_path, config, "spoken_about.mp4")
    _set_current(config, 3, str(tmp_path / "clips" / "advanced_to.mp4"))

    _state, ops = _discard(3, _make_state(landscape=SatelliteState(locked=False)), config, target_path=str(video))

    assert _cmds(config, 3) == []
    assert not is_favorite(video, config.regen_metadata_root)
    assert [op.key for op in ops] == ["Unfavorited"]


def test_a_discard_before_the_funestra_has_named_its_video_leaves_the_funestra_alone(tmp_path: Path):
    config = _make_config(tmp_path)
    assert not config.satellite(FunTimeFunestra.LANDSCAPE).status_file.exists()

    state, ops = _discard(3, _make_state(landscape=SatelliteState(locked=True)), config)

    assert _cmds(config, 3) == []
    assert ops == []
    assert state.satellite(FunTimeFunestra.LANDSCAPE).locked is True


def test_a_spoken_discard_still_demotes_its_video_before_the_funestra_has_named_one(tmp_path: Path):
    config = _make_config(tmp_path)
    video = _favorite_video(tmp_path, config, "spoken_about.mp4")

    _state, ops = _discard(3, _make_state(landscape=SatelliteState(locked=False)), config,
                           target_path=str(video))

    assert not is_favorite(video, config.regen_metadata_root)
    assert [op.key for op in ops] == ["Unfavorited"]


def test_discarding_a_demoted_video_again_marks_it_trash(tmp_path: Path):
    config = _make_config(tmp_path)
    video = _favorite_video(tmp_path, config, "twice.mp4")
    _set_current(config, 2, str(video))

    _state, demote_ops = _discard(2, _make_state(portrait=SatelliteState(locked=False)), config)
    assert video.exists()

    _state, condemn_ops = _discard(2, _make_state(portrait=SatelliteState(locked=False)), config)

    assert [op.key for op in demote_ops] == ["Unfavorited"]
    assert [op.key for op in condemn_ops] == ["Sent to Trash"]
    assert [op.level for op in demote_ops] == [FAVORITE]
    assert [op.level for op in condemn_ops] == [MESSAGE]
    assert _cmds(config, 2) == ["NEXT", "TRASH"]
    assert not video.exists()
    assert [p.name for p in config.trash_dir.iterdir()] == ["twice.mp4"]


class TestASessionThatHostsNoOrigenerator:
    def test_a_resumed_origenerator_mode_still_drives_the_funestras(self, tmp_path):
        config = replace(_origenerator_config(tmp_path), origenerator_enabled=False)
        state = _in_origenerator_mode()

        assert not routes_to_origenerator("portrait_next", state, config)
        dispatch_command("portrait_next", state, config)

        assert _origenerator_cmds(config) == []
        assert _cmds(config, 2) == ["NEXT"]


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
        config = _origenerator_config(tmp_path)

        dispatch_command("landscape_hud_restore_at|lower_left",
                         _in_origenerator_mode(), config)

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

    @pytest.mark.parametrize("command", ["portrait_hud_minimize", "portrait_minimize"])
    def test_it_ends_keyboard_navigation_of_that_funestras_map(self, tmp_path, command):
        state = _make_state().with_satellite(FunTimeFunestra.PORTRAIT, nav_anchor="alpha.mp4")

        state, _ops = dispatch_command(command, state, _make_config(tmp_path))

        assert state.satellite(FunTimeFunestra.PORTRAIT).nav_anchor == ""


@pytest.fixture
def rebuilt_satellite():
    with patch("fun_time.satellite_commands.apply_satellite_filter") as rebuild:
        yield rebuild


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

    def test_a_satellite_narrows_its_browse_the_way_f_mode_does(self, tmp_path, rebuilt_satellite):
        config = _make_config(tmp_path)
        rebuilt_satellite.return_value = _filter_result(count=4)
        state, ops = dispatch_command("landscape_enhancement_original", _make_state(), config)

        assert state.satellite(FunTimeFunestra.LANDSCAPE).enhancement is EnhancementLevel.ORIGINAL
        assert state.satellite(FunTimeFunestra.PORTRAIT).enhancement is EnhancementLevel.BOTH
        assert rebuilt_satellite.call_args.kwargs["build"].enhancement is EnhancementLevel.ORIGINAL
        assert rebuilt_satellite.call_args.kwargs["funestra"] is FunTimeFunestra.LANDSCAPE
        assert ops == [WindowOp(op="message", key="Original only", source="landscape", level=MESSAGE)]

    def test_a_level_that_keeps_none_of_a_satellites_clips_is_not_kept_either(self, tmp_path, rebuilt_satellite):
        config = _make_config(tmp_path)
        rebuilt_satellite.return_value = _filter_result(count=0, applied=False, message="nothing")
        state, ops = dispatch_command("portrait_enhancement_enhanced", _make_state(), config)

        assert state.satellite(FunTimeFunestra.PORTRAIT).enhancement is EnhancementLevel.BOTH
        assert ops == [WindowOp(op="message", key="nothing", source="portrait", level=logging.WARNING)]

    def test_a_satellites_rebuilds_carry_its_level(self, tmp_path, rebuilt_satellite):
        config = _make_config(tmp_path)
        narrowed = _make_state(portrait=SatelliteState(enhancement=EnhancementLevel.ENHANCED))

        rebuilt_satellite.return_value = _filter_result(count=2)
        dispatch_command("portrait_no_filter", narrowed, config)
        _state, _ops, mock_fmode = _dispatch_fmode("portrait_fmode_on", narrowed, config)

        assert rebuilt_satellite.call_args.kwargs["build"].enhancement is EnhancementLevel.ENHANCED
        assert mock_fmode.call_args.kwargs["satellites"][FunTimeFunestra.PORTRAIT].enhancement is (
            EnhancementLevel.ENHANCED)

    def test_a_satellites_reset_puts_its_level_back(self, tmp_path, rebuilt_satellite):
        config = _make_config(tmp_path)
        narrowed = _make_state(portrait=SatelliteState(enhancement=EnhancementLevel.ENHANCED))

        rebuilt_satellite.return_value = _filter_result(count=9)
        state, _ops = dispatch_command("portrait_reset", narrowed, config)

        assert state.satellite(FunTimeFunestra.PORTRAIT).enhancement is EnhancementLevel.BOTH

    def test_said_to_a_satellite_in_origenerator_mode_it_is_the_slideshows_own_switch(self, tmp_path):
        config = _origenerator_config(tmp_path)
        state = _in_origenerator_mode()

        state, _ops = dispatch_command("portrait_enhancement_enhanced", state, config)
        publish_the_show(config, FunTimeFunestra.PORTRAIT, f_mode=False, enhanced=True)
        state, _ops = dispatch_command("portrait_enhancement_both", state, config)
        publish_the_show(config, FunTimeFunestra.PORTRAIT, f_mode=False, enhanced=False)
        state, _ops = dispatch_command("portrait_enhancement_both", state, config)
        state, ops = dispatch_command("portrait_enhancement_original", state, config)

        assert _origenerator_cmds(config) == ["PORTRAIT_SAY:enhanced only", "portrait_enhanced"]
        assert ops == [WindowOp(op=Op.IGNORED, key="in Origenerator mode")]
        assert state.satellite(FunTimeFunestra.PORTRAIT).enhancement is EnhancementLevel.BOTH


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
        config = _origenerator_config(tmp_path)

        dispatch_command("landscape_hud_upper_right", _in_origenerator_mode(FunTimeFunestra.LANDSCAPE), config)

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
        config = _origenerator_config(tmp_path)

        dispatch_command("portrait_mute", _in_origenerator_mode(FunTimeFunestra.PORTRAIT), config)

        assert _origenerator_cmds(config) == []
        assert _cmds(config, FunTimeFunestra.PORTRAIT) == ["SET_VOLUME 100 1"]

    def test_bare_sound_words_reach_whichever_funestra_was_addressed_last(self):
        assert parse_command("active_mute").aimed_at_the_active(1).text == "audio_mute"
        assert parse_command("active_unmute").aimed_at_the_active(1).text == "audio_unmute"
        assert parse_command("active_volume_down").aimed_at_the_active(1).text == "audio_volume_down"
        assert parse_command("active_volume_up").aimed_at_the_active(3).text == "landscape_volume_up"

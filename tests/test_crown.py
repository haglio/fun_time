from __future__ import annotations

from player_core.modes import MainMode

from fun_time.crown import Crown, majority, majority_now
from fun_time.shared_state import BridgeState


def test_the_crowned_main_player_playing_a_portrait_video_takes_most_of_the_screen():
    assert majority(Crown.MAIN, main_portrait=True, held=Crown.PORTRAIT) is Crown.MAIN


def test_the_crowned_main_player_playing_a_landscape_video_leaves_most_to_the_portrait_player():
    assert majority(Crown.MAIN, main_portrait=False, held=Crown.MAIN) is Crown.PORTRAIT


def test_the_crowned_portrait_player_keeps_most_of_the_screen_whatever_the_main_player_plays():
    assert majority(Crown.PORTRAIT, main_portrait=True, held=Crown.MAIN) is Crown.PORTRAIT
    assert majority(Crown.PORTRAIT, main_portrait=None, held=Crown.MAIN) is Crown.PORTRAIT


def test_a_shape_not_known_for_a_moment_leaves_the_monitor_as_it_is():
    assert majority(Crown.MAIN, main_portrait=None, held=Crown.MAIN) is Crown.MAIN
    assert majority(Crown.MAIN, main_portrait=None, held=Crown.PORTRAIT) is Crown.PORTRAIT


def test_in_genau_mode_the_crown_follows_the_shape_of_genaus_clip(tmp_path):
    main_player_status = tmp_path / "main_player_status.txt"
    main_player_status.write_text("portrait=0\n", encoding="utf-8")
    genau_status = tmp_path / "genau_status.txt"
    genau_status.write_text("portrait=1\n", encoding="utf-8")
    state = BridgeState(main_mode=MainMode.GENAU, crowned=Crown.MAIN)

    assert majority_now(state, main_player_status, genau_status) is Crown.MAIN


def test_in_video_mode_the_crown_follows_the_shape_of_the_main_players_video(tmp_path):
    main_player_status = tmp_path / "main_player_status.txt"
    main_player_status.write_text("portrait=1\n", encoding="utf-8")
    genau_status = tmp_path / "genau_status.txt"
    genau_status.write_text("portrait=0\n", encoding="utf-8")
    state = BridgeState(main_mode=MainMode.VIDEO, crowned=Crown.MAIN)

    assert majority_now(state, main_player_status, genau_status) is Crown.MAIN


def test_a_status_read_mid_write_leaves_the_monitor_as_it_is(tmp_path):
    main_player_status = tmp_path / "main_player_status.txt"
    main_player_status.write_text("", encoding="utf-8")
    state = BridgeState(main_mode=MainMode.VIDEO, crowned=Crown.MAIN, majority=Crown.MAIN)

    assert majority_now(state, main_player_status, tmp_path / "genau_status.txt") is Crown.MAIN

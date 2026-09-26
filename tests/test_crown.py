from __future__ import annotations

from player_core.modes import MainMode

from fun_time.crown import Crown, majority


def test_the_crowned_main_player_playing_a_portrait_video_takes_most_of_the_screen():
    assert majority(Crown.MAIN, main_mode=MainMode.VIDEO, main_portrait=True) is Crown.MAIN


def test_the_crowned_main_player_playing_a_landscape_video_leaves_most_to_the_portrait_player():
    assert majority(Crown.MAIN, main_mode=MainMode.VIDEO, main_portrait=False) is Crown.PORTRAIT


def test_the_crowned_portrait_player_keeps_most_of_the_screen_whatever_the_main_player_plays():
    assert majority(Crown.PORTRAIT, main_mode=MainMode.VIDEO, main_portrait=True) is Crown.PORTRAIT


def test_genau_mode_leaves_most_of_the_screen_to_the_portrait_player_whatever_is_crowned():
    assert majority(Crown.MAIN, main_mode=MainMode.GENAU, main_portrait=True) is Crown.PORTRAIT


def test_a_main_video_whose_shape_is_not_known_yet_leaves_most_to_the_portrait_player():
    assert majority(Crown.MAIN, main_mode=MainMode.VIDEO, main_portrait=None) is Crown.PORTRAIT

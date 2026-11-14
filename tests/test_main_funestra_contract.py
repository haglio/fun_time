from __future__ import annotations

from pathlib import Path

from funestra.channel_manifest import channel_flag

from fun_time.osr2_section import osr2_readout_path
from main_funestra.cli import build_parser
from main_funestra.contract import MainChannels


def _args(*extra: str):
    return build_parser({}).parse_args([
        channel_flag("playlist"), "C:/state/main_player_playlist.tsv",
        channel_flag("command"), "C:/state/main_player_cmd.txt",
        channel_flag("paused"), "C:/state/main_player_paused.txt",
        channel_flag("status"), "C:/state/main_player_status.txt",
        channel_flag("notice"), "C:/state/main_player_notice.txt",
        "--state-dir", "C:/state",
        channel_flag("console"), "C:/state/main_player_console.json",
        channel_flag("drive"), "C:/state/genau_drive.txt",
        channel_flag("dashboard_cmd"), "C:/state/dashboard_cmd.txt",
        channel_flag("osr2_port"), "51000",
        *extra,
    ])


def test_every_file_fun_time_names_lands_on_its_channel():
    channels = MainChannels.from_args(_args())

    assert channels.playlist == Path("C:/state/main_player_playlist.tsv")
    assert channels.command == Path("C:/state/main_player_cmd.txt")
    assert channels.paused == Path("C:/state/main_player_paused.txt")
    assert channels.status == Path("C:/state/main_player_status.txt")
    assert channels.notice == Path("C:/state/main_player_notice.txt")
    assert channels.console == Path("C:/state/main_player_console.json")
    assert channels.drive == Path("C:/state/genau_drive.txt")
    assert channels.dashboard_cmd == Path("C:/state/dashboard_cmd.txt")
    assert (channels.osr2_sender, channels.osr2_port) == ("main", 51000)


def test_where_each_video_was_left_is_kept_in_the_state_dir():
    assert MainChannels.from_args(_args()).play_points == Path("C:/state/main_player_play_points.json")


def test_the_osr2_readout_it_hands_on_is_kept_in_the_state_dir():
    assert MainChannels.from_args(_args()).osr2_readout == osr2_readout_path(Path("C:/state"), "main")


def test_a_main_funestra_draws_the_main_hud_never_a_satellite_hud():
    assert MainChannels.from_args(_args()).hud is None

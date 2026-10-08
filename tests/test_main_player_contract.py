"""The files Fun Time launches the Main Funestra with, read off its command line whole."""
from __future__ import annotations

from pathlib import Path

from main_player.cli import build_parser
from main_player.contract import MainChannels


def _args(*extra: str):
    return build_parser({}).parse_args([
        "--playlist", "C:/state/main_player_playlist.tsv",
        "--command-file", "C:/state/main_player_cmd.txt",
        "--paused-file", "C:/state/main_player_paused.txt",
        "--status-file", "C:/state/main_player_status.txt",
        "--state-dir", "C:/state",
        "--console-file", "C:/state/main_player_console.json",
        "--drive-file", "C:/state/genau_drive.txt",
        "--dashboard-cmd-file", "C:/state/dashboard_cmd.txt",
        "--tcode-host", "127.0.0.1", "--tcode-port", "51000",
        *extra,
    ])


def test_every_file_fun_time_names_lands_on_its_channel():
    channels = MainChannels.from_args(_args())

    assert channels.playlist == Path("C:/state/main_player_playlist.tsv")
    assert channels.command == Path("C:/state/main_player_cmd.txt")
    assert channels.paused == Path("C:/state/main_player_paused.txt")
    assert channels.status == Path("C:/state/main_player_status.txt")
    assert channels.console == Path("C:/state/main_player_console.json")
    assert channels.drive == Path("C:/state/genau_drive.txt")
    assert channels.dashboard_cmd == Path("C:/state/dashboard_cmd.txt")
    assert (channels.tcode_host, channels.tcode_port) == ("127.0.0.1", 51000)


def test_where_each_video_was_left_is_kept_in_the_state_dir():
    assert MainChannels.from_args(_args()).play_points == Path("C:/state/main_player_play_points.json")


def test_a_main_funestra_draws_the_console_never_a_satellite_panel():
    assert MainChannels.from_args(_args()).hud is None

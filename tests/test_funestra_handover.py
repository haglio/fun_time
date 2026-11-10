from __future__ import annotations

from pathlib import Path

from funestra.file_channel import consume_command_file, publish_whole
from funestra.playlist import PlaylistItem, read_playlist, write_playlist

from fun_time.config import SatelliteChannel
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.funestra_handover import hand_back, hud_stamp, keep_aside, let_go_since


def _channel(tmp_path: Path) -> SatelliteChannel:
    return SatelliteChannel.in_state_dir(tmp_path, FunTimeFunestra.PORTRAIT)


def _paths(path: Path) -> list[str]:
    return [item.path.name for item in read_playlist(path)]


def _playing(channel: SatelliteChannel, video: str) -> None:
    channel.status_file.write_text(f"video={video}\n", encoding="utf-8")


def _the_hosted_app_writes_its_own_list_over(channel: SatelliteChannel) -> None:
    write_playlist(channel.playlist_file,
                   [PlaylistItem(channel.playlist_file.parent / "image.png")])


def test_coming_home_lands_the_funestra_on_the_video_it_left(tmp_path):
    channel = _channel(tmp_path)
    write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / name)
                                        for name in ("one.mp4", "two.mp4", "three.mp4")])
    _playing(channel, str(tmp_path / "two.mp4"))

    keep_aside(channel)
    _the_hosted_app_writes_its_own_list_over(channel)
    assert hand_back(channel) is True

    assert _paths(channel.playlist_file) == ["two.mp4", "three.mp4", "one.mp4"]
    assert consume_command_file(channel.cmd_file, uppercase=False) == ["RELOAD_PLAYLIST"]


def test_coming_home_lands_on_the_video_it_had_moved_on_to_before_the_app_took_it(tmp_path):
    channel = _channel(tmp_path)
    write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / name)
                                        for name in ("one.mp4", "two.mp4", "three.mp4")])
    _playing(channel, str(tmp_path / "one.mp4"))
    keep_aside(channel)
    _the_hosted_app_writes_its_own_list_over(channel)

    assert hand_back(channel, onto=str(tmp_path / "two.mp4")) is True

    assert _paths(channel.playlist_file) == ["two.mp4", "three.mp4", "one.mp4"]


def test_the_kept_list_is_spent_by_handing_it_back(tmp_path):
    channel = _channel(tmp_path)
    write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / "one.mp4")])
    keep_aside(channel)

    assert hand_back(channel) is True
    assert hand_back(channel) is False


def test_a_satellite_with_no_list_of_its_own_has_nothing_to_keep(tmp_path):
    channel = _channel(tmp_path)

    keep_aside(channel)

    assert hand_back(channel) is False
    assert not channel.cmd_file.exists()


def test_the_app_has_let_go_once_it_publishes_the_satellite_empty_after_the_leave(tmp_path):
    channel = _channel(tmp_path)
    publish_whole(channel.origenerator_hud_file, '{"Funestra": "portrait"}')
    since = hud_stamp(channel)
    assert not let_go_since(channel, since)

    publish_whole(channel.origenerator_hud_file, "")

    assert let_go_since(channel, since)


def test_an_empty_hud_already_there_at_the_leave_is_no_letting_go(tmp_path):
    channel = _channel(tmp_path)
    publish_whole(channel.origenerator_hud_file, "")

    assert not let_go_since(channel, hud_stamp(channel))


def test_a_hud_the_app_publishes_after_the_leave_is_the_satellite_still_held(tmp_path):
    channel = _channel(tmp_path)
    since = hud_stamp(channel)

    publish_whole(channel.origenerator_hud_file, '{"Funestra": "portrait"}')

    assert not let_go_since(channel, since)


def test_a_satellite_that_never_came_home_keeps_the_list_it_left_with(tmp_path):
    channel = _channel(tmp_path)
    write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / "one.mp4")])
    keep_aside(channel)
    _the_hosted_app_writes_its_own_list_over(channel)

    keep_aside(channel)

    assert hand_back(channel) is True
    assert _paths(channel.playlist_file) == ["one.mp4"]

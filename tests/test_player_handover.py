"""A satellite handed to the hosted Origenerator, and taken back.

Handing a player over costs the session its own list for that player — the hosted
app writes the player's playlist file — so the list is kept aside first and put
back when the player comes home.  What these pin is that coming home lands the
player on the clip it was showing when it left.
"""
from __future__ import annotations

from pathlib import Path

from player_core.file_channel import consume_command_file, publish_whole
from player_core.playlist import PlaylistItem, read_playlist, write_playlist

from fun_time.bridge_records import SatelliteChannel
from fun_time.player_handover import hand_back, keep_aside, let_go_since, panel_stamp


def _channel(tmp_path: Path) -> SatelliteChannel:
    return SatelliteChannel(
        cmd_file=tmp_path / "portrait_cmd.txt",
        paused_file=tmp_path / "portrait_paused.txt",
        status_file=tmp_path / "portrait_status.txt",
        playlist_file=tmp_path / "portrait_playlist.tsv",
        sources="",
        origenerator_hud_file=tmp_path / "origenerator_portrait_hud.json",
    )


def _paths(path: Path) -> list[str]:
    return [item.path.name for item in read_playlist(path)]


def _playing(channel: SatelliteChannel, video: str) -> None:
    channel.status_file.write_text(f"video={video}\n", encoding="utf-8")


def test_coming_home_lands_the_player_on_the_clip_it_left(tmp_path):
    """The kept list is turned onto the clip on screen, and a player handed a
    list it is not playing opens at the top of it — which is that clip."""
    channel = _channel(tmp_path)
    write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / name)
                                        for name in ("one.mp4", "two.mp4", "three.mp4")])
    _playing(channel, str(tmp_path / "two.mp4"))

    keep_aside(channel)
    write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / "picture.png")])
    assert hand_back(channel) is True

    assert _paths(channel.playlist_file) == ["two.mp4", "three.mp4", "one.mp4"]
    assert consume_command_file(channel.cmd_file, uppercase=False) == ["RELOAD_PLAYLIST"]


def test_the_kept_list_is_spent_by_handing_it_back(tmp_path):
    """A side handed back twice must not be handed a stale list the second
    time — over one the session has rebuilt since."""
    channel = _channel(tmp_path)
    write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / "one.mp4")])
    keep_aside(channel)

    assert hand_back(channel) is True
    assert hand_back(channel) is False


def test_a_side_with_no_list_of_its_own_has_nothing_to_keep(tmp_path):
    """Nothing kept, nothing handed back: the player keeps whatever it has
    rather than being told to read an empty list."""
    channel = _channel(tmp_path)

    keep_aside(channel)

    assert hand_back(channel) is False
    assert not channel.cmd_file.exists()


def test_the_app_has_let_go_once_it_publishes_the_side_empty_after_the_leave(tmp_path):
    """Its close is the one thing it publishes only after it has read the
    leave, so an empty panel from before the leave says nothing."""
    channel = _channel(tmp_path)
    publish_whole(channel.origenerator_hud_file, '{"player": "portrait"}')
    since = panel_stamp(channel)
    assert not let_go_since(channel, since)

    publish_whole(channel.origenerator_hud_file, "")

    assert let_go_since(channel, since)


def test_an_empty_panel_already_there_at_the_leave_is_no_letting_go(tmp_path):
    """The app had not taken the side yet: the list it is about to write would
    land on top of the one handed back."""
    channel = _channel(tmp_path)
    publish_whole(channel.origenerator_hud_file, "")

    assert not let_go_since(channel, panel_stamp(channel))


def test_a_panel_the_app_publishes_after_the_leave_is_the_side_still_held(tmp_path):
    channel = _channel(tmp_path)
    since = panel_stamp(channel)

    publish_whole(channel.origenerator_hud_file, '{"player": "portrait"}')

    assert not let_go_since(channel, since)


def test_a_side_that_never_came_home_keeps_the_list_it_left_with(tmp_path):
    """Going back into the mode before a side was handed back must not keep
    the hosted app's list aside in place of the session's own."""
    channel = _channel(tmp_path)
    write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / "one.mp4")])
    keep_aside(channel)
    write_playlist(channel.playlist_file, [PlaylistItem(tmp_path / "picture.png")])

    keep_aside(channel)

    assert hand_back(channel) is True
    assert _paths(channel.playlist_file) == ["one.mp4"]

from __future__ import annotations

from pathlib import Path

import fun_time.vlc_actions as vlc_actions


def test_get_repeat_mode_reads_one_from_repeat_xml(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, "<loop>false</loop><repeat>true</repeat>"))

    assert vlc_actions.get_repeat_mode(8080, "pw") == "one"


def test_get_playback_state_reads_state_from_xml(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, "<state>paused</state>"))

    assert vlc_actions.get_playback_state(8080, "pw") == "paused"


def test_wait_for_http_succeeds_when_state_tag_appears(monkeypatch):
    responses = iter([(0, ""), (200, "<state>playing</state>")])
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": next(responses))

    assert vlc_actions.wait_for_http(8080, "pw", 500, sleep_fn=lambda _seconds: None) is True


def test_get_current_file_path_decodes_current_file_uri(monkeypatch):
    xml = '<leaf uri="file:///C:/clips/demo%20clip.mp4" current="current"></leaf>'
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))

    assert vlc_actions.get_current_file_path(8080, "pw") == r"C:\clips\demo clip.mp4"


def test_ensure_playback_state_toggles_pause_until_target(monkeypatch):
    states = iter(["paused", "paused", "playing"])
    commands: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: next(states))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, command, password: commands.append(command) or True)

    assert vlc_actions.ensure_playback_state(8080, "pw", True, sleep_fn=lambda _seconds: None) is True
    assert commands == ["pl_pause", "pl_pause"]


def test_set_repeat_mode_toggles_loop_until_all(monkeypatch):
    modes = iter(["off", "off", "all"])
    commands: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_repeat_mode", lambda port, password: next(modes))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, command, password: commands.append(command) or True)

    assert vlc_actions.set_repeat_mode(8080, "pw", "all", sleep_fn=lambda _seconds: None) is True
    assert commands == ["pl_loop", "pl_loop"]


def test_replace_playlist_from_file_sends_empty_stop_input_and_repeat(monkeypatch, tmp_path: Path):
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    commands: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, command, password: commands.append(command) or True)
    monkeypatch.setattr(vlc_actions, "send_vlc_input_command", lambda port, command, full_path, password: command == "in_play" and full_path == str(playlist))
    monkeypatch.setattr(vlc_actions, "set_repeat_mode", lambda port, password, target, sleep_fn=None: target == "all")

    assert vlc_actions.replace_playlist_from_file(8080, "pw", playlist, repeat_mode="all", sleep_fn=lambda _seconds: None) is True
    assert commands == ["pl_empty", "pl_stop"]


def test_replace_playlist_from_file_fails_when_playlist_missing(tmp_path: Path):
    assert vlc_actions.replace_playlist_from_file(8080, "pw", tmp_path / "missing.m3u", sleep_fn=lambda _seconds: None) is False


# --- vlc_nav_step ---

_PLAYLIST_XML = """\
<?xml version="1.0" encoding="utf-8" standalone="yes" ?>
<node name="root" id="1" ro="rw">
 <node name="Playlist" id="2" ro="rw">
  <leaf name="a.mp4" id="3" uri="file:///C:/a.mp4" current="current" duration="120000" ro="rw"/>
  <leaf name="b.mp4" id="4" uri="file:///C:/b.mp4" duration="90000" ro="rw"/>
  <leaf name="c.mp4" id="5" uri="file:///C:/c.mp4" duration="60000" ro="rw"/>
 </node>
</node>
"""


def test_vlc_nav_step_prev_calls_pl_play_with_prev_item_id(monkeypatch):
    """prev on item 3 (index 0) should wrap to item 5 (last)."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "prev")

    assert result is True
    assert calls == ["pl_play&id=5"]


def test_vlc_nav_step_next_calls_pl_play_with_next_item_id(monkeypatch):
    """next on item 3 (index 0) should go to item 4."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is True
    assert calls == ["pl_play&id=4"]


def test_vlc_nav_step_next_wraps_at_end(monkeypatch):
    """next on last item (id=5) should wrap to first item (id=3)."""
    xml = _PLAYLIST_XML.replace('id="3" uri', 'id="3" uri').replace(
        'id="5" uri="file:///C:/c.mp4" duration="60000" ro="rw"',
        'id="5" uri="file:///C:/c.mp4" current="current" duration="60000" ro="rw"',
    ).replace(
        'id="3" uri="file:///C:/a.mp4" current="current"',
        'id="3" uri="file:///C:/a.mp4"',
    )
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is True
    assert calls == ["pl_play&id=3"]


def test_vlc_nav_step_returns_false_when_vlc_unreachable(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))

    assert vlc_actions.vlc_nav_step(8090, "pw", "next") is False

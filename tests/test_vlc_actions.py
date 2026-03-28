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
#
# The XML below matches the actual VLC 3.x jstree format verified against a real
# VLC instance.  Container nodes (root, Playlist folder) have no uri= attribute.
# Media items use id="plid_N" (numeric suffix only is passed to pl_play&id=N).

_PLAYLIST_XML = """\
<?xml version="1.0" encoding="utf-8" standalone="yes" ?>
<root>
<item id="plid_0" name="" ro="rw"><content><name></name></content>\
<item id="plid_1" name="Playlist" ro="ro"><content><name>Playlist</name></content>\
<item id="plid_3" uri="file:///C:/a.mp4" name="a.mp4" current="current" ro="rw" duration="120000"><content><name>a.mp4</name></content></item>\
<item id="plid_4" uri="file:///C:/b.mp4" name="b.mp4" ro="rw" duration="90000"><content><name>b.mp4</name></content></item>\
<item id="plid_5" uri="file:///C:/c.mp4" name="c.mp4" ro="rw" duration="60000"><content><name>c.mp4</name></content></item>\
</item></item>
</root>
"""

# Same playlist but with the last item (plid_5) as current
_PLAYLIST_XML_LAST_CURRENT = _PLAYLIST_XML.replace(
    'id="plid_3" uri="file:///C:/a.mp4" name="a.mp4" current="current"',
    'id="plid_3" uri="file:///C:/a.mp4" name="a.mp4"',
).replace(
    'id="plid_5" uri="file:///C:/c.mp4" name="c.mp4" ro="rw"',
    'id="plid_5" uri="file:///C:/c.mp4" name="c.mp4" current="current" ro="rw"',
)


def test_vlc_nav_step_prev_calls_pl_play_with_prev_item_id(monkeypatch):
    """prev on plid_3 (first item) should wrap to plid_5 (last)."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "prev")

    assert result is True
    assert calls == ["pl_play&id=5"]


def test_vlc_nav_step_next_calls_pl_play_with_next_item_id(monkeypatch):
    """next on plid_3 (first item, current) should go to plid_4."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is True
    assert calls == ["pl_play&id=4"]


def test_vlc_nav_step_next_wraps_at_end(monkeypatch):
    """next on last item (plid_5, current) should wrap to first item (plid_3)."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML_LAST_CURRENT))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is True
    assert calls == ["pl_play&id=3"]


def test_vlc_nav_step_container_nodes_not_counted_as_items(monkeypatch):
    """Container nodes (plid_0 root, plid_1 Playlist folder) must not appear
    in the navigation sequence — only media items with a uri= attribute should."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    vlc_actions.vlc_nav_step(8090, "pw", "next")

    # Container plid_0 and plid_1 must not appear as navigation targets
    assert calls == ["pl_play&id=4"]  # not plid_0 or plid_1


def test_vlc_nav_step_returns_false_when_vlc_unreachable(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))

    assert vlc_actions.vlc_nav_step(8090, "pw", "next") is False


# --- get_current_playlist_id ---


def test_get_current_playlist_id_returns_current_item_id(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))

    assert vlc_actions.get_current_playlist_id(8090, "pw") == 3


def test_get_current_playlist_id_returns_negative_one_on_failure(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))

    assert vlc_actions.get_current_playlist_id(8090, "pw") == -1


def test_get_current_playlist_id_returns_negative_one_when_no_current(monkeypatch):
    xml_no_current = _PLAYLIST_XML.replace('current="current" ', '')
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml_no_current))

    assert vlc_actions.get_current_playlist_id(8090, "pw") == -1


# --- vlc_delete_playlist_item ---


def test_vlc_delete_playlist_item_sends_pl_delete_command(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_delete_playlist_item(8090, "pw", 7)

    assert result is True
    assert calls == ["pl_delete&id=7"]

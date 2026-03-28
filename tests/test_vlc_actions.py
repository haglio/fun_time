from __future__ import annotations

from pathlib import Path

import pytest

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

def _make_playlist_xml(items: list[tuple[int, Path, bool]]) -> str:
    """Build VLC playlist XML from (plid, file_path, is_current) tuples."""
    inner = ""
    for plid, path, is_current in items:
        uri = path.as_uri()
        cur = ' current="current"' if is_current else ""
        inner += (
            f'<item id="plid_{plid}" uri="{uri}" name="{path.name}"{cur} ro="rw">'
            f"<content><name>{path.name}</name></content></item>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>'
        '<root><item id="plid_0" name="" ro="rw"><content><name></name></content>'
        '<item id="plid_1" name="Playlist" ro="ro"><content><name>Playlist</name></content>'
        f"{inner}</item></item></root>"
    )


@pytest.fixture()
def three_videos(tmp_path):
    """Create three video files and return (a, b, c) paths."""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    c = tmp_path / "c.mp4"
    for f in (a, b, c):
        f.write_bytes(b"")
    return a, b, c


def test_vlc_nav_step_prev_calls_pl_play_with_prev_item_id(monkeypatch, three_videos):
    """prev on plid_3 (first item) should wrap to plid_5 (last)."""
    a, b, c = three_videos
    xml = _make_playlist_xml([(3, a, True), (4, b, False), (5, c, False)])
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "prev")

    assert result is True
    assert calls == ["pl_play&id=5"]


def test_vlc_nav_step_next_calls_pl_play_with_next_item_id(monkeypatch, three_videos):
    """next on plid_3 (first item, current) should go to plid_4."""
    a, b, c = three_videos
    xml = _make_playlist_xml([(3, a, True), (4, b, False), (5, c, False)])
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is True
    assert calls == ["pl_play&id=4"]


def test_vlc_nav_step_next_wraps_at_end(monkeypatch, three_videos):
    """next on last item (plid_5, current) should wrap to first item (plid_3)."""
    a, b, c = three_videos
    xml = _make_playlist_xml([(3, a, False), (4, b, False), (5, c, True)])
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is True
    assert calls == ["pl_play&id=3"]


def test_vlc_nav_step_container_nodes_not_counted_as_items(monkeypatch, three_videos):
    """Container nodes (plid_0 root, plid_1 Playlist folder) must not appear
    in the navigation sequence — only media items with a uri= attribute should."""
    a, b, c = three_videos
    xml = _make_playlist_xml([(3, a, True), (4, b, False), (5, c, False)])
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    vlc_actions.vlc_nav_step(8090, "pw", "next")

    # Container plid_0 and plid_1 must not appear as navigation targets
    assert calls == ["pl_play&id=4"]  # not plid_0 or plid_1


def test_vlc_nav_step_returns_false_when_vlc_unreachable(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))

    assert vlc_actions.vlc_nav_step(8090, "pw", "next") is False


# --- vlc_nav_step: playlist healing (skip dead entries) ---


def test_vlc_nav_step_next_skips_missing_file(monkeypatch, tmp_path):
    """When the next item's file doesn't exist (moved to weird_dir),
    vlc_nav_step should skip it and play the one after."""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"  # dead — not created
    c = tmp_path / "c.mp4"
    a.write_bytes(b"")
    c.write_bytes(b"")

    xml = _make_playlist_xml([(3, a, True), (4, b, False), (5, c, False)])
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is True
    assert "pl_play&id=5" in calls, "should skip dead plid_4 and play plid_5"
    assert "pl_delete&id=4" in calls, "should clean up the dead entry"


def test_vlc_nav_step_prev_skips_missing_file(monkeypatch, tmp_path):
    """Going backward should also skip dead entries."""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"  # dead
    c = tmp_path / "c.mp4"
    a.write_bytes(b"")
    c.write_bytes(b"")

    xml = _make_playlist_xml([(3, a, False), (4, b, False), (5, c, True)])
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "prev")

    assert result is True
    assert "pl_play&id=3" in calls, "should skip dead plid_4 and play plid_3"
    assert "pl_delete&id=4" in calls


def test_vlc_nav_step_all_others_dead_returns_false(monkeypatch, tmp_path):
    """If every item except current is dead, navigation should fail gracefully."""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"  # dead
    c = tmp_path / "c.mp4"  # dead
    a.write_bytes(b"")

    xml = _make_playlist_xml([(3, a, True), (4, b, False), (5, c, False)])
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is False
    assert "pl_delete&id=4" in calls, "should still clean up dead entries"
    assert "pl_delete&id=5" in calls



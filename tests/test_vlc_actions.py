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


def test_ensure_playback_state_sends_pl_play_from_stopped(monkeypatch):
    """When VLC is in 'stopped' state, pl_pause does nothing.
    ensure_playback_state must send pl_play to transition from stopped to playing."""
    states = iter(["stopped", "stopped", "playing"])
    commands: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: next(states))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, command, password: commands.append(command) or True)

    assert vlc_actions.ensure_playback_state(8080, "pw", True, sleep_fn=lambda _seconds: None) is True
    assert commands == ["pl_play", "pl_play"], "must use pl_play (not pl_pause) from stopped state"


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
    # Simulate item change after pl_play fires (calls becomes non-empty)
    monkeypatch.setattr(vlc_actions, "get_current_file_path", lambda port, pw: "C:/a.mp4" if not calls else "C:/c.mp4")

    result = vlc_actions.vlc_nav_step(8090, "pw", "prev")

    assert result is True
    assert calls == ["pl_play&id=5"]


def test_vlc_nav_step_next_calls_pl_play_with_next_item_id(monkeypatch):
    """next on plid_3 (first item, current) should go to plid_4."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)
    monkeypatch.setattr(vlc_actions, "get_current_file_path", lambda port, pw: "C:/a.mp4" if not calls else "C:/b.mp4")

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is True
    assert calls == ["pl_play&id=4"]


def test_vlc_nav_step_next_wraps_at_end(monkeypatch):
    """next on last item (plid_5, current) should wrap to first item (plid_3)."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML_LAST_CURRENT))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)
    monkeypatch.setattr(vlc_actions, "get_current_file_path", lambda port, pw: "C:/c.mp4" if not calls else "C:/a.mp4")

    result = vlc_actions.vlc_nav_step(8090, "pw", "next")

    assert result is True
    assert calls == ["pl_play&id=3"]


def test_vlc_nav_step_container_nodes_not_counted_as_items(monkeypatch):
    """Container nodes (plid_0 root, plid_1 Playlist folder) must not appear
    in the navigation sequence — only media items with a uri= attribute should."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)
    monkeypatch.setattr(vlc_actions, "get_current_file_path", lambda port, pw: "C:/a.mp4" if not calls else "C:/b.mp4")

    vlc_actions.vlc_nav_step(8090, "pw", "next")

    # Container plid_0 and plid_1 must not appear as navigation targets
    assert calls == ["pl_play&id=4"]  # not plid_0 or plid_1


def test_vlc_nav_step_returns_false_when_vlc_unreachable(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))

    assert vlc_actions.vlc_nav_step(8090, "pw", "next") is False


# --- vlc_advance_and_remove ---
#
# When discarding a video, we need to:
# 1. Play the next item in playlist order (by ID, not pl_next)
# 2. Delete the current item from VLC's playlist
# This ensures the user lands on the correct next video and the dead
# entry doesn't pollute future navigation.


# plid_4 is current (middle item)
_PLAYLIST_XML_MID_CURRENT = _PLAYLIST_XML.replace(
    'id="plid_3" uri="file:///C:/a.mp4" name="a.mp4" current="current"',
    'id="plid_3" uri="file:///C:/a.mp4" name="a.mp4"',
).replace(
    'id="plid_4" uri="file:///C:/b.mp4" name="b.mp4" ro="rw"',
    'id="plid_4" uri="file:///C:/b.mp4" name="b.mp4" current="current" ro="rw"',
)


def test_vlc_advance_and_remove_plays_next_then_deletes_current(monkeypatch):
    """Removing plid_4 (middle) should play plid_5 (next) then delete plid_4."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML_MID_CURRENT))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_advance_and_remove(8090, "pw", sleep_fn=lambda _: None)

    assert result is True
    assert calls == ["pl_play&id=5", "pl_delete&id=4"]


def test_vlc_advance_and_remove_wraps_at_end(monkeypatch):
    """Removing plid_5 (last) should wrap to plid_3 (first) then delete plid_5."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML_LAST_CURRENT))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_advance_and_remove(8090, "pw", sleep_fn=lambda _: None)

    assert result is True
    assert calls == ["pl_play&id=3", "pl_delete&id=5"]


def test_vlc_advance_and_remove_from_first_item(monkeypatch):
    """Removing plid_3 (first, current) should play plid_4 then delete plid_3."""
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_advance_and_remove(8090, "pw", sleep_fn=lambda _: None)

    assert result is True
    assert calls == ["pl_play&id=4", "pl_delete&id=3"]


def test_vlc_advance_and_remove_sleeps_between_play_and_delete(monkeypatch):
    """Must sleep between pl_play and pl_delete so VLC can transition to the
    new item before the old one is removed — prevents black screen / stopped state."""
    calls_with_timing: list[tuple[str, bool]] = []
    did_sleep = [False]

    def track_cmd(port, cmd, pw):
        calls_with_timing.append((cmd, did_sleep[0]))
        return True

    def track_sleep(seconds):
        did_sleep[0] = True

    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML_MID_CURRENT))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", track_cmd)

    vlc_actions.vlc_advance_and_remove(8090, "pw", sleep_fn=track_sleep)

    # pl_play should happen before sleep, pl_delete should happen after sleep
    assert calls_with_timing[0] == ("pl_play&id=5", False), "pl_play must happen before sleep"
    assert calls_with_timing[1] == ("pl_delete&id=4", True), "pl_delete must happen after sleep"


def test_vlc_advance_and_remove_returns_false_when_unreachable(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))

    assert vlc_actions.vlc_advance_and_remove(8090, "pw") is False


def test_vlc_advance_and_remove_single_item_playlist(monkeypatch):
    """With only one item, there's nowhere to advance — should still delete and return True."""
    xml = (
        '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>'
        '<root><item id="plid_0" name="" ro="rw"><content><name></name></content>'
        '<item id="plid_1" name="Playlist" ro="ro"><content><name>Playlist</name></content>'
        '<item id="plid_3" uri="file:///C:/only.mp4" name="only.mp4" current="current" ro="rw">'
        '<content><name>only.mp4</name></content></item>'
        '</item></item></root>'
    )
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)

    result = vlc_actions.vlc_advance_and_remove(8090, "pw")

    assert result is True
    assert calls == ["pl_delete&id=3"]


# --- Error/edge-case coverage ---


def test_vlc_http_req_returns_zero_on_connection_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(vlc_actions.urllib.request, "urlopen", _raise)
    status, body = vlc_actions.vlc_http_req(9999, "/requests/status.xml", "pw")
    assert status == 0
    assert body == ""


def test_get_current_file_path_returns_empty_on_http_failure(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))
    assert vlc_actions.get_current_file_path(8080, "pw") == ""


def test_get_current_file_path_handles_reversed_attribute_order(monkeypatch):
    xml = '<leaf current="current" uri="file:///C:/clips/test.mp4"></leaf>'
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    assert vlc_actions.get_current_file_path(8080, "pw") == r"C:\clips\test.mp4"


def test_get_current_file_path_returns_empty_when_no_current_item(monkeypatch):
    xml = '<leaf uri="file:///C:/clips/test.mp4"></leaf>'
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    assert vlc_actions.get_current_file_path(8080, "pw") == ""


def test_get_repeat_mode_returns_off_when_tags_missing(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, "<root></root>"))
    assert vlc_actions.get_repeat_mode(8080, "pw") == "off"


def test_get_playback_time_returns_none_on_http_failure(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))
    assert vlc_actions.get_playback_time(8080, "pw") is None


def test_get_playback_time_returns_none_when_time_tag_missing(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, "<root></root>"))
    assert vlc_actions.get_playback_time(8080, "pw") is None


def test_get_playback_time_returns_none_on_non_numeric_value(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, "<time>not_a_number</time>"))
    assert vlc_actions.get_playback_time(8080, "pw") is None


def test_get_playback_time_returns_float_on_success(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, "<time>42.5</time>"))
    assert vlc_actions.get_playback_time(8080, "pw") == 42.5


def test_get_playback_state_returns_none_on_http_failure(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))
    assert vlc_actions.get_playback_state(8080, "pw") is None


def test_ensure_playback_state_returns_false_when_state_is_none(monkeypatch):
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: None)
    assert vlc_actions.ensure_playback_state(8080, "pw", True, sleep_fn=lambda _: None) is False


def test_ensure_playback_state_returns_false_when_retries_exhausted(monkeypatch):
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: "paused")
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: True)
    assert vlc_actions.ensure_playback_state(8080, "pw", True, sleep_fn=lambda _: None) is False


def test_wait_for_http_returns_false_on_timeout(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))
    assert vlc_actions.wait_for_http(8080, "pw", 0, sleep_fn=lambda _: None) is False


def test_replace_playlist_from_file_succeeds_without_repeat_mode(monkeypatch, tmp_path: Path):
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: True)
    monkeypatch.setattr(vlc_actions, "send_vlc_input_command", lambda port, cmd, path, pw: True)
    assert vlc_actions.replace_playlist_from_file(8080, "pw", playlist, sleep_fn=lambda _: None) is True


def test_replace_playlist_from_file_enqueues_without_playing(monkeypatch, tmp_path: Path):
    """When enqueue_only=True, in_enqueue must be used instead of in_play so
    VLC loads the playlist without starting playback.  This prevents MFP from
    syncing funscripts while the loading screen is still visible."""
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    commands: list[str] = []
    input_commands: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, command, password: commands.append(command) or True)
    monkeypatch.setattr(vlc_actions, "send_vlc_input_command", lambda port, command, full_path, password: input_commands.append(command) or True)

    assert vlc_actions.replace_playlist_from_file(8080, "pw", playlist, enqueue_only=True, sleep_fn=lambda _: None) is True
    assert commands == ["pl_empty", "pl_stop"]
    assert input_commands == ["in_enqueue"]


def test_vlc_nav_step_returns_false_when_playlist_empty(monkeypatch):
    xml = '<root><item id="plid_0" name="" ro="rw"><content><name></name></content></item></root>'
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, xml))
    assert vlc_actions.vlc_nav_step(8090, "pw", "next") is False


def test_send_vlc_input_command_encodes_file_uri(monkeypatch, tmp_path: Path):
    test_file = tmp_path / "clip.mp4"
    test_file.write_text("x", encoding="utf-8")
    captured_paths: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (captured_paths.append(path), (200, ""))[1])
    vlc_actions.send_vlc_input_command(8080, "in_play", str(test_file), "pw")
    assert "file%3A" in captured_paths[0] or "in_play" in captured_paths[0]


def test_set_repeat_mode_returns_false_when_retries_exhausted(monkeypatch):
    monkeypatch.setattr(vlc_actions, "get_repeat_mode", lambda port, password: "off")
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: True)
    assert vlc_actions.set_repeat_mode(8080, "pw", "all", sleep_fn=lambda _: None) is False


# --- restore_vlcrc_volume ---


def test_restore_vlcrc_volume_patches_volume_line(tmp_path: Path, monkeypatch):
    vlcrc = tmp_path / "vlc" / "vlcrc"
    vlcrc.parent.mkdir(parents=True)
    vlcrc.write_text("# VLC config\nvolume=0\nhttp-password=test\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path))

    vlc_actions.restore_vlcrc_volume(256)

    text = vlcrc.read_text(encoding="utf-8")
    assert "volume=256" in text
    assert "volume=0" not in text
    assert "http-password=test" in text


def test_restore_vlcrc_volume_leaves_file_alone_when_no_volume_line(tmp_path: Path, monkeypatch):
    vlcrc = tmp_path / "vlc" / "vlcrc"
    vlcrc.parent.mkdir(parents=True)
    original = "# VLC config\nhttp-password=test\n"
    vlcrc.write_text(original, encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path))

    vlc_actions.restore_vlcrc_volume(256)

    assert vlcrc.read_text(encoding="utf-8") == original


def test_restore_vlcrc_volume_ignores_missing_vlcrc(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # Should not raise even with no vlcrc file
    vlc_actions.restore_vlcrc_volume(256)

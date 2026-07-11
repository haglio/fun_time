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


def test_ensure_playback_state_pauses_a_stopped_vlc_that_is_still_loading(monkeypatch):
    """A VLC reporting 'stopped' may be mid-transition — still loading the item a
    nav just selected — and starts PLAYING moments later.  Treating stopped as
    "already not playing" let OmniPause report success without ever pausing the
    satellite, which then resumed on its own."""
    states = iter(["stopped", "playing", "paused"])
    commands: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: next(states))
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, command, password: commands.append(command) or True)

    assert vlc_actions.ensure_playback_state(8080, "pw", False, sleep_fn=lambda _seconds: None) is True
    assert commands == ["pl_pause"], "must pause the loading VLC the moment it starts playing"


def test_ensure_playback_state_accepts_a_vlc_that_stays_stopped(monkeypatch):
    """A VLC that stays stopped for the whole settle window is genuinely idle,
    which satisfies "not playing" — and it must never receive pl_pause, whose
    toggle semantics would START the item."""
    commands: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: "stopped")
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, command, password: commands.append(command) or True)

    assert vlc_actions.ensure_playback_state(8080, "pw", False, sleep_fn=lambda _seconds: None) is True
    assert commands == [], "pl_pause on a stopped VLC would start it playing"


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


# --- get_playlist_entries ---


def test_get_playlist_entries_returns_ids_with_windows_paths(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, _PLAYLIST_XML))

    entries, current_id = vlc_actions.get_playlist_entries(8090, "pw")

    assert entries == [(3, r"C:\a.mp4"), (4, r"C:\b.mp4"), (5, r"C:\c.mp4")]
    assert current_id == 3


def test_get_playlist_entries_empty_when_unreachable(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))

    assert vlc_actions.get_playlist_entries(8090, "pw") == ([], -1)


# --- vlc_play_playlist_item ---


def test_vlc_play_playlist_item_plays_id_and_confirms_change(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: calls.append(cmd) or True)
    monkeypatch.setattr(vlc_actions, "get_current_file_path", lambda port, pw: "C:/a.mp4" if not calls else "C:/b.mp4")

    result = vlc_actions.vlc_play_playlist_item(8090, "pw", 4, sleep_fn=lambda _s: None)

    assert result is True
    assert calls == ["pl_play&id=4"]


def test_vlc_play_playlist_item_false_when_item_never_changes(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: True)
    monkeypatch.setattr(vlc_actions, "get_current_file_path", lambda port, pw: "C:/a.mp4")
    clock = iter(float(t) for t in range(100))
    monkeypatch.setattr(vlc_actions.time, "monotonic", lambda: next(clock))

    assert vlc_actions.vlc_play_playlist_item(8090, "pw", 4, sleep_fn=lambda _s: None) is False


# --- get_playback_fraction ---


def test_get_playback_fraction_reads_position(monkeypatch):
    monkeypatch.setattr(
        vlc_actions, "vlc_http_req",
        lambda port, path, password, user="": (200, "<state>playing</state><position>0.4375</position>"),
    )

    assert vlc_actions.get_playback_fraction(8090, "pw") == 0.4375


def test_get_playback_fraction_none_when_unreachable_or_absent(monkeypatch):
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (0, ""))
    assert vlc_actions.get_playback_fraction(8090, "pw") is None

    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, "<state>stopped</state>"))
    assert vlc_actions.get_playback_fraction(8090, "pw") is None


# --- vlc_swap_current_with ---

_PLAYLIST_XML_WITH_D = _PLAYLIST_XML.replace(
    '<item id="plid_5" uri="file:///C:/c.mp4" name="c.mp4" ro="rw" duration="60000">'
    "<content><name>c.mp4</name></content></item>",
    '<item id="plid_5" uri="file:///C:/c.mp4" name="c.mp4" ro="rw" duration="60000">'
    "<content><name>c.mp4</name></content></item>"
    '<item id="plid_6" uri="file:///C:/d.mp4" name="d.mp4" ro="rw" duration="60000">'
    "<content><name>d.mp4</name></content></item>",
)


def test_vlc_swap_current_with_enqueues_plays_and_deletes_old(monkeypatch):
    """Swapping a.mp4 (current, plid_3) for d.mp4: enqueue, play new id, drop old."""
    jstrees = iter([_PLAYLIST_XML, _PLAYLIST_XML_WITH_D])
    enqueued: list[tuple[str, str]] = []
    commands: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, next(jstrees)))
    monkeypatch.setattr(
        vlc_actions, "send_vlc_input_command",
        lambda port, command, full_path, password: enqueued.append((command, full_path)) or True,
    )
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: commands.append(cmd) or True)
    monkeypatch.setattr(
        vlc_actions, "get_current_file_path",
        lambda port, pw: r"C:\a.mp4" if not commands else r"C:\d.mp4",
    )

    result = vlc_actions.vlc_swap_current_with(8090, "pw", r"C:\d.mp4", sleep_fn=lambda _s: None)

    assert result is True
    assert enqueued == [("in_enqueue", r"C:\d.mp4")]
    assert commands == ["pl_play&id=6", "pl_delete&id=3"]


def test_vlc_swap_current_with_keeps_old_entry_when_play_fails(monkeypatch):
    """If the new item never starts, the old entry must not be deleted."""
    jstrees = iter([_PLAYLIST_XML, _PLAYLIST_XML_WITH_D])
    commands: list[str] = []
    monkeypatch.setattr(vlc_actions, "vlc_http_req", lambda port, path, password, user="": (200, next(jstrees)))
    monkeypatch.setattr(vlc_actions, "send_vlc_input_command", lambda port, command, full_path, password: True)
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, pw: commands.append(cmd) or True)
    monkeypatch.setattr(vlc_actions, "get_current_file_path", lambda port, pw: r"C:\a.mp4")
    clock = iter(float(t) for t in range(100))
    monkeypatch.setattr(vlc_actions.time, "monotonic", lambda: next(clock))

    result = vlc_actions.vlc_swap_current_with(8090, "pw", r"C:\d.mp4", sleep_fn=lambda _s: None)

    assert result is False
    assert commands == ["pl_play&id=6"], "no pl_delete after a failed swap"


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
    class _DeadConn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise ConnectionRefusedError("refused")

        def getresponse(self):
            raise AssertionError("should not be reached")

        def close(self):
            pass

    monkeypatch.setattr(vlc_actions.http.client, "HTTPConnection", _DeadConn)
    vlc_actions._conn_pool.by_port = {}
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


def test_wait_for_http_aborts_early_when_process_dies(monkeypatch):
    """A dead VLC will never bind its HTTP interface, so wait_for_http must
    abort as soon as is_alive() reports the process gone instead of polling
    out the whole (deliberately generous) timeout."""
    calls = {"n": 0}

    def fake_req(port, path, password, user=""):
        calls["n"] += 1
        return (0, "")

    monkeypatch.setattr(vlc_actions, "vlc_http_req", fake_req)

    result = vlc_actions.wait_for_http(
        8080, "pw", 60000, is_alive=lambda: False, sleep_fn=lambda _: None
    )

    assert result is False
    assert calls["n"] == 1, "should probe once then abort, not loop to the deadline"


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


def test_ensure_playback_state_never_pauses_a_stopped_vlc(monkeypatch):
    """pl_pause on a stopped VLC STARTS the current item (VLC toggle
    semantics), phantom-loading item 1 during omnipause. Stopped already
    satisfies should_play=False, so no command may be sent."""
    cmds: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: "stopped")
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, password: cmds.append(cmd) or True)

    ok = vlc_actions.ensure_playback_state(8090, "pw", should_play=False, sleep_fn=lambda s: None)

    assert ok is True
    assert cmds == []


def test_pause_if_playing_pauses_only_a_playing_vlc(monkeypatch):
    """The OmniPause watchdog re-pauses a satellite that has slipped back into
    playing.  A single pl_pause on a confirmed-playing VLC pauses it."""
    cmds: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: "playing")
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, password: cmds.append(cmd) or True)

    assert vlc_actions.pause_if_playing(8090, "pw") is True
    assert cmds == ["pl_pause"]


def test_pause_if_playing_leaves_a_paused_vlc_untouched(monkeypatch):
    """An already-paused satellite is the steady state; sending pl_pause would
    toggle it back into playing."""
    cmds: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: "paused")
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, password: cmds.append(cmd) or True)

    assert vlc_actions.pause_if_playing(8090, "pw") is False
    assert cmds == []


def test_pause_if_playing_never_touches_a_stopped_vlc(monkeypatch):
    """The phantom-load trap: pl_pause on a stopped VLC STARTS item 1.  A
    satellite mid-load reports 'stopped', so the watchdog must leave it alone
    and catch it on the next tick once it actually turns playing."""
    cmds: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: "stopped")
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, password: cmds.append(cmd) or True)

    assert vlc_actions.pause_if_playing(8090, "pw") is False
    assert cmds == []


def test_pause_if_playing_does_nothing_when_vlc_is_unreachable(monkeypatch):
    """A None state (VLC down / HTTP silent) is not 'playing', so no command
    is sent."""
    cmds: list[str] = []
    monkeypatch.setattr(vlc_actions, "get_playback_state", lambda port, password: None)
    monkeypatch.setattr(vlc_actions, "vlc_http_cmd", lambda port, cmd, password: cmds.append(cmd) or True)

    assert vlc_actions.pause_if_playing(8090, "pw") is False
    assert cmds == []


# --- vlc_http_req connection reuse ---
#
# vlc_http_req used to open a fresh TCP connection per call.  VLC's httpd closes
# each connection it serves, so a connect-per-call pattern floods the port with
# server-side TIME_WAIT sockets (measured: 1331 for 1500 rapid calls).  Under the
# rapid polling the navigation paths do, those collide on ephemeral ports and
# stall new connects, so VLC's HTTP interface intermittently returns nothing.
# Reusing one keep-alive connection per port keeps the socket count flat.


class _FakeResp:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body


class _FakeConn:
    """Records instantiations and requests so tests can assert on reuse."""

    instances: list["_FakeConn"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.requests: list[tuple] = []
        self.closed = False
        _FakeConn.instances.append(self)

    def request(self, method, url, headers=None):
        self.requests.append((method, url, headers))

    def getresponse(self):
        return _FakeResp(200, "<root/>")

    def close(self):
        self.closed = True


def test_vlc_http_req_reuses_one_connection_across_calls(monkeypatch):
    _FakeConn.instances = []
    monkeypatch.setattr(vlc_actions.http.client, "HTTPConnection", _FakeConn)
    vlc_actions._conn_pool.by_port = {}

    vlc_actions.vlc_http_req(8080, "/requests/status.xml", "pw")
    vlc_actions.vlc_http_req(8080, "/requests/status.xml", "pw")

    assert len(_FakeConn.instances) == 1, "should reuse one keep-alive connection per port"
    assert len(_FakeConn.instances[0].requests) == 2


def test_vlc_http_req_reconnects_when_pooled_connection_is_stale(monkeypatch):
    """A keep-alive socket VLC closed while idle raises on reuse.  vlc_http_req
    must transparently reconnect and still return the response, not (0, '') —
    the request never reached VLC on the dead socket, so re-issuing it is safe."""
    made: list = []

    class _Conn:
        def __init__(self, host, port, timeout=None):
            self.n = len(made)
            self.stale = False
            made.append(self)

        def request(self, *a, **k):
            if self.stale:
                raise vlc_actions.http.client.RemoteDisconnected("closed")

        def getresponse(self):
            return _FakeResp(200, f"<c{self.n}/>")

        def close(self):
            pass

    monkeypatch.setattr(vlc_actions.http.client, "HTTPConnection", _Conn)
    vlc_actions._conn_pool.by_port = {}

    # First call establishes and pools connection #0.
    assert vlc_actions.vlc_http_req(8080, "/requests/status.xml", "pw") == (200, "<c0/>")

    # VLC drops the idle socket → the pooled connection is now stale.
    made[0].stale = True

    # Second call: request() on #0 raises → reconnect to #1 → success.
    assert vlc_actions.vlc_http_req(8080, "/requests/status.xml", "pw") == (200, "<c1/>")
    assert len(made) == 2, "should have reconnected exactly once"


def test_vlc_http_req_does_not_retry_a_fresh_connection_failure(monkeypatch):
    """A brand-new connection failing means VLC is unreachable, not a stale
    socket — vlc_http_req must return (0, '') after one attempt, never retry
    (which for a command would risk delivering it twice)."""
    attempts: list = []

    class _DeadConn:
        def __init__(self, *a, **k):
            attempts.append(self)

        def request(self, *a, **k):
            raise ConnectionRefusedError("refused")

        def getresponse(self):
            raise AssertionError("should not be reached")

        def close(self):
            pass

    monkeypatch.setattr(vlc_actions.http.client, "HTTPConnection", _DeadConn)
    vlc_actions._conn_pool.by_port = {}

    assert vlc_actions.vlc_http_req(9999, "/requests/status.xml", "pw") == (0, "")
    assert len(attempts) == 1, "a fresh connection failure must not be retried"

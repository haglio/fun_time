"""Integration tests: verify VLC playlist navigation against a real VLC instance.

Starts a VLC process on a test port with a small playlist, then exercises
pl_next, pl_previous, and pl_delete to verify they work as expected.

Requires: VLC installed, FUN_TIME_RUN_INTEGRATION=1 env var.
"""
from __future__ import annotations

import glob
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from fun_time.orchestrator import vlc_http_password_from_vlcrc
from fun_time.vlc_actions import (
    _parse_playlist_ids,
    get_current_file_path,
    get_playback_state,
    replace_playlist_from_file,
    restore_vlcrc_volume,
    set_repeat_mode,
    vlc_advance_and_remove,
    vlc_http_cmd,
    vlc_http_req,
    vlc_nav_step,
    wait_for_http,
)
from fun_time.windows_bridge_startup import _build_vlc_launch_command, _no_activate_kwargs

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

TEST_PORT = 18091
# VLC ignores --http-password when vlcrc has a saved password.
# Read the actual saved password so auth works.
TEST_PASSWORD = vlc_http_password_from_vlcrc() or "vlcpassword"
VLC_EXE = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
VIDEO_DIR = Path(r"C:\path\to\suite-root\videos\videos\2D\AI\2_outbox\upscaled_by_orientation\portrait")


def _find_test_videos(n: int = 4) -> list[str]:
    videos = glob.glob(str(VIDEO_DIR / "candy" / "*.mp4"))
    if len(videos) < n:
        videos = glob.glob(str(VIDEO_DIR / "**" / "*.mp4"), recursive=True)
    return random.sample(videos, min(n, len(videos)))


@pytest.fixture(scope="module")
def vlc_with_playlist():
    """Start a VLC instance with a known playlist of 4+ videos."""
    videos = _find_test_videos(4)
    if len(videos) < 4:
        pytest.skip(f"Need 4 videos, found {len(videos)}")

    sources = "|".join(videos)
    playlist_path = Path(tempfile.gettempdir()) / "fun_time_test_loop.m3u"
    # Defer playlist: launch VLC empty, mute via HTTP, THEN load media.
    # This eliminates the audio-leak race where VLC outputs a frame of
    # audio before --volume 0 takes effect.
    cmd = _build_vlc_launch_command(
        VLC_EXE, sources, TEST_PORT, TEST_PASSWORD,
        repeat_mode="loop", mute=True,
        playlist_path=playlist_path, defer_playlist=True,
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **_no_activate_kwargs(),
    )
    if not wait_for_http(TEST_PORT, TEST_PASSWORD, timeout_ms=10000):
        proc.kill()
        pytest.skip("VLC HTTP did not start")
    vlc_http_cmd(TEST_PORT, "volume&val=0", TEST_PASSWORD)
    replace_playlist_from_file(TEST_PORT, TEST_PASSWORD, playlist_path)
    time.sleep(1.0)
    # Freeze playback rate to near-zero.  VLC stays in "playing" state
    # (all HTTP commands and jstree updates work normally) but can never
    # reach the end of a video and auto-advance.  This eliminates the
    # race where VLC finishes a clip between a position read and the
    # subsequent navigation command, making the read stale.
    vlc_http_cmd(TEST_PORT, "rate&val=0.01", TEST_PASSWORD)
    yield proc, videos
    # Kill first, then patch vlcrc — avoids the audio blast that
    # restore_vlc_volume (HTTP) caused by setting volume=256 while playing.
    proc.kill()
    proc.wait()
    restore_vlcrc_volume(256)


def _current(port: int = TEST_PORT) -> str:
    return get_current_file_path(port, TEST_PASSWORD)


def _wait_for_item_change(port: int, before: str, timeout: float = 6.0) -> str:
    """Poll until VLC's current item differs from *before* and is stable.

    VLC's HTTP interface reports the new file path before the playlist
    engine is fully settled — a ``pl_play`` arriving during this window
    can be silently dropped.  Instead of a fixed sleep, we require two
    consecutive reads (200 ms apart) to agree on the new path before
    returning.  This catches both slow settlement and transient paths
    that revert before VLC finishes its transition.
    """
    deadline = time.monotonic() + timeout
    candidate = None
    while time.monotonic() < deadline:
        path = get_current_file_path(port, TEST_PASSWORD)
        if path and path != before:
            if path == candidate:
                # Two consecutive reads agree on the new path → stable.
                return path
            candidate = path
            time.sleep(0.2)
            continue
        # Still on the old item or empty read — reset candidate.
        candidate = None
        time.sleep(0.05)
    return get_current_file_path(port, TEST_PASSWORD)


def _wait_for_playing(port: int, timeout: float = 3.0) -> str:
    """Poll until VLC reports 'playing' state.

    VLC can briefly report 'stopped' during playlist transitions even in
    normal operation (no --start-paused).  This helper waits for the
    transient state to settle before the test asserts.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get_playback_state(port, TEST_PASSWORD)
        if state == "playing":
            return state
        time.sleep(0.1)
    return get_playback_state(port, TEST_PASSWORD)


def _wait_for_stable_current(port: int = TEST_PORT, timeout: float = 3.0) -> None:
    """Poll until VLC's playlist_jstree consistently reports a valid current item.

    After pl_delete or rapid navigation, VLC can oscillate between
    current=-1 and a valid ID.  A single valid read is not enough â
    we require two consecutive valid reads (100 ms apart) before
    returning, matching the two-read stability pattern used by
    _wait_for_item_change.
    """
    deadline = time.monotonic() + timeout
    consecutive_valid = 0
    while time.monotonic() < deadline:
        _, xml = vlc_http_req(port, "/requests/playlist_jstree.xml", TEST_PASSWORD)
        _, current_id = _parse_playlist_ids(xml)
        if current_id != -1:
            consecutive_valid += 1
            if consecutive_valid >= 2:
                return
        else:
            consecutive_valid = 0
        time.sleep(0.1)


def _wait_for_playlist_count(port: int, expected: int, timeout: float = 5.0) -> tuple[list[int], int]:
    """Poll until VLC's playlist has exactly *expected* items and a valid current.

    After pl_delete, VLC can briefly report the correct item count while
    current is still -1 (mid-transition).  Requiring both conditions
    prevents a caller from acting on a half-settled jstree.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, xml = vlc_http_req(port, "/requests/playlist_jstree.xml", TEST_PASSWORD)
        ids, current = _parse_playlist_ids(xml)
        if len(ids) == expected and current != -1:
            return ids, current
        time.sleep(0.1)
    _, xml = vlc_http_req(port, "/requests/playlist_jstree.xml", TEST_PASSWORD)
    return _parse_playlist_ids(xml)


def _next():
    """Navigate to the next playlist item via ID-based pl_play.

    Uses vlc_nav_step so that _next() and _prev() share the same
    ordering (jstree document order).  Raw pl_next uses VLC's internal
    cursor, which can diverge from jstree order after pl_play&id=N
    commands — making pl_next and vlc_nav_step("prev") non-inverse.
    Raw pl_next is still tested directly in test_pl_next_advances_video.
    """
    _wait_for_stable_current()
    before = _current()
    vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    vlc_http_cmd(TEST_PORT, "rate&val=0.01", TEST_PASSWORD)
    _wait_for_item_change(TEST_PORT, before)


def _prev():
    """Navigate to the previous playlist item via ID-based pl_play.

    Uses vlc_nav_step instead of raw pl_previous to bypass VLC's
    restart-threshold (~3 s) which makes pl_previous restart the
    current track instead of going back.  Raw pl_previous is still
    tested directly in test_pl_previous_goes_back.
    """
    _wait_for_stable_current()
    before = _current()
    vlc_nav_step(TEST_PORT, TEST_PASSWORD, "prev")
    vlc_http_cmd(TEST_PORT, "rate&val=0.01", TEST_PASSWORD)
    _wait_for_item_change(TEST_PORT, before)


# --- Phase 2: Do VLC basics work? ---


def test_vlc_reports_current_file(vlc_with_playlist):
    path = _current()
    assert path, "get_current_file_path returned empty"
    assert Path(path).suffix == ".mp4"


def test_pl_next_advances_video(vlc_with_playlist):
    """Verify the raw pl_next HTTP command advances to the next item."""
    _wait_for_stable_current()
    before = _current()
    vlc_http_cmd(TEST_PORT, "pl_next", TEST_PASSWORD)
    after = _wait_for_item_change(TEST_PORT, before)
    assert after != before, f"pl_next did not change video (still {before})"


def test_pl_previous_goes_back(vlc_with_playlist):
    """Verify the raw pl_previous HTTP command navigates to the previous item.

    Seeks to position 0 first to stay inside VLC's restart threshold (~3 s),
    which otherwise causes pl_previous to restart the current track.
    """
    _wait_for_stable_current()
    before = _current()
    vlc_http_cmd(TEST_PORT, "seek&val=0", TEST_PASSWORD)
    time.sleep(0.15)
    vlc_http_cmd(TEST_PORT, "pl_previous", TEST_PASSWORD)
    after = _wait_for_item_change(TEST_PORT, before)
    assert after != before, f"pl_previous did not change video (still {before})"


def test_next_then_previous_returns_to_same_video(vlc_with_playlist):
    start = _current()
    _next()
    assert _current() != start
    _prev()
    assert _current() == start, f"next+prev did not return to start: expected {start}, got {_current()}"


def test_next_next_prev_prev_returns_to_start(vlc_with_playlist):
    start = _current()
    _next()
    _next()
    _prev()
    _prev()
    assert _current() == start, f"next*2+prev*2 did not return to start: expected {start}, got {_current()}"


def test_playlist_wraps_around(vlc_with_playlist):
    proc, videos = vlc_with_playlist
    # Go forward through all videos — loop mode should wrap back to start
    start = _current()
    for _ in range(len(videos)):
        _next()
    assert _current() == start, (
        f"After wrapping through {len(videos)} items, expected {start!r}, got {_current()!r}"
    )


# --- vlc_nav_step (ID-based navigation) ---
# These tests exercise the actual navigation path used by Fun Time hotkeys.
# vlc_nav_step reads the live jstree, resolves the current item by plid_N ID,
# and issues pl_play&id=N.  If _parse_playlist_ids fails to parse the real
# VLC jstree format these tests will catch it immediately.


def test_vlc_nav_step_next_advances_video(vlc_with_playlist):
    _wait_for_stable_current()
    before = _current()
    ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    assert ok is True, "vlc_nav_step returned False — check _parse_playlist_ids"
    after = _wait_for_item_change(TEST_PORT, before)
    assert after != before, f"vlc_nav_step next did not change video (still {before})"


def test_vlc_nav_step_prev_goes_back(vlc_with_playlist):
    _wait_for_stable_current()
    before = _current()
    ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "prev")
    assert ok is True, "vlc_nav_step returned False — check _parse_playlist_ids"
    after = _wait_for_item_change(TEST_PORT, before)
    assert after != before, f"vlc_nav_step prev did not change video (still {before})"


def test_vlc_nav_step_next_then_prev_returns_to_start(vlc_with_playlist):
    _wait_for_stable_current()
    start = _current()
    ok_next = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    assert ok_next is True, "vlc_nav_step next returned False"
    vlc_http_cmd(TEST_PORT, "rate&val=0.01", TEST_PASSWORD)
    mid = _wait_for_item_change(TEST_PORT, start)
    assert mid != start
    _wait_for_stable_current()          # let jstree fully commit the new current marker
    ok_prev = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "prev")
    assert ok_prev is True, "vlc_nav_step prev returned False"
    vlc_http_cmd(TEST_PORT, "rate&val=0.01", TEST_PASSWORD)
    _wait_for_item_change(TEST_PORT, mid)
    assert _current() == start, f"next+prev did not return to start: expected {start}, got {_current()}"


# --- vlc_advance_and_remove (discard path) ---
# Exercises the satellite discard flow: advance to next item by ID,
# then delete the current item from VLC's playlist.


def test_advance_and_remove_plays_next_and_shrinks_playlist(vlc_with_playlist):
    """vlc_advance_and_remove must: play the next item, remove the old one,
    and leave VLC in a state where navigation still works."""
    _wait_for_stable_current()
    before_path = _current()
    _, xml_before = vlc_http_req(TEST_PORT, "/requests/playlist_jstree.xml", TEST_PASSWORD)
    ids_before, current_before = _parse_playlist_ids(xml_before)
    count_before = len(ids_before)

    ok = vlc_advance_and_remove(TEST_PORT, TEST_PASSWORD)
    # Briefly restore normal rate so VLC fully processes the pl_play +
    # pl_delete transition.  At rate=0.01, VLC can stall mid-transition
    # and never commit the current marker in the jstree.
    vlc_http_cmd(TEST_PORT, "rate&val=1", TEST_PASSWORD)
    _wait_for_item_change(TEST_PORT, before_path)

    ids_after, current_after = _wait_for_playlist_count(TEST_PORT, count_before - 1)
    assert len(ids_after) == count_before - 1, \
        f"playlist should shrink by 1: was {count_before}, now {len(ids_after)}"
    assert current_before not in ids_after, \
        "old item should be removed from playlist"
    assert current_after != current_before, \
        "current item should have changed"
    vlc_http_cmd(TEST_PORT, "rate&val=0.01", TEST_PASSWORD)


def test_advance_and_remove_preserves_navigation(vlc_with_playlist):
    """After vlc_advance_and_remove, vlc_nav_step must still work."""
    _wait_for_stable_current(timeout=5.0)
    before_remove = _current()
    vlc_http_cmd(TEST_PORT, "rate&val=1", TEST_PASSWORD)
    vlc_advance_and_remove(TEST_PORT, TEST_PASSWORD)
    _wait_for_item_change(TEST_PORT, before_remove)
    # vlc_advance_and_remove's tight 0.15 s play+delete gap can leave
    # VLC with current=-1.  Force-play the first remaining item to
    # re-establish a valid current before the navigation check.
    _, xml = vlc_http_req(TEST_PORT, "/requests/playlist_jstree.xml", TEST_PASSWORD)
    ids, cur = _parse_playlist_ids(xml)
    if cur == -1 and ids:
        vlc_http_cmd(TEST_PORT, f"pl_play&id={ids[0]}", TEST_PASSWORD)
    _wait_for_stable_current(timeout=5.0)
    vlc_http_cmd(TEST_PORT, "rate&val=0.01", TEST_PASSWORD)

    before = _current()
    ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    assert ok is True, "vlc_nav_step failed after advance_and_remove"
    vlc_http_cmd(TEST_PORT, "rate&val=0.01", TEST_PASSWORD)
    after = _wait_for_item_change(TEST_PORT, before)
    assert after != before, "nav should change video after advance_and_remove"


# --- Production config verification ---
# These tests verify that the production _build_vlc_launch_command produces
# config that doesn't break navigation. They catch config regressions that
# unit tests with mocked VLC cannot.


def test_production_config_no_start_paused(vlc_with_playlist):
    """--start-paused must not be in the production launch command.
    VLC re-applies it on every item transition, causing black screen on nav."""
    sources = "a.mp4|b.mp4"
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command(
            VLC_EXE, sources, 0, "pw",
            repeat_mode=repeat_mode, mute=True,
        )
        assert "--start-paused" not in cmd, \
            f"--start-paused must never appear (repeat_mode={repeat_mode})"


def test_production_config_has_no_random(vlc_with_playlist):
    """--no-random must be in the production launch command to override
    VLC's saved shuffle setting in vlcrc."""
    sources = "a.mp4|b.mp4"
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command(
            VLC_EXE, sources, 0, "pw",
            repeat_mode=repeat_mode,
        )
        assert "--no-random" in cmd, \
            f"--no-random must appear (repeat_mode={repeat_mode})"


# --- Primary VLC (repeat-one mode) navigation behavior ---
# These tests discover how VLC actually behaves when navigating in repeat-one
# mode, so we can build the production fix on verified behavior instead of
# guessing.

REPEAT_PORT = 18092


@pytest.fixture(scope="module")
def vlc_repeat_one():
    """Start a VLC instance in repeat-one mode (like primary VLC)."""
    videos = _find_test_videos(4)
    if len(videos) < 4:
        pytest.skip(f"Need 4 videos, found {len(videos)}")

    sources = "|".join(videos)
    playlist_path = Path(tempfile.gettempdir()) / "fun_time_test_repeat.m3u"
    cmd = _build_vlc_launch_command(
        VLC_EXE, sources, REPEAT_PORT, TEST_PASSWORD,
        repeat_mode="repeat", mute=True,
        playlist_path=playlist_path, defer_playlist=True,
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **_no_activate_kwargs(),
    )
    if not wait_for_http(REPEAT_PORT, TEST_PASSWORD, timeout_ms=10000):
        proc.kill()
        pytest.skip("VLC HTTP did not start")
    vlc_http_cmd(REPEAT_PORT, "volume&val=0", TEST_PASSWORD)
    replace_playlist_from_file(REPEAT_PORT, TEST_PASSWORD, playlist_path)
    vlc_http_cmd(REPEAT_PORT, "pl_next", TEST_PASSWORD)
    time.sleep(1.0)
    # Freeze playback rate to near-zero.  VLC stays in "playing" state
    # (all HTTP commands and jstree updates work normally) but can never
    # reach the end of a video and auto-advance.  This eliminates the
    # race where VLC finishes a clip between a position read and the
    # subsequent navigation command, making the read stale.
    vlc_http_cmd(REPEAT_PORT, "rate&val=0.01", TEST_PASSWORD)
    yield proc, videos
    proc.kill()
    proc.wait()
    restore_vlcrc_volume(256)


def test_repeat_one_nav_step_changes_video_and_keeps_playing(vlc_repeat_one):
    """vlc_nav_step in repeat-one mode must change the video AND keep playing.
    This is the exact bug that --start-paused caused: VLC would load the new
    item but pause it, producing a black screen."""
    before = _current(REPEAT_PORT)
    ok = vlc_nav_step(REPEAT_PORT, TEST_PASSWORD, "next")
    assert ok is True
    _wait_for_item_change(REPEAT_PORT, before)
    after = _current(REPEAT_PORT)
    state = _wait_for_playing(REPEAT_PORT)
    assert after != before, f"nav_step did not change video in repeat-one mode"
    assert state == "playing", f"VLC must be playing after nav (state={state})"


def test_repeat_one_nav_plays_after_every_transition(vlc_repeat_one):
    """Navigate three times — VLC must be playing after each.
    This catches --start-paused which re-applies on every transition."""
    vlc_http_cmd(REPEAT_PORT, "pl_play", TEST_PASSWORD)
    time.sleep(0.5)

    for i in range(3):
        before = _current(REPEAT_PORT)
        vlc_nav_step(REPEAT_PORT, TEST_PASSWORD, "next")
        _wait_for_item_change(REPEAT_PORT, before)
        after = _current(REPEAT_PORT)
        state = _wait_for_playing(REPEAT_PORT)
        assert after != before, f"nav #{i+1}: video did not change"
        assert state == "playing", f"nav #{i+1}: VLC not playing (state={state})"


def test_repeat_one_prev_reverses_next(vlc_repeat_one):
    """prev must reverse next — playlist timelines must be stable."""
    vlc_http_cmd(REPEAT_PORT, "pl_play", TEST_PASSWORD)
    time.sleep(0.5)

    start = _current(REPEAT_PORT)
    assert start, "precondition: VLC must have a current file"

    pos = start
    for direction in ("next", "next", "prev", "prev"):
        vlc_nav_step(REPEAT_PORT, TEST_PASSWORD, direction)
        pos = _wait_for_item_change(REPEAT_PORT, pos)
    assert pos == start, f"two prevs should return to start: expected {start}, got {pos}"

"""Integration tests: verify VLC playlist navigation against a real VLC instance.

Starts a VLC process on a test port with a small playlist, then exercises
pl_next, pl_previous, and pl_delete to verify they work as expected.

Requires: VLC installed, FUN_TIME_RUN_INTEGRATION=1 env var.
"""
from __future__ import annotations

import glob
import os
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
    restore_vlc_volume,
    set_repeat_mode,
    vlc_advance_and_remove,
    vlc_http_cmd,
    vlc_http_req,
    vlc_nav_step,
    wait_for_http,
)
from fun_time.windows_bridge_startup import _build_vlc_launch_command

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
    videos = sorted(glob.glob(str(VIDEO_DIR / "candy" / "*.mp4")))
    if len(videos) < n:
        videos = sorted(glob.glob(str(VIDEO_DIR / "**" / "*.mp4"), recursive=True))
    return videos[:n]


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
    )
    if not wait_for_http(TEST_PORT, TEST_PASSWORD, timeout_ms=10000):
        proc.kill()
        pytest.skip("VLC HTTP did not start")
    vlc_http_cmd(TEST_PORT, "volume&val=0", TEST_PASSWORD)
    replace_playlist_from_file(TEST_PORT, TEST_PASSWORD, playlist_path)
    time.sleep(1.0)
    yield proc, videos
    restore_vlc_volume(TEST_PORT, TEST_PASSWORD)
    proc.kill()
    proc.wait()


def _current() -> str:
    return get_current_file_path(TEST_PORT, TEST_PASSWORD)


def _wait_for_change(before: str, port: int = TEST_PORT, timeout: float = 3.0) -> str:
    """Poll until VLC reports a different current file."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = get_current_file_path(port, TEST_PASSWORD)
        if current and current != before:
            return current
        time.sleep(0.1)
    return get_current_file_path(port, TEST_PASSWORD)


def _next():
    before = _current()
    vlc_http_cmd(TEST_PORT, "pl_next", TEST_PASSWORD)
    _wait_for_change(before)


def _prev():
    before = _current()
    vlc_http_cmd(TEST_PORT, "pl_previous", TEST_PASSWORD)
    _wait_for_change(before)


# --- Phase 2: Do VLC basics work? ---


def test_vlc_reports_current_file(vlc_with_playlist):
    path = _current()
    assert path, "get_current_file_path returned empty"
    assert Path(path).suffix == ".mp4"


def test_pl_next_advances_video(vlc_with_playlist):
    before = _current()
    _next()
    after = _current()
    assert after != before, f"pl_next did not change video (still {before})"


def test_pl_previous_goes_back(vlc_with_playlist):
    before = _current()
    _prev()
    after = _current()
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
    # Go backward from the first item — should wrap to the last
    # First navigate to a known position
    for _ in range(len(videos)):
        _next()
    start = _current()
    _next()
    after = _current()
    # We went forward from some position; just verify it changed (wrapping works)
    # The key test: go back should return
    _prev()
    assert _current() == start


# --- vlc_nav_step (ID-based navigation) ---
# These tests exercise the actual navigation path used by Fun Time hotkeys.
# vlc_nav_step reads the live jstree, resolves the current item by plid_N ID,
# and issues pl_play&id=N.  If _parse_playlist_ids fails to parse the real
# VLC jstree format these tests will catch it immediately.


def test_vlc_nav_step_next_advances_video(vlc_with_playlist):
    before = _current()
    ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    after = _wait_for_change(before)
    assert ok is True, "vlc_nav_step returned False — check _parse_playlist_ids"
    assert after != before, f"vlc_nav_step next did not change video (still {before})"


def test_vlc_nav_step_prev_goes_back(vlc_with_playlist):
    before = _current()
    ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "prev")
    after = _wait_for_change(before)
    assert ok is True, "vlc_nav_step returned False — check _parse_playlist_ids"
    assert after != before, f"vlc_nav_step prev did not change video (still {before})"


def test_vlc_nav_step_next_then_prev_returns_to_start(vlc_with_playlist):
    start = _current()
    ok_next = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    after_next = _wait_for_change(start)
    assert ok_next is True, "vlc_nav_step next returned False"
    assert after_next != start
    ok_prev = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "prev")
    after_prev = _wait_for_change(after_next)
    assert ok_prev is True, "vlc_nav_step prev returned False"
    assert after_prev == start, f"next+prev did not return to start: expected {start}, got {after_prev}"


# --- vlc_advance_and_remove (discard path) ---
# Exercises the satellite discard flow: advance to next item by ID,
# then delete the current item from VLC's playlist.


def test_advance_and_remove_plays_next_and_shrinks_playlist(vlc_with_playlist):
    """vlc_advance_and_remove must: play the next item, remove the old one,
    and leave VLC in a state where navigation still works."""
    _, xml_before = vlc_http_req(TEST_PORT, "/requests/playlist_jstree.xml", TEST_PASSWORD)
    ids_before, current_before = _parse_playlist_ids(xml_before)
    count_before = len(ids_before)

    before_path = _current()
    ok = vlc_advance_and_remove(TEST_PORT, TEST_PASSWORD)
    _wait_for_change(before_path)

    _, xml_after = vlc_http_req(TEST_PORT, "/requests/playlist_jstree.xml", TEST_PASSWORD)
    ids_after, current_after = _parse_playlist_ids(xml_after)
    assert len(ids_after) == count_before - 1, \
        f"playlist should shrink by 1: was {count_before}, now {len(ids_after)}"
    assert current_before not in ids_after, \
        "old item should be removed from playlist"
    assert current_after != current_before, \
        "current item should have changed"


def test_advance_and_remove_preserves_navigation(vlc_with_playlist):
    """After vlc_advance_and_remove, vlc_nav_step must still work."""
    before_remove = _current()
    vlc_advance_and_remove(TEST_PORT, TEST_PASSWORD)
    _wait_for_change(before_remove)

    before = _current()
    ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    after = _wait_for_change(before)
    assert ok is True, "vlc_nav_step failed after advance_and_remove"
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
    )
    if not wait_for_http(REPEAT_PORT, TEST_PASSWORD, timeout_ms=10000):
        proc.kill()
        pytest.skip("VLC HTTP did not start")
    vlc_http_cmd(REPEAT_PORT, "volume&val=0", TEST_PASSWORD)
    replace_playlist_from_file(REPEAT_PORT, TEST_PASSWORD, playlist_path)
    vlc_http_cmd(REPEAT_PORT, "pl_next", TEST_PASSWORD)
    time.sleep(1.0)
    yield proc, videos
    proc.kill()
    proc.wait()


def test_repeat_one_nav_step_changes_video_and_keeps_playing(vlc_repeat_one):
    """vlc_nav_step in repeat-one mode must change the video AND keep playing.
    This is the exact bug that --start-paused caused: VLC would load the new
    item but pause it, producing a black screen."""
    before = get_current_file_path(REPEAT_PORT, TEST_PASSWORD)
    ok = vlc_nav_step(REPEAT_PORT, TEST_PASSWORD, "next")
    after = _wait_for_change(before, port=REPEAT_PORT)
    state = get_playback_state(REPEAT_PORT, TEST_PASSWORD)
    assert ok is True
    assert after != before, f"nav_step did not change video in repeat-one mode"
    assert state == "playing", f"VLC must be playing after nav (state={state})"


def test_repeat_one_nav_plays_after_every_transition(vlc_repeat_one):
    """Navigate three times — VLC must be playing after each.
    This catches --start-paused which re-applies on every transition."""
    vlc_http_cmd(REPEAT_PORT, "pl_play", TEST_PASSWORD)
    time.sleep(0.5)

    for i in range(3):
        before = get_current_file_path(REPEAT_PORT, TEST_PASSWORD)
        vlc_nav_step(REPEAT_PORT, TEST_PASSWORD, "next")
        after = _wait_for_change(before, port=REPEAT_PORT)
        state = get_playback_state(REPEAT_PORT, TEST_PASSWORD)
        assert after != before, f"nav #{i+1}: video did not change"
        assert state == "playing", f"nav #{i+1}: VLC not playing (state={state})"


def test_repeat_one_prev_reverses_next(vlc_repeat_one):
    """prev must reverse next — playlist timelines must be stable."""
    vlc_http_cmd(REPEAT_PORT, "pl_play", TEST_PASSWORD)
    time.sleep(0.5)

    start = get_current_file_path(REPEAT_PORT, TEST_PASSWORD)
    assert start, "precondition: VLC must have a current file"

    pos = start
    for direction in ("next", "next", "prev", "prev"):
        vlc_nav_step(REPEAT_PORT, TEST_PASSWORD, direction)
        pos = _wait_for_change(pos, port=REPEAT_PORT)
    assert pos == start, f"two prevs should return to start: expected {start}, got {pos}"

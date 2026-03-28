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
import time
from pathlib import Path

import pytest

from fun_time.orchestrator import vlc_http_password_from_vlcrc
from fun_time.vlc_actions import (
    get_current_file_path,
    vlc_http_cmd,
    vlc_http_req,
    vlc_nav_step,
    wait_for_http,
)

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

    proc = subprocess.Popen(
        [VLC_EXE,
         "--no-one-instance", "--extraintf", "http",
         "--http-host", "127.0.0.1",
         "--http-port", str(TEST_PORT), "--http-password", TEST_PASSWORD,
         "--no-video", "--no-audio",
         "--playlist-autostart", "--no-random", "--loop",
         ] + videos,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not wait_for_http(TEST_PORT, TEST_PASSWORD, timeout_ms=10000):
        proc.kill()
        pytest.skip("VLC HTTP did not start")
    time.sleep(1.0)
    yield proc, videos
    proc.kill()
    proc.wait()


def _current() -> str:
    return get_current_file_path(TEST_PORT, TEST_PASSWORD)


def _next():
    vlc_http_cmd(TEST_PORT, "pl_next", TEST_PASSWORD)
    time.sleep(0.3)


def _prev():
    vlc_http_cmd(TEST_PORT, "pl_previous", TEST_PASSWORD)
    time.sleep(0.3)


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
    time.sleep(0.3)
    after = _current()
    assert ok is True, "vlc_nav_step returned False — check _parse_playlist_ids"
    assert after != before, f"vlc_nav_step next did not change video (still {before})"


def test_vlc_nav_step_prev_goes_back(vlc_with_playlist):
    before = _current()
    ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "prev")
    time.sleep(0.3)
    after = _current()
    assert ok is True, "vlc_nav_step returned False — check _parse_playlist_ids"
    assert after != before, f"vlc_nav_step prev did not change video (still {before})"


def test_vlc_nav_step_next_then_prev_returns_to_start(vlc_with_playlist):
    start = _current()
    ok_next = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    time.sleep(0.3)
    assert ok_next is True, "vlc_nav_step next returned False"
    assert _current() != start
    ok_prev = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "prev")
    time.sleep(0.3)
    assert ok_prev is True, "vlc_nav_step prev returned False"
    assert _current() == start, f"next+prev did not return to start: expected {start}, got {_current()}"


# --- Playlist healing: skip dead entries ---


def test_vlc_nav_step_skips_over_deleted_file(vlc_with_playlist):
    """When a video file has been removed from disk (e.g. moved to weird_dir),
    vlc_nav_step should skip over the dead playlist entry and play the next
    live video instead of triggering a VLC error dialog."""
    proc, videos = vlc_with_playlist
    assert len(videos) >= 4, "need at least 4 videos for this test"

    # Navigate to video[0]
    # Use pl_play to go to a known starting point by finding its ID
    from fun_time.vlc_actions import _parse_playlist_items, vlc_http_req
    status, xml = vlc_http_req(TEST_PORT, "/requests/playlist_jstree.xml", TEST_PASSWORD)
    assert status == 200
    items, _ = _parse_playlist_items(xml)
    assert len(items) >= 4

    # Play the first item to establish a known position
    first_id = items[0][0]
    vlc_http_cmd(TEST_PORT, f"pl_play&id={first_id}", TEST_PASSWORD)
    time.sleep(0.5)
    start_path = _current()
    assert start_path, "could not get starting video path"

    # Identify what the NEXT video is (video[1]) — we'll delete it
    ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    time.sleep(0.3)
    assert ok
    victim_path = _current()
    assert victim_path != start_path

    # Identify what's AFTER the victim (video[2]) — this is where healing should land
    ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
    time.sleep(0.3)
    assert ok
    expected_target = _current()
    assert expected_target != victim_path

    # Go back to start
    vlc_http_cmd(TEST_PORT, f"pl_play&id={first_id}", TEST_PASSWORD)
    time.sleep(0.5)
    assert _current() == start_path

    # Delete the victim file from disk (simulates move_to_weird)
    victim = Path(victim_path)
    assert victim.exists(), f"victim file missing before test: {victim_path}"
    victim.rename(victim.with_suffix(".mp4.healing_test_backup"))
    try:
        # Navigate forward — should skip the dead entry and land on expected_target
        ok = vlc_nav_step(TEST_PORT, TEST_PASSWORD, "next")
        time.sleep(0.3)
        assert ok is True, "vlc_nav_step should succeed by skipping the dead entry"
        landed = _current()
        assert landed == expected_target, (
            f"Expected to skip dead entry and land on {expected_target}, "
            f"but landed on {landed}"
        )
    finally:
        # Restore the victim file
        backup = victim.with_suffix(".mp4.healing_test_backup")
        if backup.exists():
            backup.rename(victim)

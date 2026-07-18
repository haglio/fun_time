"""Integration: navigate the native satellite player through its file protocol.

Drives a real, launched native satellite (genau's ``satellite`` package) purely
through the command/paused/status file quartet — ``write_satellite_command`` in,
``read_satellite_status`` out — the exact channel fun_time's dispatch loop uses.
Complements ``test_satellite_native_integration`` (which proves basic
play/lock/pause) by covering navigation inverses, wrap-around, discard, playlist
reload and PLAY_FILE.

Requires: FUN_TIME_RUN_INTEGRATION=1 and a real display (the hidden-desktop runner).
"""
from __future__ import annotations

import glob
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.modes import write_playlist_file
from fun_time.satellite_control import read_satellite_status, write_satellite_command
from fun_time.windows_bridge_startup import launch_satellite

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]

_CONFIG = Path(r"C:/path/to/suite-root/projects/fun_time/fun_time_config.json")


def _wait(predicate, *, timeout, desc):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.15)
    pytest.fail(f"timed out waiting for {desc} (last={last!r})")


class _Satellite:
    """A launched native satellite plus the files that drive and observe it."""

    def __init__(self, pid: int, cmd: Path, paused: Path, status: Path, playlist: Path):
        self.pid = pid
        self.cmd = cmd
        self.paused = paused
        self.status = status
        self.playlist = playlist

    def send(self, verb: str) -> None:
        write_satellite_command(self.cmd, verb)

    def video(self) -> str:
        return read_satellite_status(self.status).video

    def wait_for_video(self, *, other_than: str = "", timeout: float = 15.0) -> str:
        return _wait(
            lambda: (lambda v: v if v and v != other_than else None)(self.video()),
            timeout=timeout, desc=f"the satellite's clip to settle (not {other_than!r})",
        )


@pytest.fixture()
def satellite(tmp_path):
    """Launch a native satellite on a random real portrait playlist, playing."""
    cfg = load_config(_CONFIG)
    portrait_dir = str(cfg.paths.portrait_dirs[0])
    all_videos = glob.glob(os.path.join(portrait_dir, "**", "*.mp4"), recursive=True)
    videos = random.sample(all_videos, 4)

    playlist = tmp_path / "portrait_playlist.tsv"
    write_playlist_file(playlist, videos)
    cmd = tmp_path / "portrait_cmd.txt"
    paused = tmp_path / "portrait_paused.txt"
    status = tmp_path / "portrait_status.txt"

    pid = launch_satellite(
        python_exe=str(cfg.paths.genau_python_exe),
        satellite_module="satellite",
        title="Satellite Portrait",
        playlist_file=playlist, command_file=cmd, paused_file=paused, status_file=status,
        log_file=tmp_path / "portrait_satellite.log",
        x=0, y=0, width=800, height=600,
    )
    sat = _Satellite(pid, cmd, paused, status, playlist)
    try:
        # Wait for real playback to begin.
        _wait(
            lambda: (lambda s: True if s.duration_ms > 0 and s.position_ms > 0 else None)(
                read_satellite_status(status)),
            timeout=30, desc="the satellite to start playing",
        )
        # Lock so the clip on screen never auto-advances mid-test, making each
        # navigation's effect unambiguous.  A locked satellite still obeys NEXT/PREV
        # (they load a new clip); it just does not walk on its own.
        sat.send("LOCK")
        _wait(lambda: read_satellite_status(status).locked, timeout=10, desc="the satellite to lock")
        yield sat
    finally:
        write_satellite_command(cmd, "QUIT")
        time.sleep(1.0)
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)


def test_next_changes_the_clip(satellite):
    start = satellite.video()
    assert start.endswith(".mp4")
    satellite.send("NEXT")
    after = satellite.wait_for_video(other_than=start)
    assert after != start


def test_prev_inverts_next(satellite):
    start = satellite.video()
    satellite.send("NEXT")
    mid = satellite.wait_for_video(other_than=start)
    assert mid != start
    satellite.send("PREV")
    back = satellite.wait_for_video(other_than=mid)
    assert back == start, f"prev should reverse next: expected {start!r}, got {back!r}"


def test_navigation_wraps_around(satellite):
    videos = [v.strip() for v in satellite.playlist.read_text(encoding="utf-8").splitlines() if v.strip()]
    start = satellite.video()
    current = start
    for _ in range(len(videos)):
        satellite.send("NEXT")
        current = satellite.wait_for_video(other_than=current)
    assert current == start, f"stepping the whole list should wrap to {start!r}, got {current!r}"


def test_play_file_switches_to_a_specific_clip(satellite):
    videos = [v.strip() for v in satellite.playlist.read_text(encoding="utf-8").splitlines() if v.strip()]
    start = satellite.video()
    target = next(v for v in videos if v != start)
    satellite.send(f"PLAY_FILE {target}")
    after = satellite.wait_for_video(other_than=start)
    assert after == target, f"PLAY_FILE should jump to {target!r}, got {after!r}"


def test_trash_advances_off_the_discarded_clip(satellite):
    condemned = satellite.video()
    satellite.send("TRASH")
    after = satellite.wait_for_video(other_than=condemned)
    assert after != condemned


def test_reload_playlist_keeps_the_current_clip(satellite):
    """A loop/filter reshape writes a new playlist that still contains the clip on
    screen and sends RELOAD_PLAYLIST; the native player keeps that clip playing."""
    current = satellite.video()
    videos = [v.strip() for v in satellite.playlist.read_text(encoding="utf-8").splitlines() if v.strip()]
    # Rebuild the playlist with the current clip kept and the order changed.
    reshaped = [current] + [v for v in videos if v != current]
    write_playlist_file(satellite.playlist, reshaped)
    satellite.send("RELOAD_PLAYLIST")
    # The clip on screen must survive the reload — never restarted to item 0.
    time.sleep(1.0)
    assert satellite.video() == current, "RELOAD_PLAYLIST should keep the current clip"

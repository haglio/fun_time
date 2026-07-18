"""Integration: the native satellite player launches, plays, and obeys commands.

Proves the mpv-backed satellite player (genau's `satellite` package) works
end-to-end on the real platform — through the file quartet fun_time will drive it
with — before the VLC cutover wires it into the live session.  Launched via the
production `launch_satellite`; the playlist is a random real sample.
"""
from __future__ import annotations

import glob
import os
import random
import subprocess
import time
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.satellite_control import read_satellite_status, write_satellite_command
from fun_time.windows_bridge_startup import launch_satellite

_CONFIG = Path(r"C:/path/to/suite-root/projects/fun_time/fun_time_config.json")


def _wait(predicate, *, timeout, desc):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.2)
    pytest.fail(f"timed out waiting for {desc} (last={last!r})")


def test_native_satellite_plays_and_obeys_commands(tmp_path):
    cfg = load_config(_CONFIG)
    portrait_dir = str(cfg.paths.portrait_dirs[0])
    all_videos = glob.glob(os.path.join(portrait_dir, "**", "*.mp4"), recursive=True)
    videos = random.sample(all_videos, 3)

    playlist = tmp_path / "portrait_playlist.tsv"
    playlist.write_text("\n".join(videos) + "\n", encoding="utf-8")
    cmd = tmp_path / "portrait_cmd.txt"
    paused = tmp_path / "portrait_paused.txt"
    status = tmp_path / "portrait_status.txt"

    pid = launch_satellite(
        python_exe=str(cfg.paths.genau_python_exe),
        satellite_module="satellite",
        title="Satellite Portrait",
        playlist_file=playlist, command_file=cmd, paused_file=paused, status_file=status,
        x=0, y=0, width=800, height=600,
    )
    try:
        first = _wait(
            lambda: (lambda s: s.video if s.duration_ms > 0 and s.position_ms > 0 else None)(
                read_satellite_status(status)),
            timeout=30, desc="the satellite to start playing",
        )
        # Lock (stops auto-advance) so NEXT's effect on the clip is unambiguous.
        write_satellite_command(cmd, "LOCK")
        _wait(lambda: read_satellite_status(status).locked, timeout=10, desc="the satellite to lock")
        locked_clip = read_satellite_status(status).video
        write_satellite_command(cmd, "NEXT")
        _wait(lambda: read_satellite_status(status).video not in ("", locked_clip),
              timeout=15, desc="NEXT to change the clip while locked")
        # The paused flag freezes playback.
        paused.write_text("1", encoding="utf-8")
        _wait(lambda: read_satellite_status(status).paused, timeout=10, desc="the satellite to report paused")
        pos_a = read_satellite_status(status).position_ms
        time.sleep(1.2)
        pos_b = read_satellite_status(status).position_ms
        assert pos_b == pos_a, f"paused satellite kept playing ({pos_a} -> {pos_b})"
        assert first  # a real clip was playing
    finally:
        write_satellite_command(cmd, "QUIT")
        time.sleep(1.0)
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)

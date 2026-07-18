"""Integration: the native satellite player launches, plays, obeys commands, and
composites its lock HUD.

Proves the mpv-backed satellite player (this repo's `satellite` package) works
end-to-end on the real platform — through the file quartet fun_time drives it
with, plus the HUD panel fun_time publishes for it to draw.  Launched via the
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
from fun_time.hud_transport import HudPublisher
from fun_time.lock_hud import THUMBNAIL_CACHE_DIRNAME, build_hud_panel
from fun_time.satellite_control import read_satellite_status, write_satellite_command
from fun_time.thumbnail_cache import thumbnail_for
from fun_time.win32 import get_process_creation_time
from fun_time.windows_bridge_startup import launch_satellite, reap_orphaned_satellites

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


def _sample_videos(count: int) -> list[str]:
    cfg = load_config(_CONFIG)
    portrait_dir = str(cfg.paths.portrait_dirs[0])
    return random.sample(
        glob.glob(os.path.join(portrait_dir, "**", "*.mp4"), recursive=True), count)


def test_native_satellite_plays_and_obeys_commands(tmp_path):
    cfg = load_config(_CONFIG)
    videos = _sample_videos(3)

    playlist = tmp_path / "portrait_playlist.tsv"
    playlist.write_text("\n".join(videos) + "\n", encoding="utf-8")
    cmd = tmp_path / "portrait_cmd.txt"
    paused = tmp_path / "portrait_paused.txt"
    status = tmp_path / "portrait_status.txt"

    pid = launch_satellite(
        python_exe=str(cfg.paths.python_exe),
        satellite_module="satellite",
        title="Satellite Portrait",
        playlist_file=playlist, command_file=cmd, paused_file=paused, status_file=status,
        log_file=tmp_path / "portrait_satellite.log",
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


def test_another_sessions_startup_reap_leaves_this_satellite_alone(tmp_path):
    """The startup reap must not be able to leave its own session.

    Every satellite on the machine runs ``-m satellite``, so a reap matched on the
    module alone swept the whole machine: an integration run coming up killed both
    players in the user's live session, with no traceback anywhere because nothing
    had crashed — they were terminated.  Only a real process can prove the
    PowerShell filter actually holds, so this launches one and fires both halves of
    the contract at it: a stranger's reap must spare it, its own must still take it.
    """
    cfg = load_config(_CONFIG)
    videos = _sample_videos(2)

    playlist = tmp_path / "portrait_playlist.tsv"
    playlist.write_text("\n".join(videos) + "\n", encoding="utf-8")
    cmd = tmp_path / "portrait_cmd.txt"
    status = tmp_path / "portrait_status.txt"

    pid = launch_satellite(
        python_exe=str(cfg.paths.python_exe),
        satellite_module="satellite",
        title="Satellite Portrait",
        playlist_file=playlist, command_file=cmd,
        paused_file=tmp_path / "portrait_paused.txt", status_file=status,
        log_file=tmp_path / "portrait_satellite.log",
        x=0, y=0, width=800, height=600,
    )
    try:
        _wait(lambda: read_satellite_status(status).position_ms > 0,
              timeout=30, desc="the satellite to start playing")

        # A session whose state dir is somewhere else entirely comes up.
        elsewhere = tmp_path / "some_other_session"
        reap_orphaned_satellites(
            "satellite",
            [elsewhere / "portrait_status.txt", elsewhere / "landscape_status.txt"],
        )
        time.sleep(2.0)
        assert get_process_creation_time(pid) is not None, (
            "another session's startup reap killed this satellite")

        # ...and the reap still does its own job, on its own files.
        reap_orphaned_satellites("satellite", [status])
        _wait(lambda: get_process_creation_time(pid) is None,
              timeout=10, desc="our own reap to clear a satellite stranded on our files")
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)


def test_the_satellite_composites_the_published_lock_hud(tmp_path):
    """The HUD is drawn INTO the video by mpv, so nothing outside the player can
    read it back — what this proves is that the whole render path survives the
    real platform: real thumbnails, real fonts, a real BGRA overlay handed to a
    real mpv, redrawn when the panel changes, with the clip still advancing after.
    A crash anywhere in it takes the player down and the status file goes stale.

    The panel is built by the production publisher from a production HudPanel, so
    the bytes the player parses are exactly the bytes fun_time writes.
    """
    cfg = load_config(_CONFIG)
    videos = _sample_videos(2)
    cache_dir = tmp_path / THUMBNAIL_CACHE_DIRNAME
    for video in videos:
        thumbnail_for(video, cache_dir)

    playlist = tmp_path / "portrait_playlist.tsv"
    playlist.write_text("\n".join(videos) + "\n", encoding="utf-8")
    cmd = tmp_path / "portrait_cmd.txt"
    paused = tmp_path / "portrait_paused.txt"
    status = tmp_path / "portrait_status.txt"
    hud_file = tmp_path / "portrait_hud.json"
    dashboard_cmd = tmp_path / "dashboard_cmd.txt"

    publisher = HudPublisher({"portrait": hud_file}, cache_dir)

    def publish(locked: bool) -> None:
        publisher.publish("portrait", build_hud_panel(
            "portrait", locked=locked, current=videos[0], index=None,
            filter_query="alpha" if locked else "",
        ))

    publish(locked=False)

    pid = launch_satellite(
        python_exe=str(cfg.paths.python_exe),
        satellite_module="satellite",
        title="Satellite Portrait",
        playlist_file=playlist, command_file=cmd, paused_file=paused, status_file=status,
        hud_file=hud_file, dashboard_cmd_file=dashboard_cmd,
        log_file=tmp_path / "portrait_satellite.log",
        x=0, y=0, width=800, height=600,
    )
    try:
        _wait(
            lambda: read_satellite_status(status).position_ms > 0,
            timeout=30, desc="the satellite to start playing with a HUD",
        )
        # Republish a changed panel: the player must re-render and composite it
        # without disturbing playback.
        publish(locked=True)
        before = read_satellite_status(status).position_ms
        time.sleep(2.0)
        after = read_satellite_status(status).position_ms
        assert after != before, (
            f"the satellite stopped publishing after the HUD redrew ({before} -> {after})")
    finally:
        write_satellite_command(cmd, "QUIT")
        time.sleep(1.0)
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)

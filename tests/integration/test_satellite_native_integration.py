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
import sys
import time
from pathlib import Path

import pytest
from PIL import Image
from player_core.file_channel import append_command
from player_core.player_verbs import LOCK_ON, NEXT, QUIT, SET_PACE, SET_SPEED

from fun_time.config import load_config
from fun_time.filter_vocab import load_camera_words
from fun_time.hud_transport import HudPublisher
from fun_time.lock_hud import SatelliteInputs, build_hud_panel
from fun_time.satellite_control import read_satellite_status
from fun_time.thumbnail_cache import THUMBNAIL_CACHE_DIRNAME, thumbnail_for
from fun_time.win32_process import get_process_creation_time
from fun_time.windows_bridge_startup import launch_satellite, reap_orphaned_satellites
from satellite.contract import SatelliteChannels, WindowPlacement

from .integration_support import (
    checkout_project_dirs,
    end_satellite,
    identify_child,
    published_status,
    real_config_path,
    sample_library_clips,
)

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]


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
    cfg = load_config(real_config_path())
    portrait_dir = str(cfg.paths.portrait_dirs[0])
    return sample_library_clips(
        glob.glob(os.path.join(portrait_dir, "**", "*.mp4"), recursive=True),
        count, desc="portrait clips")


def test_native_satellite_plays_and_obeys_commands(tmp_path):
    cfg = load_config(real_config_path())
    videos = _sample_videos(3)

    playlist = tmp_path / "portrait_playlist.tsv"
    playlist.write_text("\n".join(videos) + "\n", encoding="utf-8")
    cmd = tmp_path / "portrait_cmd.txt"
    paused = tmp_path / "portrait_paused.txt"
    status = tmp_path / "portrait_status.txt"

    pid = launch_satellite(
        python_exe=str(cfg.paths.python_exe),
        satellite_module="satellite",
        channels=SatelliteChannels(
            playlist=playlist,
            command=cmd,
            paused=paused,
            status=status,
            play_points=tmp_path / "portrait_play_points.json"),
        placement=WindowPlacement(x=0, y=0, width=800, height=600,
                                  title="Portrait AI Player"),
        role="Portrait",
        log_file=tmp_path / "portrait_satellite.log",
        # This checkout's siblings, as a session launches them: a player
        # importing an unlanded player_core name dies at import.
        project_dirs=checkout_project_dirs(),
    )
    satellite_process = identify_child(pid)
    try:
        first = _wait(
            lambda: (lambda s: s.video if s.duration_ms > 0 and s.position_ms > 0 else None)(
                read_satellite_status(status)),
            timeout=30, desc="the satellite to start playing",
        )
        # Lock (stops auto-advance) so NEXT's effect on the clip is unambiguous.
        append_command(cmd, LOCK_ON)
        _wait(lambda: read_satellite_status(status).locked, timeout=10, desc="the satellite to lock")
        locked_clip = published_status(read_satellite_status, status).video
        append_command(cmd, NEXT)
        _wait(lambda: read_satellite_status(status).video not in ("", locked_clip),
              timeout=15, desc="NEXT to change the clip while locked")
        append_command(cmd, f"{SET_SPEED} 2")
        _wait(lambda: read_satellite_status(status).speed == 2.0,
              timeout=10, desc="the satellite to report double speed")
        # The paused flag freezes playback.
        paused.write_text("1", encoding="utf-8")
        _wait(lambda: read_satellite_status(status).paused, timeout=10, desc="the satellite to report paused")
        pos_a = published_status(read_satellite_status, status).position_ms
        time.sleep(1.2)
        pos_b = published_status(read_satellite_status, status).position_ms
        assert pos_b == pos_a, f"paused satellite kept playing ({pos_a} -> {pos_b})"
        assert first  # a real clip was playing
    finally:
        append_command(cmd, QUIT)
        time.sleep(1.0)
        end_satellite(satellite_process, tmp_path / "portrait_satellite.log")


def test_another_sessions_startup_reap_leaves_this_satellite_alone(tmp_path):
    """The startup reap must not be able to leave its own session.

    Every satellite on the machine runs ``-m satellite``, so a reap matched on the
    module alone swept the whole machine: an integration run coming up killed both
    players in the user's live session, with no traceback anywhere because nothing
    had crashed — they were terminated.  Only a real process can prove the
    PowerShell filter actually holds, so this launches one and fires both halves of
    the contract at it: a stranger's reap must spare it, its own must still take it.
    """
    cfg = load_config(real_config_path())
    videos = _sample_videos(2)

    playlist = tmp_path / "portrait_playlist.tsv"
    playlist.write_text("\n".join(videos) + "\n", encoding="utf-8")
    cmd = tmp_path / "portrait_cmd.txt"
    status = tmp_path / "portrait_status.txt"

    pid = launch_satellite(
        python_exe=str(cfg.paths.python_exe),
        satellite_module="satellite",
        channels=SatelliteChannels(
            playlist=playlist,
            command=cmd,
            paused=tmp_path / "portrait_paused.txt",
            status=status,
            play_points=tmp_path / "portrait_play_points.json"),
        placement=WindowPlacement(x=0, y=0, width=800, height=600,
                                  title="Portrait AI Player"),
        role="Portrait",
        log_file=tmp_path / "portrait_satellite.log",
        # This checkout's siblings, as a session launches them: a player
        # importing an unlanded player_core name dies at import.
        project_dirs=checkout_project_dirs(),
    )
    satellite_process = identify_child(pid)
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
        end_satellite(satellite_process, tmp_path / "portrait_satellite.log")


def test_the_satellite_composites_the_published_lock_hud(tmp_path):
    """The HUD is drawn INTO the video by mpv, so nothing outside the player can
    read it back — what this proves is that the whole render path survives the
    real platform: real thumbnails, real fonts, a real BGRA overlay handed to a
    real mpv, redrawn when the panel changes, with the clip still advancing after.
    A crash anywhere in it takes the player down and the status file goes stale.

    The panel is built by the production publisher from a production HudPanel, so
    the bytes the player parses are exactly the bytes fun_time writes.
    """
    cfg = load_config(real_config_path())
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

    publisher = HudPublisher({"portrait": hud_file}, cache_dir, load_camera_words())

    def publish(locked: bool) -> None:
        publisher.publish("portrait", build_hud_panel(
            SatelliteInputs(
                "portrait", locked=locked, current=str(videos[0]),
                filter_query="alpha" if locked else "",
            ),
            index=None,
        ))

    publish(locked=False)

    pid = launch_satellite(
        python_exe=str(cfg.paths.python_exe),
        satellite_module="satellite",
        channels=SatelliteChannels(
            playlist=playlist,
            command=cmd,
            paused=paused,
            status=status,
            play_points=tmp_path / "portrait_play_points.json",
            hud=hud_file,
            dashboard_cmd=dashboard_cmd),
        placement=WindowPlacement(x=0, y=0, width=800, height=600,
                                  title="Portrait AI Player"),
        role="Portrait",
        log_file=tmp_path / "portrait_satellite.log",
        # This checkout's siblings, as a session launches them: a player
        # importing an unlanded player_core name dies at import.
        project_dirs=checkout_project_dirs(),
    )
    satellite_process = identify_child(pid)
    try:
        _wait(
            lambda: read_satellite_status(status).position_ms > 0,
            timeout=30, desc="the satellite to start playing with a HUD",
        )
        # Republish a changed panel: the player must re-render and composite it
        # without disturbing playback.
        publish(locked=True)
        before = published_status(read_satellite_status, status).position_ms
        time.sleep(2.0)
        after = published_status(read_satellite_status, status).position_ms
        assert after != before, (
            f"the satellite stopped publishing after the HUD redrew ({before} -> {after})")
    finally:
        append_command(cmd, QUIT)
        time.sleep(1.0)
        end_satellite(satellite_process, tmp_path / "portrait_satellite.log")


def _pictures(folder: Path, count: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    pictures = [folder / f"picture {i}.png" for i in range(count)]
    for i, picture in enumerate(pictures):
        Image.new("RGB", (480, 640), (60 * i % 256, 90, 160)).save(picture)
    return pictures


def test_a_satellite_holds_a_picture_for_the_pace_it_is_sent_and_under_a_lock(tmp_path):
    cfg = load_config(real_config_path())
    playlist = tmp_path / "portrait_playlist.tsv"
    playlist.write_text(
        "".join(f"{picture}\n" for picture in _pictures(tmp_path / "pictures", 3)),
        encoding="utf-8")
    cmd = tmp_path / "portrait_cmd.txt"
    status = tmp_path / "portrait_status.txt"
    append_command(cmd, f"{SET_PACE} 0")

    pid = launch_satellite(
        python_exe=str(cfg.paths.python_exe),
        satellite_module="satellite",
        channels=SatelliteChannels(
            playlist=playlist,
            command=cmd,
            paused=tmp_path / "portrait_paused.txt",
            status=status,
            play_points=tmp_path / "portrait_play_points.json"),
        placement=WindowPlacement(x=0, y=0, width=480, height=640,
                                  title="Portrait AI Player"),
        role="Portrait",
        log_file=tmp_path / "portrait_satellite.log",
        project_dirs=checkout_project_dirs(),
    )
    satellite_process = identify_child(pid)
    try:
        first = _wait(
            lambda: (lambda s: s.video if s.picture else None)(read_satellite_status(status)),
            timeout=30, desc="the satellite to show a picture",
        )
        # Past the four seconds a player opens at, so only the nought can be holding it.
        time.sleep(6.0)
        assert published_status(read_satellite_status, status).video == first, (
            "a picture held at a pace of nought moved on")

        append_command(cmd, f"{SET_PACE} 1")
        _wait(lambda: read_satellite_status(status).video not in ("", first),
              timeout=10, desc="the picture to move on at a one-second pace")

        append_command(cmd, LOCK_ON)
        _wait(lambda: read_satellite_status(status).locked, timeout=10,
              desc="the satellite to lock")
        held = published_status(read_satellite_status, status).video
        time.sleep(3.0)
        assert published_status(read_satellite_status, status).video == held, (
            "a locked picture moved on at a one-second pace")
    finally:
        append_command(cmd, QUIT)
        time.sleep(1.0)
        end_satellite(satellite_process, tmp_path / "portrait_satellite.log")

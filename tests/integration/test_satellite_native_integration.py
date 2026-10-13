"""Integration: the native Satellite Funestra launches, plays, obeys commands, and
composites its lock HUD.

Proves the mpv-backed Satellite Funestra (this repo's `satellite` package) works
end-to-end on the real platform — through the file quartet fun_time drives it
with, plus the HUD panel fun_time publishes for it to draw.  Launched via the
production `launch_satellite`; the playlist is a random real sample.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from funestra.funestra_verbs import LOCK_ON, NEXT, SET_PACE, SET_SPEED, SHOW_FRAME
from PIL import Image

from fun_time.filter_vocab import load_camera_words
from fun_time.hud_transport import HudPublisher
from fun_time.lock_hud import SatelliteInputs, build_hud_panel
from fun_time.satellite_control import read_satellite_status
from fun_time.thumbnail_cache import THUMBNAIL_CACHE_DIRNAME, thumbnail_for
from fun_time.win32_process import get_process_creation_time
from fun_time.windows_bridge_startup import reap_orphaned_satellites

from .integration_support import let_seconds_pass, published_status
from .lone_satellite import LoneSatellite, a_lone_satellite, library_clips_of

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]


def test_native_satellite_plays_and_obeys_commands():
    satellite = a_lone_satellite()
    with satellite.launched(library_clips_of("portrait", 3), width=800, height=600):
        first = satellite.wait_until_playing()
        # Lock (stops auto-advance) so NEXT's effect on the clip is unambiguous.
        satellite.send(LOCK_ON)
        satellite.wait_for(lambda: read_satellite_status(satellite.status).locked,
                           desc="the satellite to lock")
        locked_clip = satellite.video()
        satellite.send(NEXT)
        satellite.wait_for(
            lambda: read_satellite_status(satellite.status).video not in ("", locked_clip),
            desc="NEXT to change the clip while locked")
        satellite.send(f"{SET_SPEED} 2")
        satellite.wait_for(lambda: read_satellite_status(satellite.status).speed == 2.0,
                           desc="the satellite to report double speed")
        # The paused flag freezes playback.
        satellite.paused.write_text("1", encoding="utf-8")
        satellite.wait_for(lambda: read_satellite_status(satellite.status).paused,
                           desc="the satellite to report paused")
        pos_a = published_status(read_satellite_status, satellite.status).position_ms
        let_seconds_pass(1.2)
        pos_b = published_status(read_satellite_status, satellite.status).position_ms
        assert pos_b == pos_a, f"paused satellite kept playing ({pos_a} -> {pos_b})"
        assert first.video  # a real clip was playing


def test_another_sessions_startup_reap_leaves_this_satellite_alone():
    """The startup reap must not be able to leave its own session.

    Every satellite on the machine runs ``-m satellite``, so a reap matched on the
    module alone swept the whole machine: an integration run coming up killed both
    Funestras in the user's live session, with no traceback anywhere because nothing
    had crashed — they were terminated.  Only a real process can prove the
    reap's filter actually holds, so this launches one and fires both halves of
    the contract at it: a stranger's reap must spare it, its own must still take it.
    """
    satellite = a_lone_satellite()
    with satellite.launched(library_clips_of("portrait", 2), width=800, height=600):
        satellite.wait_until_playing()

        # A session whose state dir is somewhere else entirely comes up.
        elsewhere = satellite.folder.parent / "some_other_session"
        reap_orphaned_satellites(
            "satellite",
            [elsewhere / "portrait_status.txt", elsewhere / "landscape_status.txt"],
        )
        let_seconds_pass(2.0)
        assert get_process_creation_time(satellite.pid) is not None, (
            "another session's startup reap killed this satellite")

        # ...and the reap still does its own job, on its own files.
        reap_orphaned_satellites("satellite", [satellite.status])
        satellite.wait_for(lambda: get_process_creation_time(satellite.pid) is None,
                           desc="our own reap to clear a satellite stranded on our files")


def test_the_satellite_composites_the_published_lock_hud(tmp_path):
    """The HUD is drawn INTO the video by mpv, so nothing outside the Funestra can
    read it back — what this proves is that the whole render path survives the
    real platform: real thumbnails, real fonts, a real BGRA overlay handed to a
    real mpv, redrawn when the panel changes, with the clip still advancing after.
    A crash anywhere in it takes the Funestra down and the status file goes stale.

    The panel is built by the production publisher from a production HudPanel, so
    the bytes the Funestra parses are exactly the bytes fun_time writes.
    """
    videos = library_clips_of("portrait", 2)
    cache_dir = tmp_path / THUMBNAIL_CACHE_DIRNAME
    for video in videos:
        thumbnail_for(video, cache_dir)
    satellite = a_lone_satellite()
    publisher = HudPublisher({"portrait": satellite.hud}, cache_dir, load_camera_words())

    def publish(locked: bool) -> None:
        publisher.publish("portrait", build_hud_panel(
            SatelliteInputs(
                "portrait", locked=locked, current=str(videos[0]),
                filter_query="alpha" if locked else "",
            ),
            index=None,
        ))

    publish(locked=False)

    with satellite.launched(videos, width=800, height=600, hud=True):
        satellite.wait_until_playing()
        # Republish a changed panel: the Funestra must re-render and composite it
        # without disturbing playback.
        publish(locked=True)
        before = published_status(read_satellite_status, satellite.status).position_ms
        let_seconds_pass(2.0)
        after = published_status(read_satellite_status, satellite.status).position_ms
        assert after != before, (
            f"the satellite stopped publishing after the HUD redrew ({before} -> {after})")


def _pictures(folder: Path, count: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    pictures = [folder / f"picture {i}.png" for i in range(count)]
    for i, picture in enumerate(pictures):
        Image.new("RGB", (480, 640), (60 * i % 256, 90, 160)).save(picture)
    return pictures


def _the_picture_on_screen(satellite: LoneSatellite) -> str | None:
    status = read_satellite_status(satellite.status)
    return status.video if status.picture else None


def test_a_satellite_holds_a_picture_for_the_pace_it_is_sent_and_under_a_lock():
    satellite = a_lone_satellite()
    pictures = _pictures(satellite.sources / "pictures", 3)
    satellite.send(f"{SET_PACE} 0")

    with satellite.launched(pictures, width=480, height=640):
        first = satellite.wait_for(lambda: _the_picture_on_screen(satellite),
                                   desc="the satellite to show a picture")
        # Past the four seconds a Funestra opens at, so only the nought can be holding it.
        let_seconds_pass(6.0)
        assert satellite.video() == first, "a picture held at a pace of nought moved on"

        satellite.send(f"{SET_PACE} 1")
        satellite.wait_for(lambda: read_satellite_status(satellite.status).video not in ("", first),
                           desc="the picture to move on at a one-second pace")

        satellite.send(LOCK_ON)
        satellite.wait_for(lambda: read_satellite_status(satellite.status).locked,
                           desc="the satellite to lock")
        held = satellite.video()
        let_seconds_pass(3.0)
        assert satellite.video() == held, "a locked picture moved on at a one-second pace"


def test_a_frame_put_up_over_a_picture_moves_on_when_the_picture_would_have():
    satellite = a_lone_satellite()
    pictures = _pictures(satellite.sources / "pictures", 3)
    frames = _pictures(satellite.sources / "frames", 2)
    satellite.send(f"{SET_PACE} 4")

    with satellite.launched(pictures, width=480, height=640):
        first = satellite.wait_for(lambda: _the_picture_on_screen(satellite),
                                   desc="the satellite to show a picture")
        second = satellite.wait_for(
            lambda: (lambda video: video if video not in ("", first) else None)(
                read_satellite_status(satellite.status).video),
            desc="the next picture")
        satellite.send(f"{SHOW_FRAME} {frames[0]}")
        time.sleep(1.0)
        satellite.send(f"{SHOW_FRAME} {frames[1]}")
        time.sleep(0.5)
        assert satellite.video() == second, (
            "a picture with frames put up over it moved on at the first frame")

        satellite.wait_for(lambda: read_satellite_status(satellite.status).video not in ("", second),
                           desc="the picture under the frames to move on at its pace")

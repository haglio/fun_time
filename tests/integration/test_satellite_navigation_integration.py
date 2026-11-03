from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from funestra.funestra_verbs import LOCK_ON, play_file
from funestra.playlist import read_playlist

from fun_time.bridge_records import BridgeConfig
from fun_time.command_dispatch import dispatch_command
from fun_time.filter_vocab import load_camera_words
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.hud_transport import HudPublisher
from fun_time.latest_arrivals import LatestArrivals
from fun_time.lock_hud import SatelliteHud
from fun_time.modes import write_playlist_file
from fun_time.satellite_control import read_satellite_status
from fun_time.shared_state import BridgeState, SatelliteState
from fun_time.thumbnail_cache import THUMBNAIL_CACHE_DIRNAME

from .integration_support import published_status, video_of_a_test_pattern
from .lone_satellite import LoneSatellite, a_lone_satellite, library_videos_of
from .run_clock import Budget

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]


@pytest.fixture
def satellite():
    lone = a_lone_satellite()
    with lone.launched(library_videos_of("portrait", 4), width=800, height=600, hud=True):
        lone.wait_until_playing()
        lone.send(LOCK_ON)
        lone.wait_for(lambda: read_satellite_status(lone.status).locked,
                      desc="the satellite to lock")
        yield lone


def test_next_changes_the_video(satellite):
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
    start = satellite.video()
    current = start
    for _ in range(len(read_playlist(satellite.playlist))):
        satellite.send("NEXT")
        current = satellite.wait_for_video(other_than=current)
    assert current == start, f"stepping the whole list should wrap to {start!r}, got {current!r}"


def test_play_file_switches_to_a_specific_video(satellite):
    start = satellite.video()
    target = next(item for item in read_playlist(satellite.playlist)
                  if str(item.path) != start)

    satellite.send(play_file(target))

    after = satellite.wait_for_video(other_than=start)
    assert after == str(target.path), (
        f"PLAY_FILE should jump to {target.path}, got {after!r}")


def test_trash_advances_off_the_discarded_video(satellite):
    condemned = satellite.video()
    satellite.send("TRASH")
    after = satellite.wait_for_video(other_than=condemned)
    assert after != condemned


def test_reload_playlist_keeps_the_current_video(satellite):
    current = satellite.video()
    videos = [v.strip() for v in satellite.playlist.read_text(encoding="utf-8").splitlines() if v.strip()]
    reshaped = [current] + [v for v in videos if v != current]
    write_playlist_file(satellite.playlist, reshaped)
    satellite.send("RELOAD_PLAYLIST")
    time.sleep(1.0)
    assert satellite.video() == current, "RELOAD_PLAYLIST should keep the current clip"


def _playlist_videos(satellite: LoneSatellite) -> list[str]:
    return [str(item.path) for item in read_playlist(satellite.playlist)]


def _bridge_config(satellite: LoneSatellite) -> BridgeConfig:
    state_dir = satellite.folder
    runtime = state_dir.parent
    return BridgeConfig(
        portrait_cmd_file=satellite.cmd,
        portrait_paused_file=satellite.paused,
        portrait_status_file=satellite.status,
        portrait_playlist_file=satellite.playlist,
        landscape_cmd_file=state_dir / "landscape_cmd.txt",
        landscape_paused_file=state_dir / "landscape_paused.txt",
        landscape_status_file=state_dir / "landscape_status.txt",
        landscape_playlist_file=state_dir / "landscape_playlist.tsv",
        trash_dir=runtime / "trash",
        state_dir=state_dir,
        main_sources=str(runtime / "primary"),
        portrait_sources=str(satellite.sources),
        landscape_sources=str(runtime / "landscape"),
        broker_mode_file=state_dir / "broker_mode.txt",
        genau_cmd_file=state_dir / "genau_cmd.txt",
        genau_paused_file=state_dir / "genau_paused.txt",
        genau_status_file=state_dir / "genau_status.txt",
        audio_paused_file=state_dir / "audio_paused.txt",
        audio_volume_file=state_dir / "audio_volume.txt",
        main_funestra_cmd_file=state_dir / "main_player_cmd.txt",
        main_funestra_paused_file=state_dir / "main_player_paused.txt",
        main_funestra_status_file=state_dir / "main_player_status.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
    )


def _drained(satellite: LoneSatellite) -> None:
    def empty() -> bool:
        try:
            return not satellite.cmd.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return True
        except PermissionError:
            return False

    satellite.wait_for(empty, desc="the Funestra to drain its command file")


def test_latest_puts_the_newest_video_on_screen(satellite):
    made: list[str] = []
    for i in range(4):
        video = video_of_a_test_pattern(satellite.sources / f"clip{i}.mp4", size="240x426",
                                      seconds=3)
        os.utime(video, (1000 + i * 1000, 1000 + i * 1000))
        made.append(str(video))
    oldest, newest = made[0], made[-1]

    satellite.send(f"PLAY_FILE {oldest}")
    satellite.wait_for(lambda: Path(satellite.video()) == Path(oldest), desc="the oldest clip")

    dispatch_command("portrait_latest", BridgeState(), _bridge_config(satellite))

    _drained(satellite)
    satellite.wait_for(lambda: Path(satellite.video()) == Path(newest),
                       desc="the newest clip to come up")


def test_a_video_that_arrives_under_latest_comes_up_after_the_one_on_screen(satellite):
    video_of_a_test_pattern(satellite.sources / "already there.mp4", size="240x426", seconds=3)
    arrivals = LatestArrivals(_bridge_config(satellite))
    arrivals.look()
    arrived = video_of_a_test_pattern(satellite.sources / "arrived.mp4", size="240x426",
                                     seconds=3)
    arrivals.look()
    on_screen = satellite.video()

    arrivals.deliver(BridgeState().with_satellite(FunTimeFunestra.PORTRAIT, latest=True))
    _drained(satellite)
    still_up = satellite.video()
    satellite.send("NEXT")

    satellite.wait_for(lambda: Path(satellite.video()) == arrived, desc="the clip that came in")
    assert still_up == on_screen


def test_no_loop_keeps_the_video_on_screen_playing(satellite):
    config = _bridge_config(satellite)
    playing = satellite.video()
    browse = [v for v in _playlist_videos(satellite) if v != playing]
    assert playing not in browse

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_no_loop", BridgeState(portrait=SatelliteState(loop="seed")), config)

    _drained(satellite)
    time.sleep(0.5)
    assert satellite.video() == playing, "loop off must never switch the clip on screen"


def test_next_leaves_a_loop_down_to_one_video_for_another_video(satellite):
    config = _bridge_config(satellite)
    playing = satellite.video()
    browse = [v for v in _playlist_videos(satellite) if v != playing]
    write_playlist_file(satellite.playlist, [playing])
    satellite.send("RELOAD_PLAYLIST")
    satellite.wait_for(lambda: read_satellite_status(satellite.status).playlist_length == 1,
                       desc="the loop to hold only the clip on screen")

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_next", BridgeState(portrait=SatelliteState(loop="seed", locked=True)), config)

    assert satellite.wait_for_video(other_than=playing) in browse


def _decoding(satellite: LoneSatellite) -> bool:
    status = published_status(read_satellite_status, satellite.status)
    return status.duration_ms > 0 and status.position_ms > 0


def test_more_seeds_leaves_the_funestra_decoding(tmp_path):
    videos = library_videos_of("landscape", 8)
    satellite = a_lone_satellite()
    with satellite.launched(videos[:3], width=1706, height=1410, hud=True):
        satellite.wait_until_playing()
        satellite.send(LOCK_ON)
        satellite.wait_for(lambda: read_satellite_status(satellite.status).locked,
                           desc="the satellite to lock")
        config = _bridge_config(satellite)
        playing = satellite.video()
        others = [v for v in videos if v != playing]
        family, widened = [playing, others[0]], [playing, *others]

        # The HUD is up throughout, as in a real session: widening is the one
        # gesture that grows the HUD — a two-cell seed row becomes eight — so the
        # Funestra re-renders it at a new size and hands mpv a differently-shaped
        # overlay while the video under it keeps decoding.
        publisher = HudPublisher({"portrait": satellite.hud},
                                 tmp_path / THUMBNAIL_CACHE_DIRNAME, load_camera_words())

        def publish(row: list[str], loop: str) -> None:
            publisher.publish("portrait", SatelliteHud(
                funestra="portrait", locked=False, title="Looping seeds", current=playing,
                seed_siblings=[v for v in row if v != playing], action_siblings=[],
                seed_count=len(row), active_loop=loop, playing=playing,
            ))

        state = BridgeState(portrait=SatelliteState(loop="", locked=True))
        with patch("fun_time.satellite_groups.seed_family_items", return_value=family), \
                patch("fun_time.satellite_groups.widened_seed_items", return_value=widened):
            state, _ = dispatch_command("portrait_loop", state, config)
            publish(family, "seed")
            _drained(satellite)
            assert _decoding(satellite), "the seed loop's own reload stopped the video"
            state, _ = dispatch_command("portrait_more_seeds", state, config)
            publish(widened, "seed")

        _drained(satellite)
        assert satellite.video() == playing, "widening must never switch the clip on screen"
        assert _decoding(satellite), "the Funestra went black when the seed row widened"
        # Then let the widened loop run through several video boundaries: the reload
        # lands wherever the click happened to fall, so what has to hold is that the
        # video output survives every auto-advance the reshaped queue then makes.
        #
        # A video mpv had not finished prefetching cold-opens at the boundary and
        # reads 0/0 for a beat, so what fails this is a blank that OUTLASTS any
        # transition — the Funestra having lost its file for good, which is the black
        # window that never comes back.
        watching = Budget(60)
        seen: set[str] = set()
        blank = None
        while not watching.expired():
            status = read_satellite_status(satellite.status)
            if status.duration_ms > 0 and status.position_ms > 0:
                blank = None
                seen.add(status.video)
            else:
                blank = blank or Budget(8.0)
                assert not blank.expired(), (
                    f"the Funestra stopped decoding for good, {len(seen)} clip(s) into the "
                    f"widened loop\n{satellite.what_it_last_said()}")
            time.sleep(0.25)
        assert len(seen) > 1, f"the loop never advanced, so nothing was proven (saw {len(seen)})"

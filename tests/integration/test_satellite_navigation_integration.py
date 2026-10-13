"""Integration: navigate the native Satellite Funestra through its file protocol.

Drives a real, launched native satellite (this repo's ``satellite`` package) purely
through the command/paused/status file quartet — ``append_command`` in,
``read_satellite_status`` out — the exact channel fun_time's dispatch loop uses.
Complements ``test_satellite_native_integration`` (which proves basic
play/lock/pause) by covering navigation inverses, wrap-around, discard, playlist
reload and PLAY_FILE.

Requires: FUN_TIME_RUN_INTEGRATION=1 and a real display (the hidden-desktop runner).
"""
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
from fun_time.hud_transport import HudPublisher
from fun_time.lock_hud import HudPanel
from fun_time.modes import write_playlist_file
from fun_time.satellite_control import read_satellite_status
from fun_time.shared_state import BridgeState, SatelliteState
from fun_time.thumbnail_cache import THUMBNAIL_CACHE_DIRNAME

from .integration_support import published_status
from .lone_satellite import LoneSatellite, a_lone_satellite, library_clips_of
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
    with lone.launched(library_clips_of("portrait", 4), width=800, height=600, hud=True):
        lone.wait_until_playing()
        # Lock so the clip on screen never auto-advances mid-test, making each
        # navigation's effect unambiguous.  A locked satellite still obeys NEXT/PREV
        # (they load a new clip); it just does not walk on its own.
        lone.send(LOCK_ON)
        lone.wait_for(lambda: read_satellite_status(lone.status).locked,
                      desc="the satellite to lock")
        yield lone


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
    start = satellite.video()
    current = start
    for _ in range(len(read_playlist(satellite.playlist))):
        satellite.send("NEXT")
        current = satellite.wait_for_video(other_than=current)
    assert current == start, f"stepping the whole list should wrap to {start!r}, got {current!r}"


def test_play_file_switches_to_a_specific_clip(satellite):
    """A line of the playlist is a video and, where it has one, a TAB and its
    funscript, and ``PLAY_FILE`` carries that whole line -- so what the Funestra
    then reports playing is the video half of it."""
    start = satellite.video()
    target = next(item for item in read_playlist(satellite.playlist)
                  if str(item.path) != start)

    satellite.send(play_file(target))

    after = satellite.wait_for_video(other_than=start)
    assert after == str(target.path), (
        f"PLAY_FILE should jump to {target.path}, got {after!r}")


def test_trash_advances_off_the_discarded_clip(satellite):
    condemned = satellite.video()
    satellite.send("TRASH")
    after = satellite.wait_for_video(other_than=condemned)
    assert after != condemned


def test_reload_playlist_keeps_the_current_clip(satellite):
    """A loop/filter reshape writes a new playlist that still contains the clip on
    screen and sends RELOAD_PLAYLIST; the native Funestra keeps that clip playing."""
    current = satellite.video()
    videos = [v.strip() for v in satellite.playlist.read_text(encoding="utf-8").splitlines() if v.strip()]
    # Rebuild the playlist with the current clip kept and the order changed.
    reshaped = [current] + [v for v in videos if v != current]
    write_playlist_file(satellite.playlist, reshaped)
    satellite.send("RELOAD_PLAYLIST")
    # The clip on screen must survive the reload — never restarted to item 0.
    time.sleep(1.0)
    assert satellite.video() == current, "RELOAD_PLAYLIST should keep the current clip"


def _playlist_videos(satellite: LoneSatellite) -> list[str]:
    return [str(item.path) for item in read_playlist(satellite.playlist)]


def _bridge_config(satellite: LoneSatellite) -> BridgeConfig:
    """A BridgeConfig whose portrait side drives this launched satellite.

    Only the portrait quartet has to be real — the loop commands touch just the
    satellite they address — so every other file points somewhere beside it.
    """
    # The state dir is where a rebuild writes the side's playlist, and the satellite
    # is already reading portrait_playlist.tsv there — the same name — so they have
    # to be the same directory or the rebuild would land beside the running Funestra.
    state_dir = satellite.folder
    runtime = state_dir.parent
    favs = state_dir / "favs.csv"
    favs.write_text("local_file,web_url\n", encoding="utf-8")
    return BridgeConfig(
        portrait_cmd_file=satellite.cmd,
        portrait_paused_file=satellite.paused,
        portrait_status_file=satellite.status,
        portrait_playlist_file=satellite.playlist,
        landscape_cmd_file=state_dir / "landscape_cmd.txt",
        landscape_paused_file=state_dir / "landscape_paused.txt",
        landscape_status_file=state_dir / "landscape_status.txt",
        landscape_playlist_file=state_dir / "landscape_playlist.tsv",
        favs_file=favs,
        weird_dir=runtime / "weird",
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
    """Block until the Funestra has consumed everything on its command file.

    No file at all is the drained state, and the usual one: the Funestra claims the
    queue by renaming it aside and deletes what it read
    (``funestra.file_channel.consume_command_file``), so a fully drained queue
    leaves nothing.  Reading it outright raised FileNotFoundError from
    inside the wait — a drain looked like a hang.

    A read caught DURING that rename is denied outright by Windows, which is
    not an answer either way — the queue is mid-claim, so the honest reading is
    "not drained yet" and the next poll is milliseconds away.  Left to raise, it
    took the whole run down with a PermissionError from inside the wait.
    """
    def empty() -> bool:
        try:
            return not satellite.cmd.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return True
        except PermissionError:
            return False  # claimed this instant; ask again next poll

    satellite.wait_for(empty, desc="the Funestra to drain its command file")


def test_latest_puts_the_newest_clip_on_screen(satellite):
    """The user-visible contract of "portrait latest": the newest arrival is what
    comes up.

    Reordering the queue is not enough — the reload keeps the clip on screen playing
    while it survives the new list, so the newest-first order applied only *after*
    it and the top of the list was never reached.  This drives the production
    dispatch over a small real source tree with mtimes we set, so "newest" is a fact
    of the filesystem rather than of a stub.
    """
    linked: list[str] = []
    for i, video in enumerate(_playlist_videos(satellite)):
        dest = satellite.sources / f"clip{i}.mp4"
        os.link(video, dest)  # the same bytes under a path whose mtime is ours
        os.utime(dest, (1000 + i * 1000, 1000 + i * 1000))
        linked.append(str(dest))
    oldest, newest = linked[0], linked[-1]

    # Start on the oldest, which the rebuilt list still holds: without the jump to
    # the head, the reload would simply keep playing it.
    satellite.send(f"PLAY_FILE {oldest}")
    satellite.wait_for(lambda: Path(satellite.video()) == Path(oldest), desc="the oldest clip")

    dispatch_command("portrait_latest", BridgeState(), _bridge_config(satellite))

    _drained(satellite)
    satellite.wait_for(lambda: Path(satellite.video()) == Path(newest),
                       desc="the newest clip to come up")


def test_no_loop_keeps_the_clip_on_screen_playing(satellite):
    """Turning a loop OFF must leave the clip on screen alone, changing only what
    comes up next — the whole contract of the loop toggle.

    The interesting case is the real one: a loop cycles a group whose items are
    NOT in the browse it returns to (the browse holds one clip per group), and a
    Funestra whose clip is missing from a reloaded playlist restarts at the top.  So
    this drives the production ``portrait_no_loop`` against a real Funestra with
    exactly that shape of browse — stubbing only where the browse's *contents* come
    from, since the real library might happen to include the clip on screen and
    then never exercise the case at all.
    """
    config = _bridge_config(satellite)
    playing = satellite.video()
    browse = [v for v in _playlist_videos(satellite) if v != playing]
    assert playing not in browse

    with patch("fun_time.satellite_groups.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_no_loop", BridgeState(portrait=SatelliteState(loop="seed")), config)

    _drained(satellite)
    time.sleep(0.5)
    assert satellite.video() == playing, "loop off must never switch the clip on screen"


def test_next_leaves_a_loop_down_to_one_clip_for_another_clip(satellite):
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
    """Whether the Funestra still has a decoded clip on screen.

    ``duration_ms`` and ``position_ms`` are mpv's own ``duration`` / ``time_pos``,
    which are None — published as 0 — the moment mpv has no file open.  A Funestra
    showing a black window reads exactly that way, so this is what tells a clip
    still being drawn from an empty video output.
    """
    status = published_status(read_satellite_status, satellite.status)
    return status.duration_ms > 0 and status.position_ms > 0


def test_more_seeds_leaves_the_funestra_decoding(tmp_path):
    """"More seeds" reshapes a seed loop's playlist a second time, seconds after
    the loop itself reshaped it — and the Funestra must still be drawing its clip.

    This is the sequence off the HUD's expand button: lock a clip, start its seed
    loop (one playlist rewrite + RELOAD_PLAYLIST), then widen the net (another).
    Only the widening pool is stubbed, so which clips come back is fixed while
    every reshape, verb and reload is the production one — the point is what two
    reloads in a row do to a real mpv, not what the library ranks.

    On the landscape library at a landscape slot's size, because that is where the
    load is: those clips are the upscaled 4K ones, prefetch has mpv opening the
    next while the current still decodes, and widening is exactly what puts a
    satellite into a tight loop over a handful of them.
    """
    videos = library_clips_of("landscape", 8)
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
        # gesture that grows the panel — a two-cell seed row becomes eight — so the
        # Funestra re-renders it at a new size and hands mpv a differently-shaped
        # overlay while the video under it keeps decoding.
        publisher = HudPublisher({"portrait": satellite.hud},
                                 tmp_path / THUMBNAIL_CACHE_DIRNAME, load_camera_words())

        def publish(row: list[str], loop: str) -> None:
            publisher.publish("portrait", HudPanel(
                funestra="portrait", locked=False, lock_label="Looping seeds", current=playing,
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
        # Then let the widened loop run through several clip boundaries: the reload
        # lands wherever the click happened to fall, so what has to hold is that the
        # video output survives every auto-advance the reshaped queue then makes.
        #
        # A clip mpv had not finished prefetching cold-opens at the boundary and
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

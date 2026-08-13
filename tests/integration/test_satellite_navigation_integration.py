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
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.command_dispatch import BridgeConfig, BridgeState, dispatch_command
from fun_time.config import load_config
from fun_time.hud_transport import HudPublisher
from fun_time.lock_hud import HudPanel
from fun_time.modes import write_playlist_file
from fun_time.thumbnail_cache import THUMBNAIL_CACHE_DIRNAME
from fun_time.satellite_control import read_satellite_status, write_satellite_command
from fun_time.windows_bridge_startup import launch_satellite

from .integration_support import real_config_path

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
    pytest.mark.skipif(
        os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
        reason="Set FUN_TIME_RUN_INTEGRATION=1 to run",
    ),
]



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

    def __init__(self, pid: int, cmd: Path, paused: Path, status: Path, playlist: Path,
                 hud: Path, log: Path):
        self.pid = pid
        self.cmd = cmd
        self.paused = paused
        self.status = status
        self.playlist = playlist
        self.hud = hud
        self.log = log

    def send(self, verb: str) -> None:
        write_satellite_command(self.cmd, verb)

    def log_tail(self, lines: int = 25) -> str:
        """The end of the player's own log, for a failure message.

        It carries mpv's warnings and errors as well as the player's own lines, so
        a player that stopped drawing says here why it stopped — the answer is
        never anywhere else, and the temp dir is gone by the time anyone looks.
        """
        try:
            text = self.log.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            return f"(no log: {err})"
        return "\n".join(text.splitlines()[-lines:])

    def video(self) -> str:
        return read_satellite_status(self.status).video

    def wait_for_video(self, *, other_than: str = "", timeout: float = 15.0) -> str:
        return _wait(
            lambda: (lambda v: v if v and v != other_than else None)(self.video()),
            timeout=timeout, desc=f"the satellite's clip to settle (not {other_than!r})",
        )


def library_videos(which: str, count: int) -> list[str]:
    """*count* random clips out of a side's real library."""
    paths = getattr(load_config(real_config_path()).paths, f"{which}_dirs")
    return random.sample(
        glob.glob(os.path.join(str(paths[0]), "**", "*.mp4"), recursive=True), count)


@contextmanager
def launched(tmp_path: Path, videos: list[str], *, width: int, height: int):
    """A native satellite playing *videos*, on the portrait quartet, taken down after.

    The window size is the caller's because it decides what mpv actually decodes:
    a satellite's real slot is the whole monitor, and the clips filling it are the
    library's biggest.
    """
    playlist = tmp_path / "portrait_playlist.tsv"
    write_playlist_file(playlist, videos)
    cmd = tmp_path / "portrait_cmd.txt"
    paused = tmp_path / "portrait_paused.txt"
    status = tmp_path / "portrait_status.txt"
    hud = tmp_path / "portrait_hud.json"
    log = tmp_path / "portrait_satellite.log"

    pid = launch_satellite(
        python_exe=str(load_config(real_config_path()).paths.python_exe),
        satellite_module="satellite",
        title="Portrait AI Player",
        playlist_file=playlist, command_file=cmd, paused_file=paused, status_file=status,
        hud_file=hud, dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
        log_file=log,
        x=0, y=0, width=width, height=height,
    )
    sat = _Satellite(pid, cmd, paused, status, playlist, hud, log)
    try:
        _wait(
            lambda: (lambda s: True if s.duration_ms > 0 and s.position_ms > 0 else None)(
                read_satellite_status(status)),
            timeout=30, desc="the satellite to start playing",
        )
        yield sat
    finally:
        write_satellite_command(cmd, "QUIT")
        time.sleep(1.0)
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)


@pytest.fixture()
def satellite(tmp_path):
    """Launch a native satellite on a random real portrait playlist, playing."""
    with launched(tmp_path, library_videos("portrait", 4), width=800, height=600) as sat:
        # Lock so the clip on screen never auto-advances mid-test, making each
        # navigation's effect unambiguous.  A locked satellite still obeys NEXT/PREV
        # (they load a new clip); it just does not walk on its own.
        sat.send("LOCK")
        _wait(lambda: read_satellite_status(sat.status).locked, timeout=10,
              desc="the satellite to lock")
        yield sat


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


def _playlist_videos(satellite: _Satellite) -> list[str]:
    return [v.strip() for v in satellite.playlist.read_text(encoding="utf-8").splitlines() if v.strip()]


def _bridge_config(satellite: _Satellite, tmp_path: Path) -> BridgeConfig:
    """A BridgeConfig whose portrait side drives this launched satellite.

    Only the portrait quartet has to be real — the loop commands touch just the
    satellite they address — so every other file points somewhere under tmp_path.
    """
    # The state dir is where a rebuild writes the side's playlist, and the satellite
    # is already reading tmp_path/portrait_playlist.tsv — the same name — so they have
    # to be the same directory or the rebuild would land beside the running player.
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    favs = tmp_path / "favs.csv"
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
        weird_dir=tmp_path / "weird",
        state_dir=state_dir,
        main_sources=str(tmp_path / "primary"),
        portrait_sources=str(tmp_path / "portrait"),
        landscape_sources=str(tmp_path / "landscape"),
        genau_mode_file=state_dir / "genau_mode.txt",
        genau_cmd_file=state_dir / "genau_cmd.txt",
        genau_paused_file=state_dir / "genau_paused.txt",
        audio_paused_file=state_dir / "audio_paused.txt",
        audio_volume_file=state_dir / "audio_volume.txt",
        nau_cmd_file=state_dir / "nau_cmd.txt",
        nau_paused_file=state_dir / "nau_paused.txt",
        nau_status_file=state_dir / "nau_status.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
    )


def _drained(satellite: _Satellite) -> None:
    """Block until the player has consumed everything on its command file."""
    _wait(
        lambda: not satellite.cmd.read_text(encoding="utf-8").strip(),
        timeout=10, desc="the player to drain its command file",
    )


def test_latest_puts_the_newest_clip_on_screen(satellite, tmp_path):
    """The user-visible contract of "portrait latest": the newest arrival is what
    comes up.

    Reordering the queue is not enough — the reload keeps the clip on screen playing
    while it survives the new list, so the newest-first order applied only *behind*
    it and the top of the list was never reached.  This drives the production
    dispatch over a small real source tree with mtimes we set, so "newest" is a fact
    of the filesystem rather than of a stub.
    """
    source = tmp_path / "sources"
    source.mkdir()
    linked: list[str] = []
    for i, video in enumerate(_playlist_videos(satellite)):
        dest = source / f"clip{i}.mp4"
        os.link(video, dest)  # the same bytes under a path whose mtime is ours
        os.utime(dest, (1000 + i * 1000, 1000 + i * 1000))
        linked.append(str(dest))
    oldest, newest = linked[0], linked[-1]

    # Start on the oldest, which the rebuilt list still holds: without the jump to
    # the head, the reload would simply keep playing it.
    satellite.send(f"PLAY_FILE {oldest}")
    _wait(lambda: Path(satellite.video()) == Path(oldest), timeout=15, desc="the oldest clip")

    config = replace(_bridge_config(satellite, tmp_path), portrait_sources=str(source))
    dispatch_command("portrait_latest", BridgeState(), config)

    _drained(satellite)
    _wait(lambda: Path(satellite.video()) == Path(newest),
          timeout=15, desc="the newest clip to come up")


def test_no_loop_keeps_the_clip_on_screen_playing(satellite, tmp_path):
    """Turning a loop OFF must leave the clip on screen alone, changing only what
    comes up next — the whole contract of the loop toggle.

    The interesting case is the real one: a loop cycles a group whose members are
    NOT in the browse it returns to (the browse holds one clip per group), and a
    player whose clip is missing from a reloaded playlist restarts at the top.  So
    this drives the production ``portrait_no_loop`` against a real player with
    exactly that shape of browse — stubbing only where the browse's *contents* come
    from, since the real library might happen to include the clip on screen and
    then never exercise the case at all.
    """
    config = _bridge_config(satellite, tmp_path)
    playing = satellite.video()
    browse = [v for v in _playlist_videos(satellite) if v != playing]
    assert playing not in browse

    with patch("fun_time.command_dispatch.satellite_browse_paths", return_value=browse):
        dispatch_command("portrait_no_loop", BridgeState(portrait_loop="seed"), config)

    _drained(satellite)
    time.sleep(0.5)
    assert satellite.video() == playing, "loop off must never switch the clip on screen"


def _decoding(satellite: _Satellite) -> bool:
    """Whether the player still has a decoded clip on screen.

    ``duration_ms`` and ``position_ms`` are mpv's own ``duration`` / ``time_pos``,
    which are None — published as 0 — the moment mpv has no file open.  A player
    showing a black window reads exactly that way, so this is what tells a clip
    still being drawn from an empty video output.
    """
    status = read_satellite_status(satellite.status)
    return status.duration_ms > 0 and status.position_ms > 0


def test_more_seeds_leaves_the_player_decoding(tmp_path):
    """"More seeds" reshapes a seed loop's playlist a second time, seconds after
    the loop itself reshaped it — and the player must still be drawing its clip.

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
    videos = library_videos("landscape", 8)
    with launched(tmp_path, videos[:3], width=1706, height=1410) as satellite:
        satellite.send("LOCK")
        _wait(lambda: read_satellite_status(satellite.status).locked, timeout=10,
              desc="the satellite to lock")
        config = _bridge_config(satellite, tmp_path)
        playing = satellite.video()
        others = [v for v in videos if v != playing]
        family, widened = [playing, others[0]], [playing, *others]

        # The HUD is up throughout, as in a real session: widening is the one
        # gesture that grows the panel — a two-cell seed row becomes eight — so the
        # player re-renders it at a new size and hands mpv a differently-shaped
        # overlay while the video under it keeps decoding.
        publisher = HudPublisher({"portrait": satellite.hud},
                                 tmp_path / THUMBNAIL_CACHE_DIRNAME)

        def publish(row: list[str], loop: str) -> None:
            publisher.publish("portrait", HudPanel(
                side="portrait", locked=False, lock_label="Looping seeds", current=playing,
                seed_siblings=[v for v in row if v != playing], action_siblings=[],
                seed_count=len(row), active_loop=loop, playing=playing,
            ))

        state = BridgeState(portrait_loop="", locked2=True)
        with patch("fun_time.command_dispatch.seed_family_members", return_value=family), \
                patch("fun_time.command_dispatch.widened_seed_members", return_value=widened):
            state, _ = dispatch_command("portrait_loop", state, config)
            publish(family, "seed")
            _drained(satellite)
            assert _decoding(satellite), "the seed loop's own reload stopped the video"
            state, _ = dispatch_command("portrait_more_seeds", state, config)
            publish(widened, "seed")

        _drained(satellite)
        assert satellite.video() == playing, "widening must never switch the clip on screen"
        assert _decoding(satellite), "the player went black when the seed row widened"
        # Then let the widened loop run through several clip boundaries: the reload
        # lands wherever the click happened to fall, so what has to hold is that the
        # video output survives every auto-advance the reshaped queue then makes.
        #
        # A clip mpv had not finished prefetching cold-opens at the boundary and
        # reads 0/0 for a beat, so what fails this is a blank that OUTLASTS any
        # transition — the player having lost its file for good, which is the black
        # window that never comes back.
        deadline = time.monotonic() + 60
        seen: set[str] = set()
        blank_since = None
        while time.monotonic() < deadline:
            status = read_satellite_status(satellite.status)
            if status.duration_ms > 0 and status.position_ms > 0:
                blank_since = None
                seen.add(status.video)
            else:
                blank_since = blank_since or time.monotonic()
                assert time.monotonic() - blank_since < 8.0, (
                    f"the player stopped decoding for good, {len(seen)} clip(s) into the "
                    f"widened loop (video={Path(status.video).name!r}, "
                    f"pos={status.position_ms}, dur={status.duration_ms})\n"
                    f"--- player log ---\n{satellite.log_tail()}")
            time.sleep(0.25)
        assert len(seen) > 1, f"the loop never advanced, so nothing was proven (saw {len(seen)})"

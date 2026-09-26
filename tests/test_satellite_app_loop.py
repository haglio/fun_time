"""The satellite's run loop, actually run.

``satellite/app.py::_run`` — the status publish, the paused poll, the command drain and the overlay painting — was
guarded only by AST scans over its source, which hold no matter what the loop
does.  Here the loop runs for real: pygame and mpv are the two fakes (the
window system and the video engine, this process's true boundaries), the args
come from the production parser, and each test reads the loop's observable
output — the status file, the player's overlays, the session's state.

Every command file ends in QUIT, which is how a run is bounded to a known
number of passes instead of an event loop the test would have to break into.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from player_core.funscript import load as load_funscript
from player_core.playhead import PlayheadHudPainter, readout_xy, video_playhead
from player_core.timeline import TIMELINE_HEIGHT, bar_track_x, progress_bar_bgra
from player_core.volume import chip_xy

from main_player.heatmap import build_heatmap
from satellite.app import _run
from satellite.cli import build_parser, resolve_playlist
from tests.satellite_fakes import FakeSatellitePlayer


class _FakePygame:
    """Just enough SDL for the loop: a window that exists, events that arrive
    in scripted batches, and a clock whose tick is free."""

    QUIT = 256
    MOUSEBUTTONDOWN = 1025
    MOUSEMOTION = 1024

    def __init__(self, event_batches=()):
        self._batches = list(event_batches)
        self.quit_called = False
        self.display = SimpleNamespace(
            set_icon=lambda *_a: None,
            set_mode=lambda *_a, **_kw: None,
            set_caption=lambda *_a: None,
            get_wm_info=lambda: {"window": 4242},
            get_window_size=lambda: (640, 480),
        )
        self.event = SimpleNamespace(get=self._next_batch)
        self.time = SimpleNamespace(Clock=lambda: SimpleNamespace(tick=lambda _fps: None))
        self.NOFRAME = 32

    def _next_batch(self):
        return self._batches.pop(0) if self._batches else []

    def init(self):
        pass

    def quit(self):
        self.quit_called = True


def _loop_args(tmp_path: Path, playlist: list[Path], *, no_audio: bool = False,
               **extra: str):
    """The loop's args, defaulting to how ``_build_satellite_launch_command``
    launches one — which no longer passes ``--no-audio``, so the volume chip in
    these runs is the live one a session gets."""
    argv = ["--playlist", str(tmp_path / "playlist.tsv"),
            "--command-file", str(tmp_path / "cmd.txt"),
            "--paused-file", str(tmp_path / "paused.txt"),
            "--status-file", str(tmp_path / "status.txt"),
            "--title", "Portrait AI Player"]
    if no_audio:
        argv.append("--no-audio")
    for flag, value in extra.items():
        argv += [f"--{flag.replace('_', '-')}", value]
    (tmp_path / "playlist.tsv").write_text(
        "".join(f"{clip}\n" for clip in playlist), encoding="utf-8")
    return build_parser().parse_args(argv)


def _clips(tmp_path: Path, *names: str) -> list[Path]:
    out = []
    for name in names:
        clip = tmp_path / f"{name}.mp4"
        clip.write_bytes(b"")
        out.append(clip)
    return out


def _run_loop(tmp_path: Path, args, *, fake=None) -> tuple[int, FakeSatellitePlayer, _FakePygame]:
    fake = fake or _FakePygame()
    player = FakeSatellitePlayer()
    with patch("satellite.app.pygame", fake), \
         patch("satellite.app.deliver_the_focusing_click"), \
         patch("satellite.app._load_icon_surface", return_value=None), \
         patch("satellite.app.MpvPlayer", return_value=player):
        code = _run(args, playlist=resolve_playlist(args))
    return code, player, fake


def test_one_pass_plays_publishes_and_paints_then_quit_ends_it_cleanly(tmp_path):
    clips = _clips(tmp_path, "v0", "v1")
    args = _loop_args(tmp_path, clips)
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")

    code, player, fake = _run_loop(tmp_path, args)

    assert code == 0
    assert player.opened[0] == clips[0]                    # the first clip is up
    status = (tmp_path / "status.txt").read_text(encoding="utf-8")
    assert f"video={clips[0]}" in status                   # published for the loop
    assert len(player.overlays) == 3                       # scrubber, volume chip, readout
    assert player.closed and fake.quit_called              # a clean teardown


def test_one_pass_puts_up_where_the_clip_is_and_how_long_it_runs(tmp_path):
    clips = _clips(tmp_path, "v0")
    args = _loop_args(tmp_path, clips)
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")

    _code, player, _fake = _run_loop(tmp_path, args)

    pill = PlayheadHudPainter().bgra(
        video_playhead(0.0, player.duration_ms, player.frame_rate))
    at = readout_xy(pill.shape[1], win_w=640, win_h=480, timeline_h=TIMELINE_HEIGHT)
    assert any((x, y) == at and np.array_equal(bgra, pill)
               for x, y, bgra in player.overlays.values())


def test_a_scripted_clips_scrubber_is_filled_with_its_scripts_colors(tmp_path):
    clip = _clips(tmp_path, "v0")[0]
    script = tmp_path / "v0.funscript"
    script.write_text('{"actions": [{"at": 0, "pos": 0}, {"at": 900, "pos": 100}, '
                      '{"at": 2400, "pos": 10}]}', encoding="utf-8")
    args = _loop_args(tmp_path, [f"{clip}\t{script}"])
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")

    _code, player, _fake = _run_loop(tmp_path, args)

    x0, x1 = bar_track_x(640)
    _x, _y, bar = player.overlays[11]
    assert np.array_equal(bar, progress_bar_bgra(
        0.0, player.duration_ms, None, 640,
        heatmap=build_heatmap(load_funscript(script), x1 - x0,
                              start_ms=0, end_ms=player.duration_ms)))


def test_commands_drain_and_act_before_the_frame_is_published(tmp_path):
    clips = _clips(tmp_path, "v0", "v1")
    args = _loop_args(tmp_path, clips)
    (tmp_path / "cmd.txt").write_text("NEXT\nQUIT\n", encoding="utf-8")

    _code, player, _fake = _run_loop(tmp_path, args)

    status = (tmp_path / "status.txt").read_text(encoding="utf-8")
    assert f"video={clips[1]}" in status                   # the NEXT took effect


def test_the_paused_flag_reaches_the_player_each_pass(tmp_path):
    clips = _clips(tmp_path, "v0")
    args = _loop_args(tmp_path, clips)
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")
    (tmp_path / "paused.txt").write_text("1", encoding="utf-8")

    _code, player, _fake = _run_loop(tmp_path, args)

    assert player.paused is True


def test_the_window_close_asks_the_session_not_this_player(tmp_path):
    """Alt+F4 on one satellite must not leave the session running around a
    gap: the QUIT event posts the session-quit gesture, and only the
    gesture's answer ends this loop."""
    clips = _clips(tmp_path, "v0")
    args = _loop_args(tmp_path, clips)
    fake = _FakePygame(event_batches=[[SimpleNamespace(type=_FakePygame.QUIT)]])

    with patch("satellite.app.quit_gesture", return_value=True) as gesture:
        code, _player, _f = _run_loop(tmp_path, args, fake=fake)

    assert code == 0
    gesture.assert_called_once_with(args.dashboard_cmd_file)


def _press(pos, button=1):
    return SimpleNamespace(type=_FakePygame.MOUSEBUTTONDOWN, button=button, pos=pos)


def test_a_press_on_the_scrubber_seeks_the_clip(tmp_path):
    """The bar is drawn full-window-width along the lower edge, so a press halfway
    across the 640-wide window's inset track lands halfway through the clip."""
    clips = _clips(tmp_path, "v0")
    args = _loop_args(tmp_path, clips)
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")
    x0, x1 = bar_track_x(640)
    fake = _FakePygame(event_batches=[[_press(((x0 + x1) // 2, 476))]])

    _code, player, _fake = _run_loop(tmp_path, args, fake=fake)

    assert len(player.seeks) == 1
    assert abs(player.seeks[0] - player.duration_ms / 2) <= player.duration_ms / (x1 - x0)


def test_a_press_on_the_volume_chip_unmutes_this_player(tmp_path, unmuted):
    """The speaker at the left end of the chip, which is placed from the
    window's lower-right corner — a satellite opens muted and this is the way
    to hear one."""
    clips = _clips(tmp_path, "v0")
    args = _loop_args(tmp_path, clips)
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")
    vx, vy = chip_xy(win_w=640, win_h=480, timeline_h=TIMELINE_HEIGHT)
    fake = _FakePygame(event_batches=[[_press((vx + 7, vy + 11))]])

    _code, player, _fake = _run_loop(tmp_path, args, fake=fake)

    assert player.muted is False
    assert player.seeks == []          # the chip took it, not the row under it


def test_no_audio_leaves_the_chip_a_read_only_indicator(tmp_path):
    """What the hidden-desktop integration runs buy with FUN_TIME_MUTE_AUDIO:
    silence no press can lift."""
    clips = _clips(tmp_path, "v0")
    args = _loop_args(tmp_path, clips, no_audio=True)
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")
    vx, vy = chip_xy(win_w=640, win_h=480, timeline_h=TIMELINE_HEIGHT)
    fake = _FakePygame(event_batches=[[_press((vx + 7, vy + 11))]])

    _code, player, _fake = _run_loop(tmp_path, args, fake=fake)

    assert player.muted is True


def test_each_pass_creeps_a_little_further_into_the_picture(tmp_path):
    """A still does not simply sit there while it holds the screen — the loop
    asks the player to push into it every frame, the way it repaints the
    overlays every frame."""
    clips = _clips(tmp_path, "v0")
    args = _loop_args(tmp_path, clips)
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")

    _code, player, _fake = _run_loop(tmp_path, args)

    assert player.pushes == 1

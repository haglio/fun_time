"""The satellite's run loop, actually run.

``satellite/app.py::_run`` — the origenerator blackout machine, the status
publish, the paused poll, the command drain and the overlay painting — was
guarded only by AST scans over its source, which hold no matter what the loop
does.  Here the loop runs for real: pygame and mpv are the two fakes (the
window system and the video engine, this process's true boundaries), the args
come from the production parser, and each test reads the loop's observable
output — the status file, the player's overlays, the session's state.

Every command file ends in QUIT, which is how a run is bounded to a known
number of passes instead of an event loop the test would have to break into.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from player_core.timeline import TIMELINE_HEIGHT
from player_core.volume import chip_xy

from satellite.app import _run
from satellite.cli import build_parser
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
        code = _run(args, playlist=[Path(line.split("\t")[0]) for line in
                                    (tmp_path / "playlist.tsv").read_text(encoding="utf-8").splitlines()])
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
    assert len(player.overlays) == 2                       # scrubber + volume chip
    assert player.closed and fake.quit_called              # a clean teardown


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
    hole: the QUIT event posts the session-quit gesture, and only the
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
    """The bar is drawn full-window-width along the bottom, so a press halfway
    across the 640-wide window's inset track lands halfway through the clip."""
    clips = _clips(tmp_path, "v0")
    args = _loop_args(tmp_path, clips)
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")
    fake = _FakePygame(event_batches=[[_press((274, 476))]])

    _code, player, _fake = _run_loop(tmp_path, args, fake=fake)

    assert player.seeks == [player.duration_ms / 2]


def test_a_press_on_the_volume_chip_unmutes_this_player(tmp_path):
    """The speaker at the left end of the chip, which is placed from the
    window's bottom-right corner — a satellite opens muted and this is the way
    to hear one."""
    clips = _clips(tmp_path, "v0")
    args = _loop_args(tmp_path, clips)
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")
    vx, vy = chip_xy(win_w=640, win_h=480, timeline_h=TIMELINE_HEIGHT)
    fake = _FakePygame(event_batches=[[_press((vx + 7, vy + 11))]])

    _code, player, _fake = _run_loop(tmp_path, args, fake=fake)

    assert player.muted is False
    assert player.seeks == []          # the chip took it, not the row behind it


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


def _suppressed_panel(tmp_path: Path) -> Path:
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (40, 60), (90, 90, 90)).save(thumb)
    panel = tmp_path / "portrait_hud.json"
    panel.write_text(json.dumps({
        "side": "portrait", "locked": False, "lock_label": "Unlocked",
        "satellites_mode": "origenerator",
        "corner": None, "seeds": [], "actions": [],
    }), encoding="utf-8")
    return panel


def test_origenerator_mode_blacks_the_video_out_under_the_hud(tmp_path):
    """The region is the hosted app's: an opaque frame goes up over the video
    and the scrubber and volume chip come down — the blackout the HUD's own
    tests pin the FLAG for, acted on here by the loop itself."""
    clips = _clips(tmp_path, "v0")
    panel = _suppressed_panel(tmp_path)
    args = _loop_args(
        tmp_path, clips,
        hud_file=str(panel), dashboard_cmd_file=str(tmp_path / "dash_cmd.txt"),
    )
    (tmp_path / "cmd.txt").write_text("QUIT\n", encoding="utf-8")

    _code, player, _fake = _run_loop(tmp_path, args)

    # One overlay is the HUD's own panel; the blackout frame is a full-window
    # opaque plate at the origin.  No scrubber, no volume chip.
    plates = [
        (x, y, bgra) for (x, y, bgra) in player.overlays.values()
        if getattr(bgra, "shape", None) == (480, 640, 4)
    ]
    assert len(plates) == 1
    x, y, plate = plates[0]
    assert (x, y) == (0, 0)
    assert int(plate[:, :, 3].min()) == 255      # opaque…
    assert int(plate[:, :, :3].max()) == 0       # …and black

"""Fun Time's satellite program: the window it opens, and the Funestra it runs on that window.

pygame is the one fake (the window system, this process's true boundary), the
args come from the production parser, and what the loop hands the Funestra --
the handle, the launch's channels and list, each pass's window size, each press
and the close -- is read back off a fake Funestra.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pygame

from fun_time.project_paths import PROJECT_ICON
from satellite.app import _run
from satellite.cli import build_parser, resolve_playlist
from satellite.contract import SatelliteChannels

WINDOW = (640, 480)


class _FakePygame:
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
            get_window_size=lambda: WINDOW,
        )
        self.event = SimpleNamespace(get=self._next_batch)
        self.time = SimpleNamespace(Clock=lambda: SimpleNamespace(tick=lambda _fps: None))
        self.NOFRAME = 32
        self.window = SimpleNamespace(resizable=False)
        self.Window = SimpleNamespace(from_display_module=lambda: self.window)

    def _next_batch(self):
        return self._batches.pop(0) if self._batches else []

    def init(self):
        pass

    def quit(self):
        self.quit_called = True


class _FakeFunestra:
    def __init__(self, *, passes: int = 1) -> None:
        self._passes_left = passes
        self.ticks: list[tuple[int, int]] = []
        self.presses: list[tuple] = []
        self.motions: list[tuple] = []
        self.closes_requested = 0
        self.closed = False

    @property
    def stopped(self) -> bool:
        return self._passes_left <= 0

    def tick(self, *, window) -> None:
        self.ticks.append(window)
        self._passes_left -= 1

    def press(self, x, y, *, window) -> None:
        self.presses.append((x, y, window))

    def motion(self, x, y, *, held, window) -> None:
        self.motions.append((x, y, held, window))

    def close_requested(self) -> None:
        self.closes_requested += 1

    def close(self) -> None:
        self.closed = True


class _OpensAFunestra:
    def __init__(self, funestra: _FakeFunestra) -> None:
        self.funestra = funestra
        self.asked: tuple | None = None

    def on_window(self, wid, **what):
        self.asked = (wid, what)
        return self.funestra


def _loop_args(tmp_path: Path, playlist: list[Path], *, no_audio: bool = False,
               tile: bool = False, **extra: str):
    argv = ["--playlist", str(tmp_path / "playlist.tsv"),
            "--command-file", str(tmp_path / "cmd.txt"),
            "--paused-file", str(tmp_path / "paused.txt"),
            "--status-file", str(tmp_path / "status.txt"),
            "--title", "Portrait AI Player"]
    if no_audio:
        argv.append("--no-audio")
    if tile:
        argv.append("--tile")
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


def _run_loop(tmp_path: Path, args, *, fake=None, passes: int = 1):
    fake = fake or _FakePygame()
    opens = _OpensAFunestra(_FakeFunestra(passes=passes))
    with patch("satellite.app.pygame", fake), \
         patch("satellite.app.deliver_the_focusing_click"), \
         patch("satellite.app.Funestra", opens):
        code = _run(args, playlist=resolve_playlist(args))
    return code, opens, fake


def test_a_satellite_wears_the_icon_the_session_hands_it(tmp_path):
    handed = tmp_path / "preview_icon.ico"
    shutil.copyfile(PROJECT_ICON, handed)
    args = _loop_args(tmp_path, _clips(tmp_path, "v0"), icon=str(handed))
    fake = _FakePygame()
    worn = []
    fake.display.set_icon = worn.append
    fake.image = pygame.image

    _run_loop(tmp_path, args, fake=fake)

    assert len(worn) == 1


def test_the_funestra_is_opened_on_the_window_with_what_the_launch_hands_it(tmp_path, unmuted):
    args = _loop_args(tmp_path, _clips(tmp_path, "v0", "v1"))

    _code, opens, _fake = _run_loop(tmp_path, args)

    wid, what = opens.asked
    assert wid == 4242
    assert what == {
        "channels": SatelliteChannels.from_args(args),
        "playlist": resolve_playlist(args),
        "audible": True,
        "tiles": False,
    }


def test_no_audio_opens_a_silent_one(tmp_path):
    args = _loop_args(tmp_path, _clips(tmp_path, "v0"), no_audio=True)

    _code, opens, _fake = _run_loop(tmp_path, args)

    assert opens.asked[1]["audible"] is False


def test_tile_opens_one_that_tiles_its_picture(tmp_path):
    args = _loop_args(tmp_path, _clips(tmp_path, "v0"), tile=True)

    _code, opens, _fake = _run_loop(tmp_path, args)

    assert opens.asked[1]["tiles"] is True


def test_each_pass_hands_it_the_windows_size_until_it_stops_then_closes_it(tmp_path):
    args = _loop_args(tmp_path, _clips(tmp_path, "v0"))

    code, opens, fake = _run_loop(tmp_path, args, passes=3)

    assert code == 0
    assert opens.funestra.ticks == [WINDOW, WINDOW, WINDOW]
    assert opens.funestra.closed and fake.quit_called


def _press(pos, button=1):
    return SimpleNamespace(type=_FakePygame.MOUSEBUTTONDOWN, button=button, pos=pos)


def _motion(pos, *, held: bool):
    return SimpleNamespace(type=_FakePygame.MOUSEMOTION, pos=pos, buttons=(int(held), 0, 0))


def test_a_left_press_and_a_motion_reach_it_placed_in_the_window(tmp_path):
    args = _loop_args(tmp_path, _clips(tmp_path, "v0"))
    fake = _FakePygame(event_batches=[[_press((10, 20)), _press((11, 21), button=3),
                                       _motion((30, 40), held=True)]])

    _code, opens, _fake = _run_loop(tmp_path, args, fake=fake)

    assert opens.funestra.presses == [(10, 20, WINDOW)]
    assert opens.funestra.motions == [(30, 40, True, WINDOW)]


def test_the_windows_close_is_the_funestras_to_answer_and_the_loop_goes_on(tmp_path):
    args = _loop_args(tmp_path, _clips(tmp_path, "v0"))
    fake = _FakePygame(event_batches=[[SimpleNamespace(type=_FakePygame.QUIT)]])

    _code, opens, _fake = _run_loop(tmp_path, args, fake=fake, passes=2)

    assert opens.funestra.closes_requested == 1
    assert len(opens.funestra.ticks) == 2


def test_the_window_takes_the_size_fun_time_gives_it_from_outside(tmp_path):
    args = _loop_args(tmp_path, _clips(tmp_path, "v0"))

    _code, _opens, fake = _run_loop(tmp_path, args)

    assert fake.window.resizable is True

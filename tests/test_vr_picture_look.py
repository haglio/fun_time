from __future__ import annotations

from pathlib import Path

import numpy as np

from fun_time_vr.picture_look import LOOKS, PictureLook
from fun_time_vr.picture_shape import FISHEYE_CIRCLE, FULL_FRAME
from tests.test_vr_picture_shape import _side_by_side

VIDEO = Path("scene one.mp4")
CIRCLE = _side_by_side(lambda x, y, r: r < 0.97)
FULL = _side_by_side(lambda x, y, r: r < 9)
BLACK = _side_by_side(lambda x, y, r: r < 0)


def _bgra(luma: np.ndarray) -> np.ndarray:
    return np.dstack([luma, luma, luma, np.full_like(luma, 255)])


def _run_now(*, target, args=(), name=None) -> None:
    target(*args)


class _Player:
    """A video thread's surface: a still is asked for and turns up on a later look,
    painted by the thread that owns the GL context."""

    def __init__(self, frames, *, duration_ms: float = 60_000.0) -> None:
        self.frames = list(frames)
        self.duration_ms = duration_ms
        self._asked = False

    def ask_for_a_still(self) -> None:
        self._asked = True

    def take_a_still(self):
        if not self._asked or not self.frames:
            return None
        self._asked = False
        return self.frames.pop(0)


def _looked(player) -> list[tuple[Path, str]]:
    said: list[tuple[Path, str]] = []
    look = PictureLook(player, start_thread=_run_now, sleep=lambda _seconds: None)
    look.look_at(VIDEO, lambda video, shape: said.append((video, shape)))
    return said


def test_a_shape_seen_twice_is_what_the_video_is():
    assert _looked(_Player([_bgra(CIRCLE), _bgra(CIRCLE)])) == [(VIDEO, FISHEYE_CIRCLE)]


def test_one_look_alone_is_not_believed():
    looks = [_bgra(CIRCLE), _bgra(FULL), _bgra(FULL), _bgra(FULL)]

    assert _looked(_Player(looks)) == [(VIDEO, FULL_FRAME)]


def test_a_picture_filling_the_frame_takes_a_third_look_to_believe():
    """A studio's opening title fills the frame of a fisheye recording too, and
    lasts a few seconds; the circle under it shows up once it has gone."""
    looks = [_bgra(FULL), _bgra(FULL), _bgra(CIRCLE), _bgra(CIRCLE)]

    assert _looked(_Player(looks)) == [(VIDEO, FISHEYE_CIRCLE)]


def test_a_look_that_shows_nothing_is_passed_over():
    """Black between scenes, and no frame at all while the file is still opening."""
    looks = [_bgra(BLACK), None, _bgra(CIRCLE), _bgra(BLACK), _bgra(CIRCLE)]

    assert _looked(_Player(looks)) == [(VIDEO, FISHEYE_CIRCLE)]


def test_it_waits_before_each_look_so_the_file_asked_for_is_the_one_playing():
    """mpv takes a moment to let go of the last file, and its picture would be
    read as the new one's."""
    events: list[str] = []
    player = _Player([_bgra(CIRCLE), _bgra(CIRCLE)])
    grab = player.take_a_still
    player.take_a_still = lambda: events.append("look") or grab()
    look = PictureLook(player, start_thread=_run_now,
                       sleep=lambda _seconds: events.append("wait"))

    look.look_at(VIDEO, lambda video, shape: None)

    assert events == ["wait", "look", "wait", "look"]


def test_a_player_with_no_file_open_yet_is_not_asked_for_its_picture():
    player = _Player([_bgra(CIRCLE), _bgra(CIRCLE)], duration_ms=0.0)

    assert _looked(player) == []
    assert len(player.frames) == 2


def test_opening_another_video_ends_the_look_at_the_last_one():
    other = Path("scene two.mp4")
    said: list[tuple[Path, str]] = []
    player = _Player([_bgra(CIRCLE), _bgra(FULL), _bgra(FULL), _bgra(FULL), _bgra(CIRCLE)])
    look = PictureLook(player, start_thread=_run_now, sleep=lambda _seconds: None)
    grab = player.take_a_still

    def grab_then_move_on():
        frame = grab()
        if len(player.frames) == 4:  # the first look at the first video, just taken
            look.look_at(other, lambda video, shape: said.append((video, shape)))
        return frame

    player.take_a_still = grab_then_move_on

    look.look_at(VIDEO, lambda video, shape: said.append((video, shape)))

    assert said == [(other, FULL_FRAME)]


def test_a_video_opened_again_is_looked_at_once_not_twice_over():
    said: list[tuple[Path, str]] = []
    player = _Player([_bgra(CIRCLE)] * 5)
    look = PictureLook(player, start_thread=_run_now, sleep=lambda _seconds: None)
    grab = player.take_a_still

    def grab_then_open_it_again():
        frame = grab()
        if len(player.frames) == 4:
            look.look_at(VIDEO, lambda video, shape: said.append((video, shape)))
        return frame

    player.take_a_still = grab_then_open_it_again

    look.look_at(VIDEO, lambda video, shape: said.append((video, shape)))

    assert said == [(VIDEO, FISHEYE_CIRCLE)]


def test_a_video_that_never_shows_its_shape_is_given_up_on():
    player = _Player([_bgra(BLACK)] * (LOOKS + 3))

    assert _looked(player) == []
    assert len(player.frames) == 3


def test_a_session_closing_ends_the_look():
    said: list[tuple[Path, str]] = []
    player = _Player([_bgra(CIRCLE), _bgra(CIRCLE)])
    look = PictureLook(player, start_thread=_run_now, sleep=lambda _seconds: look.close())

    look.look_at(VIDEO, lambda video, shape: said.append((video, shape)))

    assert said == []

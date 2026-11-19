from __future__ import annotations

import numpy as np

from fun_time_vr.texture_rows import (
    Picture,
    Rows,
    as_the_texture_holds_them,
    rows_changed,
    rows_together,
)


def _picture(height=6, width=5, seed=1) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (height, width, 4), dtype=np.uint8)


def _texture_after(texture: np.ndarray, pixels: np.ndarray, rows: Rows) -> np.ndarray:
    first_row, held = as_the_texture_holds_them(pixels, rows)
    texture = texture.copy()
    texture[first_row:first_row + len(held)] = held
    return texture


def test_a_picture_is_held_bottom_row_first_as_the_card_reads_it():
    pixels = _picture()

    first_row, held = as_the_texture_holds_them(pixels, Rows(0, 6))

    assert first_row == 0
    assert held.tobytes() == np.ascontiguousarray(pixels[::-1]).tobytes()


def test_sending_only_the_changed_rows_leaves_the_texture_as_sending_all_of_them_would():
    before, after = _picture(seed=1), _picture(seed=1)
    after[2:4] = 0
    texture = np.ascontiguousarray(before[::-1])

    changed = rows_changed(before, after)

    assert changed == Rows(2, 4)
    assert np.array_equal(_texture_after(texture, after, changed),
                          np.ascontiguousarray(after[::-1]))


def test_a_picture_that_did_not_change_has_no_rows_to_send():
    assert rows_changed(_picture(), _picture()) is None


def test_a_first_picture_or_a_new_size_is_sent_whole():
    assert rows_changed(None, _picture()) == Rows(0, 6)
    assert rows_changed(_picture(height=4), _picture()) == Rows(0, 6)


def test_changes_not_yet_sent_add_up_to_every_row_any_of_them_touched():
    assert rows_together(None, Rows(2, 3)) == Rows(2, 3)
    assert rows_together(Rows(2, 3), None) == Rows(2, 3)
    assert rows_together(Rows(4, 5), Rows(1, 2)) == Rows(1, 5)


class _Texture:
    def __init__(self) -> None:
        self.sent: list[tuple[np.ndarray, Rows]] = []

    def upload(self, pixels, *, rows=None) -> None:
        self.sent.append((pixels, rows))

    @property
    def ready(self) -> bool:
        return bool(self.sent)


def test_a_picture_put_is_sent_once_however_many_frames_come():
    picture, texture = Picture(), _Texture()
    pixels = _picture()

    picture.put(pixels)
    shown = [picture.send_to(texture) for _ in range(4)]

    assert shown == [True] * 4
    assert [(sent is pixels, rows) for sent, rows in texture.sent] == [(True, Rows(0, 6))]


def test_pictures_put_between_two_frames_send_the_last_with_every_row_they_changed():
    picture, texture = Picture(), _Texture()
    picture.put(_picture())
    picture.send_to(texture)
    second, third = _picture(seed=2), _picture(seed=3)

    picture.put(second, only=Rows(1, 2))
    picture.put(third, only=Rows(4, 5))
    picture.send_to(texture)

    assert texture.sent[-1][0] is third
    assert texture.sent[-1][1] == Rows(1, 5)


def test_nothing_put_yet_sends_nothing():
    texture = _Texture()

    assert Picture().send_to(texture) is False
    assert Picture().pixels is None

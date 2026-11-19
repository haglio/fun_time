from __future__ import annotations

import numpy as np

from fun_time_vr.texture_rows import Rows, as_the_texture_holds_them, rows_changed, rows_together


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

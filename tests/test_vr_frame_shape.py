from __future__ import annotations

import numpy as np

from fun_time_vr.frame_shape import FISHEYE_CIRCLE, FULL_FRAME, shape_of

EYE_PX = 128


class _MeasuredOnReal180Masters:
    A_LENS_A_LITTLE_SHORT_OF_180_REACHES = 1.28
    BLACK_BARS_BEGIN_AT = 0.89
    A_BLACKED_OUT_BOTTOM_BEGINS_AT = 0.67
    A_200_DEGREE_LENS_CROPPED_BY_ITS_SQUARE_REACHES = 1.15


def _eye(lit) -> np.ndarray:
    span = (np.arange(EYE_PX) + 0.5) / EYE_PX * 2 - 1
    x, y = np.meshgrid(span, span)
    return np.where(lit(x, y, np.hypot(x, y)), 200, 0).astype(np.uint8)


def _side_by_side(lit) -> np.ndarray:
    eye = _eye(lit)
    return np.concatenate([eye, eye], axis=1)


def test_a_lit_circle_in_each_eyes_square_is_a_fisheye_recording():
    assert shape_of(_side_by_side(lambda x, y, r: r < 0.97)) == FISHEYE_CIRCLE


def test_a_frame_lit_to_its_corners_fills_the_frame():
    assert shape_of(_side_by_side(lambda x, y, r: r < 9)) == FULL_FRAME


def test_a_black_frame_says_nothing_about_the_recording():
    assert shape_of(_side_by_side(lambda x, y, r: r < 0)) is None


def test_a_fisheye_recording_with_a_studios_mark_in_each_corner_is_still_one():
    marked = _side_by_side(lambda x, y, r: (r < 0.95) | ((abs(x) > 0.8) & (abs(y) > 0.8)))

    assert shape_of(marked) == FISHEYE_CIRCLE


class TestFramesThatFillTheFrameInTheirOwnWays:
    def test_corners_rounded_off_by_a_lens_a_little_short_of_180(self):
        reach = _MeasuredOnReal180Masters.A_LENS_A_LITTLE_SHORT_OF_180_REACHES

        assert shape_of(_side_by_side(lambda x, y, r: r < reach)) == FULL_FRAME

    def test_black_bars_above_and_below_are_not_a_circle(self):
        bars = _MeasuredOnReal180Masters.BLACK_BARS_BEGIN_AT

        assert shape_of(_side_by_side(lambda x, y, r: abs(y) < bars)) != FISHEYE_CIRCLE

    def test_a_bottom_blacked_out_by_the_studio_is_not_a_circle(self):
        bottom = _MeasuredOnReal180Masters.A_BLACKED_OUT_BOTTOM_BEGINS_AT

        assert shape_of(_side_by_side(lambda x, y, r: y < bottom)) != FISHEYE_CIRCLE


def test_a_dim_scene_lit_only_in_its_middle_is_not_taken_for_a_circle():
    assert shape_of(_side_by_side(lambda x, y, r: r < 0.7)) is None


def test_the_two_eyes_have_to_agree():
    a_flat_videos_half_that_looks_like_a_circle = _eye(lambda x, y, r: r < 0.97)
    its_other_half = _eye(lambda x, y, r: r < 9)

    assert shape_of(np.concatenate(
        [a_flat_videos_half_that_looks_like_a_circle, its_other_half], axis=1)) is None
    assert shape_of(np.concatenate(
        [its_other_half, a_flat_videos_half_that_looks_like_a_circle], axis=1)) is None


def test_a_circle_wider_than_its_square_is_left_to_the_videos_name():
    reach = _MeasuredOnReal180Masters.A_200_DEGREE_LENS_CROPPED_BY_ITS_SQUARE_REACHES
    corners_neither_black_nor_video = _side_by_side(lambda x, y, r: r < reach)

    assert shape_of(corners_neither_black_nor_video) is None

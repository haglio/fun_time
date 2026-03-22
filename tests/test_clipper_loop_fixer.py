from __future__ import annotations

import numpy as np

from fun_time.robot_hand.clipper.loop_fixer_and_sizer import normalize_loop_mode, shift_frames_halfway


def _frames(values: list[int]) -> list[np.ndarray]:
    return [np.full((1, 1, 3), value, dtype=np.uint8) for value in values]


def _values(frames: list[np.ndarray]) -> list[int]:
    return [int(frame[0, 0, 0]) for frame in frames]


class TestShiftFramesHalfway:
    def test_rotates_sequence_from_middle(self):
        frames = _frames([1, 2, 3, 4])
        assert _values(shift_frames_halfway(frames)) == [3, 4, 1, 2]


class TestNormalizeLoopMode:
    def test_base_tip_base_is_unchanged(self):
        frames = _frames([1, 2, 3, 2, 1])
        assert _values(normalize_loop_mode(frames, "base-tip-base")) == [1, 2, 3, 2, 1]

    def test_tip_base_tip_rotates_by_half(self):
        frames = _frames([5, 4, 3, 2, 1, 2])
        assert _values(normalize_loop_mode(frames, "tip-base-tip")) == [2, 1, 2, 5, 4, 3]

    def test_base_tip_appends_reversed_tail_without_duplicate_tip(self):
        frames = _frames([1, 2, 3])
        assert _values(normalize_loop_mode(frames, "base-tip")) == [1, 2, 3, 2, 1]

    def test_tip_base_prepends_reversed_head_without_duplicate_tip(self):
        frames = _frames([3, 2, 1])
        assert _values(normalize_loop_mode(frames, "tip-base")) == [1, 2, 3, 2, 1]

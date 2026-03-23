from __future__ import annotations

import numpy as np
import pytest

from fun_time.robot_hand.clipper.clip_postprocess_pipeline import build_output_frames, compute_bridge_frames


def _frames(values: list[int]) -> list[np.ndarray]:
    return [np.full((1, 1, 3), value, dtype=np.uint8) for value in values]


def _values(frames: list[np.ndarray]) -> list[int]:
    return [int(frame[0, 0, 0]) for frame in frames]


def test_compute_bridge_frames_uses_milliseconds_when_explicit_frames_missing():
    result = compute_bridge_frames(fps=20.0, bridge_ms=150.0, bridge_frames=None, normalized_frame_count=20)
    assert result == 3


def test_compute_bridge_frames_caps_to_one_third_of_normalized_frames():
    result = compute_bridge_frames(fps=60.0, bridge_ms=500.0, bridge_frames=None, normalized_frame_count=9)
    assert result == 3


def test_build_output_frames_keep_length_replaces_tail_with_bridge():
    out_frames, normalized_n = build_output_frames(
        _frames([1, 2, 3, 4]),
        loop_mode="base-tip-base",
        bridge_frames=1,
        mode="blend",
        keep_length=True,
        symmetric_blend=0,
    )
    assert normalized_n == 4
    assert len(out_frames) == 4
    assert _values(out_frames[:3]) == [1, 2, 3]


def test_build_output_frames_append_keeps_original_and_adds_bridge():
    out_frames, normalized_n = build_output_frames(
        _frames([1, 2, 3, 4]),
        loop_mode="base-tip-base",
        bridge_frames=2,
        mode="blend",
        keep_length=False,
        symmetric_blend=0,
    )
    assert normalized_n == 4
    assert len(out_frames) == 6
    assert _values(out_frames[:4]) == [1, 2, 3, 4]


def test_build_output_frames_rejects_keep_length_when_bridge_is_too_long():
    with pytest.raises(RuntimeError, match="--keep-length bridge is too long"):
        build_output_frames(
            _frames([1, 2, 3]),
            loop_mode="base-tip-base",
            bridge_frames=3,
            mode="blend",
            keep_length=True,
            symmetric_blend=0,
        )

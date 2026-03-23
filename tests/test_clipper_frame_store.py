from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from fun_time.robot_hand.clipper.state import ensure_loaded, load_range, safe_frame, signature_for_index

from tests.test_clipper_state import _make_state


def test_load_range_returns_empty_when_end_before_start():
    cap = MagicMock()

    result = load_range(cap, 10, 5)

    assert result == {}
    cap.set.assert_not_called()
    cap.read.assert_not_called()


def test_ensure_loaded_expands_missing_edges_and_bumps_render_rev():
    state = _make_state(loaded_start=10, loaded_end=20)
    left_frames = {i: np.full((2, 2, 3), i, dtype=np.uint8) for i in range(5, 10)}
    right_frames = {i: np.full((2, 2, 3), i, dtype=np.uint8) for i in range(21, 26)}

    with patch("fun_time.robot_hand.clipper.state.load_range", side_effect=[left_frames, right_frames]) as load:
        ensure_loaded(state, 5, 25)

    assert state.loaded_start == 5
    assert state.loaded_end == 25
    assert state.render_rev == 1
    assert state.frames[5].shape == (2, 2, 3)
    assert state.frames[25].shape == (2, 2, 3)
    load.assert_any_call(state.cap, 5, 9)
    load.assert_any_call(state.cap, 21, 25)


def test_ensure_loaded_is_noop_when_requested_range_is_already_loaded():
    state = _make_state(loaded_start=10, loaded_end=20)

    with patch("fun_time.robot_hand.clipper.state.load_range") as load:
        ensure_loaded(state, 12, 18)

    assert state.loaded_start == 10
    assert state.loaded_end == 20
    assert state.render_rev == 0
    load.assert_not_called()


def test_safe_frame_loads_missing_index_on_demand():
    state = _make_state(loaded_start=10, loaded_end=20)
    missing_frame = np.ones((2, 2, 3), dtype=np.uint8)
    state.frames.pop(25, None)

    def fake_ensure_loaded(target_state, want_start: int, want_end: int) -> None:
        assert target_state is state
        assert (want_start, want_end) == (25, 25)
        state.frames[25] = missing_frame

    with patch("fun_time.robot_hand.clipper.state.ensure_loaded", side_effect=fake_ensure_loaded) as ensure:
        frame = safe_frame(state, 25)

    assert frame is missing_frame
    ensure.assert_called_once_with(state, 25, 25)


def test_safe_frame_raises_when_on_demand_load_still_fails():
    state = _make_state()
    state.frames.pop(35, None)

    with patch("fun_time.robot_hand.clipper.state.ensure_loaded"):
        with pytest.raises(RuntimeError, match="Could not load frame 35"):
            safe_frame(state, 35)


def test_signature_for_index_caches_processed_signature():
    state = _make_state()
    cached_signature = np.ones((3, 3), dtype=np.float32)

    with patch("fun_time.robot_hand.clipper.state.preprocess_frame_signature", return_value=cached_signature) as preprocess:
        first = signature_for_index(state, 20)
        second = signature_for_index(state, 20)

    assert first is cached_signature
    assert second is cached_signature
    preprocess.assert_called_once()

from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.robot_hand.clip_sequence import ClipSequenceController


def _paths():
    return [Path("a.mp4"), Path("b.mp4"), Path("c.mp4")]


def test_requires_at_least_one_clip():
    with pytest.raises(ValueError):
        ClipSequenceController([])


def test_starts_at_first_clip():
    controller = ClipSequenceController(_paths())

    assert controller.current_number == 1
    assert controller.current_path == Path("a.mp4")
    assert controller.count == 3


def test_step_wraps_forward_and_backward():
    controller = ClipSequenceController(_paths())

    assert controller.step(1) == Path("b.mp4")
    assert controller.step(2) == Path("a.mp4")
    assert controller.step(-1) == Path("c.mp4")


def test_nearby_candidates_prefers_next_then_previous():
    controller = ClipSequenceController(_paths())
    controller.step(1)

    assert controller.nearby_candidates() == [Path("c.mp4"), Path("a.mp4")]


def test_nearby_candidates_empty_for_single_clip():
    controller = ClipSequenceController([Path("solo.mp4")])

    assert controller.nearby_candidates() == []

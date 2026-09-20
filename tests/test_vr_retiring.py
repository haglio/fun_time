"""Quad swapchains replaced mid-session, and when they may be destroyed."""
from __future__ import annotations

from fun_time_vr.retiring import RETIRE_AFTER_FRAMES, RetiredSwapchain, advance_retirements


def test_a_replaced_swapchain_survives_the_frames_already_in_flight():
    """Destroying one the instant it is replaced destroys a chain the compositor
    may still be reading, so it waits out the frames in flight."""
    retiring = [RetiredSwapchain(handle="old")]

    for frame in range(RETIRE_AFTER_FRAMES - 1):
        retiring, destroy = advance_retirements(retiring)
        assert destroy == [], f"destroyed on frame {frame + 1}"
        assert len(retiring) == 1

    retiring, destroy = advance_retirements(retiring)

    assert destroy == ["old"]
    assert retiring == []


def test_each_replaced_swapchain_waits_out_its_own_frames():
    """Two clips resizing a frame apart retire a frame apart."""
    first = [RetiredSwapchain(handle="first")]
    first, _ = advance_retirements(first)
    both = [*first, RetiredSwapchain(handle="second")]

    for _ in range(RETIRE_AFTER_FRAMES - 2):
        both, destroy = advance_retirements(both)
        assert destroy == []

    both, destroy = advance_retirements(both)

    assert destroy == ["first"]
    assert [entry.handle for entry in both] == ["second"]


def test_nothing_retiring_is_a_frame_with_nothing_to_do():
    assert advance_retirements([]) == ([], [])

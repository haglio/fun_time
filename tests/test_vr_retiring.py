from __future__ import annotations

import ast
import inspect

from fun_time_vr import retiring as retiring_module
from fun_time_vr.retiring import RETIRE_AFTER_FRAMES, RetiredSwapchain, advance_retirements

WHAT_ONLY_RUNS_ON_A_HEADSET = {"xr", "OpenGL", "glfw"}


def test_retiring_is_kept_where_it_needs_no_headset_to_run():
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(ast.parse(inspect.getsource(retiring_module)))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imported & WHAT_ONLY_RUNS_ON_A_HEADSET == set()


def test_a_replaced_swapchain_survives_the_frames_already_in_flight():
    retiring = [RetiredSwapchain(handle="old")]

    for frame in range(RETIRE_AFTER_FRAMES - 1):
        retiring, destroy = advance_retirements(retiring)
        assert destroy == [], f"destroyed on frame {frame + 1}"
        assert len(retiring) == 1

    retiring, destroy = advance_retirements(retiring)

    assert destroy == ["old"]
    assert retiring == []


def test_each_replaced_swapchain_waits_out_its_own_frames():
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

"""Quad swapchains a clip's size change replaced, waiting to be destroyed.

A retired chain may still be composited by the frames already in flight, so
destruction waits. Kept out of :mod:`fun_time_vr.vr_session` because the
bookkeeping needs no headset and the session shell cannot be unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RETIRE_AFTER_FRAMES = 3


@dataclass
class RetiredSwapchain:
    """A replaced quad swapchain and the frames it has left to wait."""

    handle: Any
    frames_left: int = RETIRE_AFTER_FRAMES


def advance_retirements(
    retiring: list[RetiredSwapchain],
) -> tuple[list[RetiredSwapchain], list[Any]]:
    """One frame on: what is still waiting, and what may be destroyed now."""
    waiting: list[RetiredSwapchain] = []
    destroy: list[Any] = []
    for retired in retiring:
        retired.frames_left -= 1
        if retired.frames_left <= 0:
            destroy.append(retired.handle)
        else:
            waiting.append(retired)
    return waiting, destroy

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RETIRE_AFTER_FRAMES = 3


@dataclass
class RetiredSwapchain:
    handle: Any
    frames_left: int = RETIRE_AFTER_FRAMES


def advance_retirements(
    retiring: list[RetiredSwapchain],
) -> tuple[list[RetiredSwapchain], list[Any]]:
    waiting: list[RetiredSwapchain] = []
    destroy: list[Any] = []
    for retired in retiring:
        retired.frames_left -= 1
        if retired.frames_left <= 0:
            destroy.append(retired.handle)
        else:
            waiting.append(retired)
    return waiting, destroy

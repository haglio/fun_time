from __future__ import annotations

from collections.abc import Callable
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


def _held(current: dict[int, Any], index: int) -> Any:
    return current.get(index)


class QuadSwapchains:
    def __init__(self, *, make: Callable[[int, int], Any], destroy: Callable[[Any], None]) -> None:
        self._make = make
        self._destroy = destroy
        self._current: dict[int, Any] = {}
        self._retiring: list[RetiredSwapchain] = []

    def __getitem__(self, index: int) -> Any:
        return self._current[index]

    def ensure(self, index: int, width: int, height: int) -> bool:
        existing = _held(self._current, index)
        if existing is not None and (existing.width, existing.height) == (width, height):
            return False
        if existing is not None:
            self._retiring = [*self._retiring, RetiredSwapchain(existing.handle)]
        self._current[index] = self._make(width, height)
        return True

    def frame_ended(self) -> None:
        self._retiring, done_waiting = advance_retirements(self._retiring)
        for handle in done_waiting:
            self._destroy(handle)

    def close(self) -> None:
        for index in self._current:
            self._destroy(self._current[index].handle)
        for retired in self._retiring:
            self._destroy(retired.handle)

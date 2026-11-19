from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass

from .pointer import Screen
from .scene import encloses


@dataclass(frozen=True)
class Pane:
    screens: tuple[Screen, ...]


def _covers(pane: Pane, other: Pane) -> bool:
    inner = other.screens[0]
    return any(encloses(screen.placement, inner.placement,
                        outer_aspect=screen.aspect, inner_aspect=inner.aspect)
               for screen in pane.screens)


class Stacking:
    def __init__(self) -> None:
        self._clock = itertools.count(1)
        self._taken: dict[str, int] = {}
        self._last_taken = 0
        self._arranged: tuple[tuple[tuple[Pane, ...], int], list[Screen]] | None = None

    def take(self, name: str) -> None:
        self._last_taken = self._taken[name] = next(self._clock)

    def _when_taken(self, pane: Pane) -> int:
        return max(self._taken.get(screen.name, 0) for screen in pane.screens)

    def arrange(self, panes: Sequence[Pane]) -> list[Screen]:
        asked = (tuple(panes), self._last_taken)
        if self._arranged is None or self._arranged[0] != asked:
            self._arranged = (asked, self._stood_up(panes))
        return list(self._arranged[1])

    def _stood_up(self, panes: Sequence[Pane]) -> list[Screen]:
        waiting = sorted(panes, key=self._when_taken)
        standing: list[Pane] = []
        while waiting:
            pane = next((pane for pane in waiting
                         if not any(_covers(other, pane) for other in waiting if other is not pane)),
                        waiting[0])
            waiting.remove(pane)
            standing.append(pane)
        return [screen for pane in standing for screen in pane.screens]

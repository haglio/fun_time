"""Which screen hangs in front of which."""
from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass

from .pointer import Screen
from .scene import encloses


@dataclass(frozen=True)
class Pane:
    screens: tuple[Screen, ...]
    docked_to: str | None = None


def _covers(pane: Pane, other: Pane) -> bool:
    inner = other.screens[0]
    return any(encloses(screen.placement, inner.placement,
                        outer_aspect=screen.aspect, inner_aspect=inner.aspect)
               for screen in pane.screens)


class Stacking:
    def __init__(self) -> None:
        self._clock = itertools.count(1)
        self._taken: dict[str, int] = {}

    def take(self, name: str) -> None:
        self._taken[name] = next(self._clock)

    def _when_taken(self, pane: Pane) -> int:
        return max(self._taken.get(pane.docked_to, 0),
                   *(self._taken.get(screen.name, 0) for screen in pane.screens))

    def arrange(self, panes: Sequence[Pane]) -> list[Screen]:
        waiting = sorted(panes, key=self._when_taken)
        standing: list[Pane] = []
        while waiting:
            pane = next((pane for pane in waiting
                         if not any(_covers(other, pane) for other in waiting if other is not pane)),
                        waiting[0])
            waiting.remove(pane)
            standing.append(pane)
        return [screen for pane in standing for screen in pane.screens]

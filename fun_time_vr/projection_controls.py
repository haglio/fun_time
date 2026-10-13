from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from funestra.host_controls import BarHud, HostControlsHud

from .projection import PROJECTIONS
from .render import angle_limits, immersive_wrap
from .wrap_readout import label

ANGLE_LABEL = "Angle"

_NAMES = tuple(label(projection) for projection in PROJECTIONS)


def _angle_bar(projection: str, fov_deg: float | None) -> BarHud | None:
    limits = angle_limits(projection)
    if limits is None:
        return None
    return BarHud(ANGLE_LABEL, immersive_wrap(projection, fov_deg=fov_deg).fov_deg, *limits,
                  unit="°")


class ProjectionVerbs(Protocol):
    projection: str
    fov: float | None

    def watch_in(self, projection: str) -> None: ...

    def set_fov(self, degrees: float) -> None: ...


class ProjectionControls:
    def __init__(self, *, verbs: Callable[[], ProjectionVerbs | None],
                 showing: Callable[[], bool]) -> None:
        self._verbs = verbs
        self._showing = showing

    def hud(self) -> HostControlsHud | None:
        verbs = self._verbs()
        if verbs is None or not self._showing():
            return None
        return HostControlsHud(label(verbs.projection), _NAMES,
                               bar=_angle_bar(verbs.projection, verbs.fov))

    def pick(self, index: int) -> None:
        self._verbs().watch_in(PROJECTIONS[index])

    def set_bar(self, value: float) -> None:
        self._verbs().set_fov(value)

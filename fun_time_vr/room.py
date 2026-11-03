from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .pointer import Screen
from .stacking import Pane, Stacking


@dataclass(frozen=True)
class Hanging:
    screen: Screen
    mesh: object | None = None
    frame: object | None = None
    blend: bool = False
    wrap: object | None = None
    forward_with: str = ""
    in_front: bool = False


@dataclass(frozen=True)
class RoomLaunch:
    manifest: Any
    vr: Any
    contexts: Any
    perf: Any = None


@dataclass(frozen=True)
class RoomUnits:
    main_unit: Any
    satellites: Sequence[Any]
    dash: Any
    main_hud_unit: Any
    reference: Any
    library: Any
    flashed: Any
    gallery: Any
    loading_screen: Any

    def in_order(self) -> list:
        return [self.main_unit, *self.satellites, self.dash, self.main_hud_unit, self.reference,
                self.library, self.flashed, self.gallery, self.loading_screen]


@dataclass(frozen=True)
class RoomKeeping:
    messages: Any
    keeper: Any
    posts: Any


def what_hangs(room: Sequence) -> list[Hanging]:
    return [one for thing in room for one in thing.hangings()]


def _panes(hangings: Sequence[Hanging]) -> list[Pane]:
    together: dict[str, list[Hanging]] = {}
    for one in hangings:
        if one.in_front:
            continue
        together.setdefault(one.forward_with or one.screen.name, []).append(one)
    return [Pane(tuple(one.screen for one in group)) for group in together.values()]


def arranged(stacking: Stacking, hangings: Sequence[Hanging]) -> list[Screen]:
    return stacking.arrange(_panes(hangings)) + [
        one.screen for one in hangings if one.in_front]


def wrapped(hangings: Sequence[Hanging]) -> Hanging | None:
    return next((one for one in hangings if one.wrap is not None), None)


def drawn(hangings: Sequence[Hanging], *, screens: Sequence[Screen],
          as_quads: set[str]) -> list[tuple[object, object, bool]]:
    frames = {one.screen.name: one for one in hangings}
    return [(one.mesh.mesh, one.frame.texture, one.blend)
            for one in (frames[screen.name] for screen in screens
                        if not screen.immersive and screen.name not in as_quads)
            if one.mesh is not None and one.mesh.ready]


@dataclass(frozen=True)
class Hangs:
    moves: tuple[object, ...]
    kept_as: Callable[[], str] | None = None

    @property
    def placement(self):
        return self.moves[0].placement

    def put(self, placement) -> None:
        for one in self.moves:
            one.placement = placement

    def spot(self, name: str) -> str:
        return name if self.kept_as is None else self.kept_as()


def where_they_hang(room: Sequence) -> dict[str, Hangs]:
    where: dict[str, Hangs] = {}
    for thing in room:
        for name, hangs in thing.hangs_by().items():
            standing = where.get(name)
            where[name] = hangs if standing is None else Hangs(
                standing.moves + hangs.moves, standing.kept_as or hangs.kept_as)
    return where

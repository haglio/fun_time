"""Everything the headset's room hangs in front of him, said by the things that
hang it there, so the frame loop never names a kind of screen."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .pointer import Screen
from .stacking import Pane, Stacking


@dataclass(frozen=True)
class Hanging:
    screen: Screen
    mesh: object | None = None  # the quad it is drawn on; None while it is round the viewer
    picture: object | None = None
    blend: bool = False  # painted over whatever it covers
    wrap: object | None = None  # the projection it is drawn round the viewer in, instead
    forward_with: str = ""  # comes forward with that screen; "" → on its own
    docked_to: str | None = None  # forward when that screen is, without being part of it
    in_front: bool = False  # over the arrangement, never inside it


def what_hangs(room: Sequence) -> list[Hanging]:
    return [one for thing in room for one in thing.hangings()]


def _panes(hangings: Sequence[Hanging]) -> list[Pane]:
    together: dict[str, list[Hanging]] = {}
    for one in hangings:
        if one.in_front:
            continue
        together.setdefault(one.forward_with or one.screen.name, []).append(one)
    return [Pane(tuple(one.screen for one in group),
                 docked_to=next((one.docked_to for one in group if one.docked_to), None))
            for group in together.values()]


def arranged(stacking: Stacking, hangings: Sequence[Hanging]) -> list[Screen]:
    return stacking.arrange(_panes(hangings)) + [
        one.screen for one in hangings if one.in_front]


def wrapped(hangings: Sequence[Hanging]) -> Hanging | None:
    return next((one for one in hangings if one.wrap is not None), None)


def drawn(hangings: Sequence[Hanging], *, screens: Sequence[Screen],
          as_quads: set[str]) -> list[tuple[object, object, bool]]:
    """In the order *screens* stand, less those the compositor took as quads and
    the one round the viewer, which is drawn on its own."""
    pictures = {one.screen.name: one for one in hangings}
    return [(one.mesh.mesh, one.picture.texture, one.blend)
            for one in (pictures[screen.name] for screen in screens
                        if not screen.immersive and screen.name not in as_quads)
            if one.mesh is not None and one.mesh.ready]


@dataclass(frozen=True)
class Hangs:
    """Where one screen hangs: what a drag on it moves, and the spot that lands in."""

    moves: tuple[object, ...]  # each carries the .placement a drag writes
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

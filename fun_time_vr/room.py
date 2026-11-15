from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .pointer import Frame, Screen
from .stacking import Pane, Stacking

if TYPE_CHECKING:
    import threading

    from fun_time.manifest import LaunchManifest

    from .funestras import VrSettings
    from .gl_contexts import SharedContexts
    from .perf import FramePerf


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
    manifest: LaunchManifest
    vr: VrSettings
    contexts: SharedContexts
    perf: FramePerf | None = None


@runtime_checkable
class Pumped(Protocol):
    def pump(self, stop: threading.Event, now: float) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class RoomUnit(Pumped, Protocol):
    def hangings(self) -> tuple[Hanging, ...]: ...

    def hangs_by(self) -> dict[str, Hangs]: ...

    def put_back(self) -> None: ...

    def point(self, frame: Frame) -> None: ...

    def render_latest_frame(self) -> None: ...


@runtime_checkable
class Scrolls(RoomUnit, Protocol):
    @property
    def takes_the_stick(self) -> bool: ...

    def scroll(self, notches: float) -> None: ...


def what_hangs(room: Sequence[RoomUnit]) -> list[Hanging]:
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


def where_they_hang(room: Sequence[RoomUnit]) -> dict[str, Hangs]:
    where: dict[str, Hangs] = {}
    for thing in room:
        for name, hangs in thing.hangs_by().items():
            standing = where.get(name)
            where[name] = hangs if standing is None else Hangs(
                standing.moves + hangs.moves, standing.kept_as or hangs.kept_as)
    return where

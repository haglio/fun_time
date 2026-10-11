from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from funestra_core.hud_placement import HudEdge

from .console_panel import DEG_PER_PX
from .pointer import HANDLE_DEG, PRESS, PressEvent, Screen
from .room import Hanging
from .satellite_hud import HUD_GAP_DEG
from .scene import Placement, attached_to
from .surfaces import red_and_blue_swapped

SIDE = "side"


@dataclass(frozen=True, eq=False)
class Side:
    edge: HudEdge
    screen: Screen
    bgra: np.ndarray


def side_screen_name(screen_name: str, edge: HudEdge) -> str:
    return f"{screen_name}/{SIDE}/{edge}"


def side_screen_names(screen_name: str) -> tuple[str, ...]:
    return tuple(side_screen_name(screen_name, edge) for edge in HudEdge)


def side_named(name: str) -> HudEdge | None:
    owner, _, edge = name.rpartition("/")
    return HudEdge(edge) if owner.endswith(f"/{SIDE}") and edge in HudEdge else None


def sides(outside, screen_name: str, picture: Placement, aspect: float, *,
          panel_edge: HudEdge) -> tuple[Side, ...]:
    return tuple(_side(outside, screen_name, picture, aspect, edge)
                 for edge in HudEdge if edge is not panel_edge)


def _gap_deg(edge: HudEdge) -> float:
    return HANDLE_DEG + HUD_GAP_DEG if edge is HudEdge.UPPER else HUD_GAP_DEG


def _side(outside, screen_name: str, picture: Placement, aspect: float,
          edge: HudEdge) -> Side:
    bgra = outside.plus_beside(edge).bgra
    height, width = bgra.shape[:2]
    placement = attached_to(edge, picture, aspect=aspect, width_deg=width * DEG_PER_PX,
                            hanging_aspect=width / height, gap_deg=_gap_deg(edge))
    return Side(edge, Screen(side_screen_name(screen_name, edge), placement, width / height,
                             pressable=True), bgra)


class HudSides:
    def __init__(self, screen_name: str, *, post: Callable[[str], None],
                 mesh=None, texture=None) -> None:
        self._screen_name = screen_name
        self._post = post
        self._mesh = mesh
        self._texture = texture
        self.pointed_at: HudEdge | None = None
        self._outside = None
        self._hung: tuple[Side, ...] = ()
        self._shown: Side | None = None

    def hang(self, outside, picture: Placement, aspect: float, *, panel_edge: HudEdge) -> None:
        self._outside = outside
        self._hung = sides(outside, self._screen_name, picture, aspect, panel_edge=panel_edge)
        shown = next((side for side in self._hung if side.edge is self.pointed_at), None)
        if shown is not None:
            if self._shown is None or shown.bgra is not self._shown.bgra:
                self._texture.upload(red_and_blue_swapped(shown.bgra))
            self._mesh.rehang_at(shown.screen.placement, shown.screen.aspect)
        self._shown = shown

    def unhang(self) -> None:
        self._hung, self._shown = (), None

    def hangings(self) -> tuple[Hanging, ...]:
        return tuple(
            Hanging(side.screen, blend=True, forward_with=self._screen_name)
            if side is not self._shown else
            Hanging(side.screen, mesh=self._mesh, picture=self._texture, blend=True,
                    forward_with=self._screen_name)
            for side in self._hung)

    def point(self, screen_name: str | None) -> None:
        self.pointed_at = None if screen_name is None else self._mine(screen_name)

    def take(self, event: PressEvent) -> bool:
        edge = self._mine(event.screen)
        if edge is None:
            return False
        if event.kind == PRESS:
            self._post(self._outside.plus_beside(edge).command)
        return True

    def _mine(self, screen_name: str) -> HudEdge | None:
        edge = side_named(screen_name)
        if edge is None or screen_name != side_screen_name(self._screen_name, edge):
            return None
        return edge

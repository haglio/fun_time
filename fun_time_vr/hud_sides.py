from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from player_core.hud_corners import plus_bgra, plus_button
from player_core.hud_placement import HudCorner, HudEdge

from .console_panel import DEG_PER_PX
from .pointer import HANDLE_DEG, PRESS, PressEvent, Screen
from .room import Hanging
from .satellite_hud import HUD_GAP_DEG
from .scene import Placement, attached_to

SIDE = "side"

_BUTTON_NEAREST_THE_PICTURE = {
    HudEdge.LEFT: HudCorner.UPPER_RIGHT,
    HudEdge.RIGHT: HudCorner.UPPER_LEFT,
    HudEdge.UPPER: HudCorner.LOWER_LEFT,
    HudEdge.LOWER: HudCorner.UPPER_LEFT,
}


@dataclass(frozen=True)
class Side:
    edge: HudEdge
    screen: Screen
    rgba: np.ndarray


def side_screen_name(screen_name: str, edge: HudEdge) -> str:
    return f"{screen_name}/{SIDE}/{edge}"


def side_screen_names(screen_name: str) -> tuple[str, ...]:
    return tuple(side_screen_name(screen_name, edge) for edge in HudEdge)


def side_named(name: str) -> HudEdge | None:
    owner, _, edge = name.rpartition("/")
    return HudEdge(edge) if owner.endswith(f"/{SIDE}") and edge in HudEdge else None


def sides(player: str, screen_name: str, picture: Placement, aspect: float, *,
          panel_edge: HudEdge, minimized: bool) -> tuple[Side, ...]:
    return tuple(_side(player, screen_name, picture, aspect, edge, minimized=minimized)
                 for edge in HudEdge if edge is not panel_edge)


def _gap_deg(edge: HudEdge) -> float:
    return HANDLE_DEG + HUD_GAP_DEG if edge is HudEdge.UPPER else HUD_GAP_DEG


@lru_cache(maxsize=32)
def _plus_rgba(player: str, edge: HudEdge, minimized: bool) -> np.ndarray:
    bgra = plus_bgra(plus_button(player, edge, minimized=minimized),
                     _BUTTON_NEAREST_THE_PICTURE[edge])
    return np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]])


def _side(player: str, screen_name: str, picture: Placement, aspect: float,
          edge: HudEdge, *, minimized: bool) -> Side:
    rgba = _plus_rgba(player, edge, minimized)
    height, width = rgba.shape[:2]
    placement = attached_to(edge, picture, aspect=aspect, width_deg=width * DEG_PER_PX,
                            hanging_aspect=width / height, gap_deg=_gap_deg(edge))
    return Side(edge, Screen(side_screen_name(screen_name, edge), placement, width / height,
                             pressable=True), rgba)


class HudSides:
    def __init__(self, player: str, screen_name: str, *, post: Callable[[str], None],
                 mesh=None, texture=None) -> None:
        self._player = player
        self._screen_name = screen_name
        self._post = post
        self._mesh = mesh
        self._texture = texture
        self.pointed_at: HudEdge | None = None
        self._hung: tuple[Side, ...] = ()
        self._shown: Side | None = None

    def hang(self, picture: Placement, aspect: float, *, panel_edge: HudEdge,
             minimized: bool) -> None:
        self._hung = sides(self._player, self._screen_name, picture, aspect,
                           panel_edge=panel_edge, minimized=minimized)
        shown = next((side for side in self._hung if side.edge is self.pointed_at), None)
        if shown is not None:
            if self._shown is None or shown.rgba is not self._shown.rgba:
                self._texture.upload(shown.rgba)
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
            self._post(plus_button(self._player, edge, minimized=False).command)
        return True

    def _mine(self, screen_name: str) -> HudEdge | None:
        edge = side_named(screen_name)
        if edge is None or screen_name != side_screen_name(self._screen_name, edge):
            return None
        return edge

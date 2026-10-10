from __future__ import annotations

from collections.abc import Callable

from player_core.hud_placement import HudEdge

from .hud_sides import HudSides, side_screen_names
from .hud_toggle import HudToggle, toggle_screen_name
from .pointer import PressEvent
from .room import Hanging
from .scene import Placement


def hud_button_names(screen_name: str) -> tuple[str, ...]:
    return (*side_screen_names(screen_name), toggle_screen_name(screen_name))


class HudButtons:
    def __init__(self, player: str, screen_name: str, *, post: Callable[[str], None],
                 texture: Callable[[], object], mesh: Callable[[], object]) -> None:
        side_texture, toggle_texture, tip_texture = texture(), texture(), texture()
        side_mesh, toggle_mesh, tip_mesh = mesh(), mesh(), mesh()
        self._made = (side_texture, toggle_texture, tip_texture, side_mesh, toggle_mesh, tip_mesh)
        self._sides = HudSides(player, screen_name, post=post, mesh=side_mesh, texture=side_texture)
        self._toggle = HudToggle(player, screen_name, post=post, mesh=toggle_mesh,
                                 texture=toggle_texture, tip_mesh=tip_mesh,
                                 tip_texture=tip_texture)

    def hang(self, picture: Placement, aspect: float, *, edge: HudEdge, minimized: bool,
             with_sides: bool = True) -> None:
        self._toggle.hang(picture, aspect, edge=edge, minimized=minimized)
        if with_sides:
            self._sides.hang(picture, aspect, panel_edge=edge, minimized=minimized)
        else:
            self._sides.unhang()

    def unhang(self) -> None:
        self._toggle.unhang()
        self._sides.unhang()

    def hangings(self) -> tuple[Hanging, ...]:
        return (*self._sides.hangings(), *self._toggle.hangings())

    def point(self, screen_name: str | None) -> None:
        self._sides.point(screen_name)
        self._toggle.point(screen_name)

    def take(self, event: PressEvent) -> bool:
        return self._sides.take(event) or self._toggle.take(event)

    def close(self) -> None:
        for made in self._made:
            made.close()

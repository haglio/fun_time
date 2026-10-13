from __future__ import annotations

from collections.abc import Callable

import numpy as np
from funestra.hud_placement import HudEdge
from shared_ui.spacing import BUTTON_SIZE_HUD

from .console_panel import DEG_PER_PX
from .pointer import PRESS, PressEvent, Screen
from .room import Hanging
from .satellite_hud import HUD_GAP_DEG
from .scene import Placement, attached_to
from .surfaces import red_and_blue_swapped

TOGGLE = "toggle"
TIP = "tip"
BUTTON_DEG = BUTTON_SIZE_HUD * DEG_PER_PX
PANEL_GAP_DEG = HUD_GAP_DEG + BUTTON_DEG + HUD_GAP_DEG

_TIP_GAP_DEG = HUD_GAP_DEG / 2


def toggle_screen_name(screen_name: str) -> str:
    return f"{screen_name}/{TOGGLE}"


def _beside(button: Placement, edge: HudEdge, tip: np.ndarray) -> Placement:
    height, width = tip.shape[:2]
    width_deg = width * DEG_PER_PX
    if edge in (HudEdge.UPPER, HudEdge.LOWER):
        step = button.width_deg / 2 + _TIP_GAP_DEG + width_deg / 2
        return Placement(button.azimuth_deg + step, button.elevation_deg, width_deg)
    step = button.width_deg / 2 + _TIP_GAP_DEG + height * DEG_PER_PX / 2
    return Placement(button.azimuth_deg, button.elevation_deg - step, width_deg)


class HudToggle:
    def __init__(self, screen_name: str, *, post: Callable[[str], None],
                 mesh=None, texture=None, tip_mesh=None, tip_texture=None) -> None:
        self._screen_name = screen_name
        self._post = post
        self._mesh, self._texture = mesh, texture
        self._tip_mesh, self._tip_texture = tip_mesh, tip_texture
        self._pointed = False
        self._outside = None
        self._button: Screen | None = None
        self._tip: Screen | None = None
        self._painted: np.ndarray | None = None
        self._tip_painted: np.ndarray | None = None

    def hang(self, outside, picture: Placement, aspect: float, *, edge: HudEdge) -> None:
        self._outside = outside
        placement = attached_to(edge, picture, aspect=aspect, width_deg=BUTTON_DEG,
                                hanging_aspect=1.0, gap_deg=HUD_GAP_DEG)
        self._button = Screen(toggle_screen_name(self._screen_name), placement, 1.0,
                              pressable=True)
        painted = outside.toggle(pointed=self._pointed).bgra
        if painted is not self._painted:
            self._texture.upload(red_and_blue_swapped(painted))
            self._painted = painted
        self._mesh.rehang_at(placement, 1.0)
        self._tip = None
        tip = outside.toggle_tip() if self._pointed else outside.name_flash()
        if tip is not None:
            beside = _beside(placement, edge, tip)
            self._tip = Screen(f"{toggle_screen_name(self._screen_name)}/{TIP}", beside,
                               tip.shape[1] / tip.shape[0], pressable=False)
            if tip is not self._tip_painted:
                self._tip_texture.upload(red_and_blue_swapped(tip))
                self._tip_painted = tip
            self._tip_mesh.rehang_at(beside, self._tip.aspect)

    def unhang(self) -> None:
        self._button = self._tip = None

    def hangings(self) -> tuple[Hanging, ...]:
        if self._button is None:
            return ()
        button = Hanging(self._button, mesh=self._mesh, picture=self._texture, blend=True,
                         forward_with=self._screen_name)
        if self._tip is None:
            return (button,)
        return (button, Hanging(self._tip, mesh=self._tip_mesh, picture=self._tip_texture,
                                blend=True, forward_with=self._screen_name))

    def point(self, screen_name: str | None) -> None:
        self._pointed = screen_name == toggle_screen_name(self._screen_name)

    def take(self, event: PressEvent) -> bool:
        if event.screen != toggle_screen_name(self._screen_name):
            return False
        if event.kind == PRESS:
            self._post(self._outside.toggle(pointed=True).command)
        return True

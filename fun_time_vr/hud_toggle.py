from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw
from player_core.hud_corners import tooltip_size
from player_core.hud_minimize import BUTTON, mark_font, minimize_button, restore_button
from player_core.hud_panel import draw_button, draw_tooltip
from player_core.hud_placement import HudEdge

from .console_panel import DEG_PER_PX
from .pointer import PRESS, PressEvent, Screen
from .room import Hanging
from .satellite_hud import HUD_GAP_DEG
from .scene import Placement, attached_to

TOGGLE = "toggle"
TIP = "tip"
BUTTON_DEG = BUTTON * DEG_PER_PX
PANEL_GAP_DEG = HUD_GAP_DEG + BUTTON_DEG + HUD_GAP_DEG

_TIP_GAP_DEG = HUD_GAP_DEG / 2


def toggle_screen_name(screen_name: str) -> str:
    return f"{screen_name}/{TOGGLE}"


def _button(player: str, minimized: bool):
    return restore_button(player) if minimized else minimize_button(player)


@lru_cache(maxsize=16)
def _button_rgba(player: str, minimized: bool, pointed: bool) -> np.ndarray:
    image = Image.new("RGBA", (BUTTON, BUTTON), (0, 0, 0, 0))
    draw_button(image, ImageDraw.Draw(image), (0, 0, BUTTON, BUTTON), _button(player, minimized),
                hovered=pointed, glyph_font=mark_font(), word_font=mark_font())
    return np.asarray(image).copy()


@lru_cache(maxsize=8)
def _tip_rgba(text: str) -> np.ndarray:
    size = tooltip_size(text)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_tooltip(ImageDraw.Draw(image), mark_font(), text, (0, 0), size)
    return np.asarray(image).copy()


def _beside(button: Placement, edge: HudEdge, tip: np.ndarray) -> Placement:
    height, width = tip.shape[:2]
    width_deg = width * DEG_PER_PX
    if edge in (HudEdge.UPPER, HudEdge.LOWER):
        step = button.width_deg / 2 + _TIP_GAP_DEG + width_deg / 2
        return Placement(button.azimuth_deg + step, button.elevation_deg, width_deg)
    step = button.width_deg / 2 + _TIP_GAP_DEG + height * DEG_PER_PX / 2
    return Placement(button.azimuth_deg, button.elevation_deg - step, width_deg)


class HudToggle:
    def __init__(self, player: str, screen_name: str, *, post: Callable[[str], None],
                 mesh=None, texture=None, tip_mesh=None, tip_texture=None) -> None:
        self._player = player
        self._screen_name = screen_name
        self._post = post
        self._mesh, self._texture = mesh, texture
        self._tip_mesh, self._tip_texture = tip_mesh, tip_texture
        self._pointed = False
        self._minimized = False
        self._button: Screen | None = None
        self._tip: Screen | None = None
        self._painted: tuple | None = None
        self._tip_painted: str | None = None

    def hang(self, picture: Placement, aspect: float, *, edge: HudEdge,
             minimized: bool) -> None:
        self._minimized = minimized
        placement = attached_to(edge, picture, aspect=aspect, width_deg=BUTTON_DEG,
                                hanging_aspect=1.0, gap_deg=HUD_GAP_DEG)
        self._button = Screen(toggle_screen_name(self._screen_name), placement, 1.0,
                              pressable=True)
        painted = (minimized, self._pointed)
        if painted != self._painted:
            self._texture.upload(_button_rgba(self._player, minimized, self._pointed))
            self._painted = painted
        self._mesh.rehang_at(placement, 1.0)
        self._tip = None
        if self._pointed:
            text = _button(self._player, minimized).tooltip
            tip = _tip_rgba(text)
            beside = _beside(placement, edge, tip)
            self._tip = Screen(f"{toggle_screen_name(self._screen_name)}/{TIP}", beside,
                               tip.shape[1] / tip.shape[0], pressable=False)
            if text != self._tip_painted:
                self._tip_texture.upload(tip)
                self._tip_painted = text
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
            self._post(_button(self._player, self._minimized).command)
        return True

from __future__ import annotations

from PIL import ImageDraw
from shared_ui.palette import BG_BUTTON, TEXT_MUTED, hovered
from shared_ui.spacing import BUTTON_RADIUS_HUD

from fun_time.dashboard_layout import Rect


def slab(pen: ImageDraw.ImageDraw, rect: Rect, ground, edge) -> None:
    pen.rounded_rectangle(
        (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
        radius=BUTTON_RADIUS_HUD, fill=(*ground, 255), outline=(*edge, 255),
    )


def button(pen: ImageDraw.ImageDraw, rect: Rect, fill, *, lit: bool) -> None:
    edge = TEXT_MUTED if fill == BG_BUTTON else fill
    slab(pen, rect, hovered(fill) if lit else fill, edge)

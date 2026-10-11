from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from funestra.hud_panel import load_font
from PIL import Image, ImageDraw
from shared_ui.palette import TEXT_MUTED, TEXT_PRIMARY
from shared_ui.spacing import BUTTON_GAP, BUTTON_PAD_H_TIGHT, BUTTON_SIZE_HUD

from fun_time.dashboard_layout import Rect

from .dropdown import ARROW_PX, paint_field, paint_list
from .projection import PROJECTIONS
from .render import angle_limits, immersive_wrap
from .wrap_readout import label

ANGLE_LABEL = "Angle"
HEIGHT = BUTTON_SIZE_HUD

_FONT = load_font(8)
_FIELD_W = round(BUTTON_PAD_H_TIGHT + max(_FONT.getlength(label(p)) for p in PROJECTIONS)
                 + BUTTON_GAP + ARROW_PX + BUTTON_GAP)
_GROUP_GAP = 12
_ANGLE_WORDS_W = round(_FONT.getlength(ANGLE_LABEL) + BUTTON_GAP + _FONT.getlength("000°"))
_LEAST_BAR_W = 60
_TRACK_PX = 4


@dataclass(frozen=True)
class Angle:
    degrees: float
    low: float
    high: float

    @property
    def share(self) -> float:
        return (self.degrees - self.low) / (self.high - self.low)

    def at(self, share: float) -> float:
        return self.low + min(1.0, max(0.0, share)) * (self.high - self.low)


def _angle_for(projection: str, fov_deg: float | None) -> Angle | None:
    limits = angle_limits(projection)
    if limits is None:
        return None
    return Angle(immersive_wrap(projection, fov_deg=fov_deg).fov_deg, *limits)


@dataclass(frozen=True)
class ProjectionControls:
    projection: str
    angle: Angle | None
    list_open: bool = False

    height = HEIGHT
    least_width = _FIELD_W + _GROUP_GAP + _ANGLE_WORDS_W + BUTTON_GAP + _LEAST_BAR_W

    def draw(self, image: Image.Image, rect: tuple[int, int, int, int]) -> None:
        block = Rect(*rect)
        pen = ImageDraw.Draw(image)
        field = field_at(block)
        paint_field(image, field, label(self.projection), _FONT)
        if self.angle is not None:
            bar = bar_at(block)
            middle = bar.y + bar.height / 2
            pen.text((field.x + field.width + _GROUP_GAP, middle), ANGLE_LABEL, font=_FONT,
                     anchor="lm", fill=(*TEXT_MUTED, 255))
            pen.text((bar.x - BUTTON_GAP, middle), f"{self.angle.degrees:.0f}°", font=_FONT,
                     anchor="rm", fill=(*TEXT_PRIMARY, 255))
            _draw_bar(pen, bar, self.angle.share)
        if self.list_open:
            paint_list(image, [(stop, label(projection), projection == self.projection)
                               for projection, stop in stops_at(block).items()], _FONT)


def _draw_bar(pen: ImageDraw.ImageDraw, bar: Rect, share: float) -> None:
    middle = bar.y + bar.height // 2
    upper, lower = middle - _TRACK_PX // 2, middle + _TRACK_PX // 2
    pen.rounded_rectangle((bar.x, upper, bar.x + bar.width - 1, lower),
                          radius=_TRACK_PX // 2, fill=(*TEXT_MUTED, 255))
    reach = round(share * bar.width)
    if reach > 0:
        pen.rounded_rectangle((bar.x, upper, bar.x + reach - 1, lower),
                              radius=_TRACK_PX // 2, fill=(*TEXT_PRIMARY, 255))


def field_at(block: Rect) -> Rect:
    return Rect(block.x, block.y, _FIELD_W, block.height)


def bar_at(block: Rect) -> Rect:
    x = block.x + _FIELD_W + _GROUP_GAP + _ANGLE_WORDS_W + BUTTON_GAP
    return Rect(x, block.y, block.x + block.width - x, block.height)


def stops_at(block: Rect) -> dict[str, Rect]:
    field = field_at(block)
    return {
        projection: Rect(field.x, field.y - (len(PROJECTIONS) - index) * field.height,
                         field.width, field.height)
        for index, projection in enumerate(PROJECTIONS)
    }


def _on(rect: Rect, x: int, y: int) -> bool:
    return rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height


class ProjectionVerbs(Protocol):
    projection: str
    fov: float | None

    def watch_in(self, projection: str) -> None: ...

    def set_fov(self, degrees: float) -> None: ...


class ProjectionPointer:
    def __init__(self, *, verbs: Callable[[], ProjectionVerbs | None],
                 showing: Callable[[], bool]) -> None:
        self._verbs = verbs
        self._showing = showing
        self._controls: ProjectionControls | None = None
        self.holding = False

    def drawing(self) -> ProjectionControls | None:
        verbs = self._verbs()
        if verbs is None or not self._showing():
            self._controls, self.holding = None, False
            return None
        projection = verbs.projection
        open_ = self._controls is not None and self._controls.list_open
        self._controls = ProjectionControls(projection, _angle_for(projection, verbs.fov),
                                            list_open=open_)
        return self._controls

    def press(self, px: int, py: int, *, rect: tuple[int, int, int, int] | None) -> bool:
        controls = self._controls
        if controls is None or rect is None:
            return False
        block = Rect(*rect)
        if controls.list_open:
            for projection, stop in stops_at(block).items():
                if _on(stop, px, py):
                    self._verbs().watch_in(projection)
            self._controls = replace(controls, list_open=False)
            return True
        if _on(field_at(block), px, py):
            self._controls = replace(controls, list_open=True)
            return True
        if controls.angle is not None and _on(bar_at(block), px, py):
            self.holding = True
            return self.drag_to(px, py, rect=rect)
        return False

    def drag_to(self, px: int, _py: int, *, rect: tuple[int, int, int, int] | None) -> bool:
        if not self.holding or rect is None:
            return False
        bar = bar_at(Rect(*rect))
        self._verbs().set_fov(self._controls.angle.at((px - bar.x) / bar.width))
        return True

    def release(self) -> None:
        self.holding = False

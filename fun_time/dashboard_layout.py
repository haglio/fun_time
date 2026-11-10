from __future__ import annotations

from dataclasses import dataclass, fields

from shared_ui.spacing import (
    BUTTON_GAP,
    BUTTON_GROUP_GAP,
    BUTTON_SIZE_HUD,
    GAP_SMALL,
    MARGIN_STANDARD,
)


@dataclass(frozen=True)
class Size:
    width: int
    height: int


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def __iter__(self):
        return iter((self.x, self.y, self.width, self.height))

    def holds(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def add_rect_arguments(parser, *, prefix: str = "") -> None:
    for field in ("x", "y", "width", "height"):
        parser.add_argument(f"--{prefix}{field}".replace("_", "-"), type=int)


def rect_from_arguments(args, *, prefix: str = "") -> Rect | None:
    values = [getattr(args, f"{prefix}{field}") for field in ("x", "y", "width", "height")]
    return None if None in values else Rect(*values)


def client_rect_filling_frame(
    rect: Rect, *, left: int, upper: int, right: int, lower: int
) -> tuple[int, int, int, int]:
    # QWidget.setGeometry positions the client area, and the window manager draws the frame outside
    # it.
    return (
        rect.x + left,
        rect.y + upper,
        max(0, rect.width - left - right),
        max(0, rect.height - upper - lower),
    )


BUTTON = BUTTON_SIZE_HUD
GAP = BUTTON_GAP
GROUP_GAP = BUTTON_GROUP_GAP
PAD = MARGIN_STANDARD
APP_ICON = 24
APP_TITLE_W = 108
LOG_LIST_HEIGHT = 144
LOG_HEIGHT = MARGIN_STANDARD + BUTTON + GAP_SMALL + LOG_LIST_HEIGHT + MARGIN_STANDARD


@dataclass(frozen=True)
class DashboardBarLayout:
    height: int
    app_icon: Rect
    app_title: Rect
    quit_button: Rect
    omnipause_button: Rect
    help_button: Rect
    microphone_button: Rect
    fmode_button: Rect
    reset_all_button: Rect
    vr_button: Rect
    vr_reset_button: Rect

    @property
    def every_rect(self) -> tuple[Rect, ...]:
        placed = (getattr(self, field.name) for field in fields(self))
        return tuple(rect for rect in placed if isinstance(rect, Rect))

    @property
    def width(self) -> int:
        return max(rect.x + rect.width for rect in self.every_rect) + GROUP_GAP


def compute_dashboard_bar_layout() -> DashboardBarLayout:
    height = PAD * 2 + BUTTON
    def mid(size):
        return PAD + (BUTTON - size) // 2

    x = PAD
    app_icon = Rect(x, mid(APP_ICON), APP_ICON, APP_ICON)
    x += APP_ICON + GAP
    app_title = Rect(x, PAD, APP_TITLE_W, BUTTON)
    x += APP_TITLE_W + GROUP_GAP

    buttons = []
    for _ in range(4):
        buttons.append(Rect(x, PAD, BUTTON, BUTTON))
        x += BUTTON + GAP

    x += GROUP_GAP - GAP
    fmode_button = Rect(x, PAD, BUTTON, BUTTON)
    x += BUTTON + GAP
    reset_all_button = Rect(x, PAD, BUTTON, BUTTON)
    x += BUTTON + GROUP_GAP
    vr_button = Rect(x, PAD, BUTTON, BUTTON)
    x += BUTTON + GAP
    vr_reset_button = Rect(x, PAD, BUTTON, BUTTON)

    return DashboardBarLayout(
        height=height,
        app_icon=app_icon,
        app_title=app_title,
        quit_button=buttons[0],
        omnipause_button=buttons[1],
        help_button=buttons[2],
        microphone_button=buttons[3],
        fmode_button=fmode_button,
        reset_all_button=reset_all_button,
        vr_button=vr_button,
        vr_reset_button=vr_reset_button,
    )


RFB_MODE_SELECTOR_HEIGHT = PAD * 2 + BUTTON


def dashboard_window_height() -> int:
    return compute_dashboard_bar_layout().height + LOG_HEIGHT + RFB_MODE_SELECTOR_HEIGHT

from __future__ import annotations

from dataclasses import dataclass


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


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


def client_rect_filling_frame(
    rect: Rect, *, left: int, top: int, right: int, bottom: int
) -> tuple[int, int, int, int]:
    """Client ``(x, y, w, h)`` so a decorated window's whole FRAME fills *rect*.

    ``QWidget.setGeometry`` positions the *client* area; the window manager draws
    the title bar and borders outside it, so setting the client to *rect* leaves
    the title bar overhanging *rect*'s top by its height.  Insetting the client
    by the frame margins drops it down and shrinks it to match, so the decorated
    window — chrome included — occupies exactly *rect*.  Zero margins (an
    undecorated window) leave *rect* unchanged.
    """
    return (
        rect.x + left,
        rect.y + top,
        max(0, rect.width - left - right),
        max(0, rect.height - top - bottom),
    )


# --- the control bar ---------------------------------------------------------
# The dashboard used to draw a schematic of both monitors, with a box per player
# carrying that player's buttons and a cable running to the OSR2.  Every player
# draws its own HUD now and those buttons are on it, so what is left is the
# handful that belong to no player: quit, pause everything, the reference popup,
# and the F-mode and voice lights.  (The broker light went to the primary's HUD
# with the rest of the OSR2 status — it is the primary's concern, not the room's.)
# They do not need to be arranged like the room.

BUTTON = 26          # a control in the bar
CHIP = 22            # a status light — smaller, since it is read not pressed
GAP = 8              # between controls
GROUP_GAP = 22       # between the buttons and the chips
PAD = 10             # inset from the bar's edges
APP_ICON = 24
APP_TITLE_W = 108
# The log stream below the bar.  Shorter than it was: the filter controls moved up
# into the bar's row, so this is the list alone, and the Dash is that much shorter
# and the browser below it that much taller.
LOG_HEIGHT = 160


@dataclass(frozen=True)
class DashboardBarLayout:
    """Where each control sits in the bar, and how tall the bar is.

    Only the height is fixed: the bar spans whatever width the window has, and
    the log fills everything below it.
    """

    height: int
    app_icon: Rect
    app_title: Rect
    quit_button: Rect
    omnipause_button: Rect
    help_button: Rect
    fmode_panel: Rect
    voice_panel: Rect

    @property
    def content_width(self) -> int:
        """How wide the bar's own buttons run — the width it takes in its row,
        leaving the rest to the log's filter controls beside it."""
        return self.voice_panel.x + self.voice_panel.width + PAD


def compute_dashboard_bar_layout() -> DashboardBarLayout:
    """The control bar, laid out left to right at its natural size.

    The app's own name and mark lead, then the three buttons, then the three
    chips — pressable things together, readable things together, rather than in
    the shape of the monitors they used to stand for.
    """
    height = PAD * 2 + BUTTON
    mid = lambda size: PAD + (BUTTON - size) // 2  # noqa: E731 — vertical centring

    x = PAD
    app_icon = Rect(x, mid(APP_ICON), APP_ICON, APP_ICON)
    x += APP_ICON + GAP
    app_title = Rect(x, PAD, APP_TITLE_W, BUTTON)
    x += APP_TITLE_W + GROUP_GAP

    buttons = []
    for _ in range(3):
        buttons.append(Rect(x, PAD, BUTTON, BUTTON))
        x += BUTTON + GAP
    x += GROUP_GAP - GAP

    chips = []
    for _ in range(2):
        chips.append(Rect(x, mid(CHIP), CHIP, CHIP))
        x += CHIP + GAP

    return DashboardBarLayout(
        height=height,
        app_icon=app_icon,
        app_title=app_title,
        quit_button=buttons[0],
        omnipause_button=buttons[1],
        help_button=buttons[2],
        fmode_panel=chips[0],
        voice_panel=chips[1],
    )


def dashboard_window_height() -> int:
    """How tall the dashboard window stands: the bar, then the log under it.

    It used to be as tall as a scale drawing of the taller monitor, with the log
    squeezed into the strip beside that drawing.  Without the drawing the log
    takes the full width and the window is a fraction of the height, which the
    Random Favs Browser below it inherits.
    """
    return compute_dashboard_bar_layout().height + LOG_HEIGHT

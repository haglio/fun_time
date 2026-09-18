from __future__ import annotations

from dataclasses import dataclass

from shared_ui.palette import BLUE, GREEN, MAGENTA, TEXT_MUTED, TEXT_PRIMARY
from shared_ui.spacing import BUTTON_MARK_INSET_HUD

from fun_time.dashboard_actions import (
    ENTER_VR,
    EXIT_VR,
    FMODE_TOGGLE,
    HELP_REFERENCE,
    OMNIPAUSE_TOGGLE,
    QUIT_BUTTON,
    RESET_ALL,
    VOICE_TOGGLE,
    VR_RESET,
)
from fun_time.dashboard_layout import DashboardBarLayout, Rect

Color = tuple[int, int, int]


@dataclass(frozen=True)
class BarControl:
    action: str
    rect: Rect
    mark: str
    ink: Color = TEXT_PRIMARY
    lit: Color | None = None
    # Faded and unpressable: the act it offers would change nothing.
    dim: bool = False


def mark_side(rect: Rect) -> int:
    return min(rect.width, rect.height) - 2 * BUTTON_MARK_INSET_HUD


def bar_controls(
    layout: DashboardBarLayout, *, omni_paused: bool = False, voice_active: bool = False,
    f_mode: bool = False, in_vr: bool = False, reference_open: bool = False,
    nothing_to_reset: bool = False,
) -> tuple[BarControl, ...]:
    return (
        BarControl(QUIT_BUTTON, layout.quit_button, "power"),
        BarControl(OMNIPAUSE_TOGGLE, layout.omnipause_button,
                   "play" if omni_paused else "pause"),
        BarControl(HELP_REFERENCE, layout.help_button, "question",
                   lit=BLUE if reference_open else None),
        BarControl(VOICE_TOGGLE, layout.voice_panel, "mic",
                   lit=BLUE if voice_active else None),
        BarControl(FMODE_TOGGLE, layout.fmode_button, "fmode", ink=MAGENTA,
                   lit=GREEN if f_mode else None),
        BarControl(RESET_ALL, layout.reset_all_button, "reset",
                   ink=TEXT_MUTED if nothing_to_reset else TEXT_PRIMARY,
                   dim=nothing_to_reset),
        BarControl(EXIT_VR if in_vr else ENTER_VR, layout.vr_button,
                   "monitor" if in_vr else "headset"),
        *((BarControl(VR_RESET, layout.vr_reset_button, "reset"),) if in_vr else ()),
    )

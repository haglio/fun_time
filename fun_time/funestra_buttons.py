"""What the Main Funestra's HUD and each satellite's HUD have of their own beside
:mod:`funestra.hud_controls`: the noun, and minimize, which needs a window."""
from __future__ import annotations

from funestra.hud_button import Button
from funestra.hud_icons import MINIMIZE_ICON

NOUN = "video"


def minimize_button(funestra: str) -> Button:
    return Button(f"{funestra}_minimize", MINIMIZE_ICON,
                  "Minimize this Funestra — bring it back from the taskbar", group_break=True)

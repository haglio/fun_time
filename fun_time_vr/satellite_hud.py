"""A satellite's HUD in the headset hangs under its picture as a screen of its own."""
from __future__ import annotations

PICTURE = "picture"
HUD = "hud"
HUD_GAP_DEG = 0.6


def hud_screen_name(funestra: str) -> str:
    return f"{funestra}/{HUD}"


def screen_kind(name: str) -> str:
    return HUD if name.endswith(f"/{HUD}") else PICTURE

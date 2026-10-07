from __future__ import annotations

import logging
import warnings
from pathlib import Path

logger = logging.getLogger(__name__)


def wear_the_icon(pygame, icon: Path | None) -> None:
    # Before set_mode: SDL takes the icon from the display as it makes the window.
    surface = _icon_surface(pygame, icon)
    if surface is not None:
        pygame.display.set_icon(surface)


def _icon_surface(pygame, icon: Path | None):
    if icon is None or not icon.exists():
        return None
    try:
        from PIL import Image  # noqa: PLC0415  (optional: no Pillow, no icon)
        image = Image.open(icon).convert("RGBA")
        return pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
    except Exception:
        logger.debug("No window icon: %s could not be read", icon, exc_info=True)
        return None


def take_outside_resizes(pygame) -> None:
    # SDL pins a borderless window that is not resizable to its own idea of its
    # size in WM_NCCALCSIZE, so a resize from Fun Time moved the window without
    # growing or shrinking what it draws, and mpv drew on at the old size.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pygame.Window.from_display_module().resizable = True

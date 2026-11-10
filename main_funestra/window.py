from __future__ import annotations

import ctypes
import logging
import warnings
from pathlib import Path

from funestra.sdl_hints import deliver_the_focusing_click

logger = logging.getLogger(__name__)

FRAMES_A_SECOND = 60


_ALL_THREADS_OF_THIS_PROCESS = 0xFFFFFFFF


def before_the_window_exists() -> None:
    deliver_the_focusing_click()
    _leave_out_the_input_methods_that_stall_band_changes()


def _leave_out_the_input_methods_that_stall_band_changes() -> None:
    ctypes.WinDLL("imm32").ImmDisableIME(_ALL_THREADS_OF_THIS_PROCESS)


def replace_pygames_logo_with(pygame, icon: Path | None) -> None:
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


def run_until_stopped(funestra, *, window_size, events, deal, pace) -> None:
    while not funestra.stopped:
        window = window_size()
        deal(events(), window)
        funestra.tick(viewport=window)
        pace()
    funestra.close()


def run_the_window(pygame, funestra, deal) -> None:
    clock = pygame.time.Clock()
    run_until_stopped(funestra, window_size=pygame.display.get_window_size, events=pygame.event.get,
                      deal=deal, pace=lambda: clock.tick(FRAMES_A_SECOND))
    pygame.quit()

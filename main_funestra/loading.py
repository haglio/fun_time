"""The Main Funestra's window while the library is being read.

The library walk runs before there is a video, long enough on a big library
that a double-click with nothing on screen reads as nothing happening.  The
window opens first and this paints the wait into it, with the family's loading
panel on a surface.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pygame
from shared_ui.loading_panel import LoadingPanel, icon_image, render
from shared_ui.palette import LOADING_GROUND

from .library_source import PHASE_DISCOVER

# What the library build's phases are called on screen.  The build reports
# phase keys and the wording lives here, so the layer that does the waiting
# never carries display text.
_MESSAGES = {
    PHASE_DISCOVER: "Finding videos...",
}


def progress_text(phase: str) -> str:
    return _MESSAGES.get(phase, phase)


def quit_requested(events) -> bool:
    return any(event.type == pygame.QUIT for event in events)


def stop_if_asked() -> None:
    """Pump the window's event queue, and raise if the user gave up.

    Two jobs in one line each, both owed every update: pumping is what keeps
    Windows from graying the window out as unresponsive during a long scan, and
    noticing the close button is what lets the scan be abandoned at all --
    `main_funestra.app` catches `LoadingCanceled` and exits 0 without ever opening a
    video.
    """
    if quit_requested(pygame.event.get()):
        raise LoadingCanceled


class LoadingCanceled(Exception):
    """The user closed the loading window before the library finished."""


def paint_panel(surface, panel: LoadingPanel) -> None:
    """The family's loading panel in the middle of *surface*, on its own ground."""
    image = render(panel)
    surface.fill(LOADING_GROUND)
    painted = pygame.image.frombuffer(image.tobytes(), image.size, "RGB")
    surface.blit(painted, painted.get_rect(center=surface.get_rect().center))


class LoadingScreen:
    """Paints the library wait into the Main Funestra's window, and lets the user out of it.

    Also the Main Funestra's progress callback: ``update`` has the signature
    :func:`main_funestra.library_source.build_library_source` reports through, so the
    screen is handed straight to the build.
    """

    def __init__(self, surface, icon: Path | None = None) -> None:
        self._surface = surface
        self._panel = LoadingPanel(wordmark="Main Funestra", status="", icon=icon_image(icon))

    def update(self, phase: str) -> None:
        """Progress callback: paint the phase, and raise if the user gave up."""
        stop_if_asked()
        self._panel = replace(self._panel, status=progress_text(phase))
        paint_panel(self._surface, self._panel)
        pygame.display.flip()

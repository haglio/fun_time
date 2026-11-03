from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pygame
from shared_ui.loading_panel import LoadingPanel, icon_image, render
from shared_ui.palette import LOADING_GROUND

from .library_source import PHASE_DISCOVER

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
    pass


def paint_panel(surface, panel: LoadingPanel) -> None:
    image = render(panel)
    surface.fill(LOADING_GROUND)
    painted = pygame.image.frombuffer(image.tobytes(), image.size, "RGB")
    surface.blit(painted, painted.get_rect(center=surface.get_rect().center))


class LoadingScreen:
    def __init__(self, surface, icon: Path | None = None) -> None:
        self._surface = surface
        self._panel = LoadingPanel(wordmark="Main Funestra", status="", icon=icon_image(icon))

    def update(self, phase: str) -> None:
        stop_if_asked()
        self._panel = replace(self._panel, status=progress_text(phase))
        paint_panel(self._surface, self._panel)
        pygame.display.flip()

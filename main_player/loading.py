"""The main player's window while the library is being read.

The library walk and the duration probe both run before there is a video, and a
cold duration cache is one ffprobe per unprobed video — long enough that a
double-click with nothing on screen reads as nothing happening.  The window
opens first and this paints the wait into it.

The pure decisions — what the line says, how far the bar has gone, whether this
update is worth a repaint — are module functions, and the painting is the
family's loading panel on a surface.
"""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pygame
from shared_ui.loading_panel import LoadingPanel, icon_image, render
from shared_ui.palette import LOADING_GROUND

from .library_source import PHASE_DISCOVER, PHASE_DURATIONS

# What the library build's phases are called on screen.  The build reports
# phase keys and the wording lives here, so the layer that does the waiting
# never carries display text.
_MESSAGES = {
    PHASE_DISCOVER: "Finding videos...",
    PHASE_DURATIONS: "Reading video lengths...",
}


def progress_text(phase: str, done: int, total: int) -> str:
    """The line under the main player's name: the phase, plus its count when it has one."""
    message = _MESSAGES.get(phase, phase)
    if total <= 0:
        return message
    return f"{message} {done} of {total}"


def progress_fraction(done: int, total: int) -> float | None:
    """How far along a phase is, or None when it has nothing to count by."""
    if total <= 0:
        return None
    return min(1.0, max(0.0, done / total))


# ~20 repaints a second: smooth enough to read as moving, cheap enough that the
# warm-cache path — where the whole library reports in under a fifth of a second
# — is not spent redrawing a screen nobody has time to look at.
REPAINT_INTERVAL_S = 0.05


def repaint_due(*, phase: str, last_phase: str | None, now: float, last_paint_s: float) -> bool:
    """Whether this update earns a repaint: a new phase always does, and within
    a phase only once the interval has elapsed."""
    return phase != last_phase or now - last_paint_s >= REPAINT_INTERVAL_S


def quit_requested(events) -> bool:
    return any(event.type == pygame.QUIT for event in events)


def stop_if_asked() -> None:
    """Pump the window's event queue, and raise if the user gave up.

    Two jobs in one line each, both owed every update: pumping is what keeps
    Windows from graying the window out as unresponsive during a long scan, and
    noticing the close button is what lets the scan be abandoned at all --
    `main_player.app` catches `LoadingCanceled` and exits 0 without ever opening a
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
    """Paints the library wait into the main player's window, and lets the user out of it.

    Also the main player's progress callback: ``update`` has the signature
    :func:`main_player.library_source.build_library_source` reports through, so the
    screen is handed straight to the build.  Every update pumps the window's
    event queue — both to keep Windows from graying the window out as
    unresponsive, and to notice the close button.
    """

    def __init__(self, surface, icon: Path | None = None) -> None:
        self._surface = surface
        self._panel = LoadingPanel(wordmark="Main Player", status="", icon=icon_image(icon))
        self._last_phase: str | None = None
        self._last_paint_s = 0.0

    def update(self, phase: str, done: int = 0, total: int = 0) -> None:
        """Progress callback: repaint if due, and raise if the user gave up."""
        stop_if_asked()
        now = time.monotonic()
        if not repaint_due(
            phase=phase, last_phase=self._last_phase, now=now, last_paint_s=self._last_paint_s,
        ):
            return
        self._last_phase, self._last_paint_s = phase, now
        self._panel = replace(
            self._panel, status=progress_text(phase, done, total),
            fraction=progress_fraction(done, total),
        )
        paint_panel(self._surface, self._panel)
        pygame.display.flip()

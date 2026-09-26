"""The main player's window while the library is being read.

The library walk and the duration probe both run before there is a video, and a
cold duration cache is one ffprobe per unprobed video — long enough that a
double-click with nothing on screen reads as nothing happening.  The window
opens first and this paints the wait into it.

The pure decisions — what the line says, how far the bar has gone, whether this
update is worth a repaint — are module functions so they are unit-testable; the
painting itself needs a real surface and is exercised by running the main player.
"""
from __future__ import annotations

import time

import pygame

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


# The main player's own magenta (its icon's), on near-black, with the timeline's inset,
# bordered track for the bar — so the wait looks like the app it opens into.
_BACKGROUND = (12, 12, 14)
_MAGENTA = (200, 80, 160)
_TEXT = (215, 215, 220)
_TRACK_FILL = (34, 34, 38)
_TRACK_BORDER = (70, 70, 78)
_TITLE_SIZE = 72
_MESSAGE_SIZE = 26
_BAR_HEIGHT = 10
_BAR_WIDTH_FRAC = 0.6
_BORDER_W = 2
_TITLE_GAP = 30    # under the name, before the message
_MESSAGE_GAP = 24  # under the message, before the bar


class LoadingScreen:
    """Paints the library wait into the main player's window, and lets the user out of it.

    Also the main player's progress callback: ``update`` has the signature
    :func:`main_player.library_source.build_library_source` reports through, so the
    screen is handed straight to the build.  Every update pumps the window's
    event queue — both to keep Windows from graying the window out as
    unresponsive, and to notice the close button.

    Not unit-tested: it needs a real display.  Its decisions are the module
    functions above, which are — including the pump-and-raise, which is
    :func:`stop_if_asked` rather than two lines in here; what is left is the
    painting.
    """

    def __init__(self, surface) -> None:
        self._surface = surface
        self._title_font = pygame.font.Font(None, _TITLE_SIZE)
        self._message_font = pygame.font.Font(None, _MESSAGE_SIZE)
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
        self._paint(progress_text(phase, done, total), progress_fraction(done, total))

    def _paint(self, message: str, fraction: float | None) -> None:
        width, height = self._surface.get_size()
        self._surface.fill(_BACKGROUND)

        # Name over message over bar, laid out as one block and centered as one,
        # so the group sits in the middle of whatever rect the main player was given.
        title = self._title_font.render("Main Player", True, _MAGENTA)
        line = self._message_font.render(message, True, _TEXT)
        bar_w = int(width * _BAR_WIDTH_FRAC)
        block_h = (
            title.get_height() + _TITLE_GAP + line.get_height() + _MESSAGE_GAP + _BAR_HEIGHT
        )
        top = (height - block_h) // 2

        self._surface.blit(title, title.get_rect(midtop=(width // 2, top)))
        top += title.get_height() + _TITLE_GAP
        self._surface.blit(line, line.get_rect(midtop=(width // 2, top)))
        top += line.get_height() + _MESSAGE_GAP

        track = pygame.Rect((width - bar_w) // 2, top, bar_w, _BAR_HEIGHT)
        pygame.draw.rect(self._surface, _TRACK_FILL, track)
        pygame.draw.rect(self._surface, _TRACK_BORDER, track, _BORDER_W)
        if fraction:
            inner = track.inflate(-_BORDER_W * 2, -_BORDER_W * 2)
            pygame.draw.rect(self._surface, _MAGENTA, pygame.Rect(
                inner.left, inner.top, max(1, int(inner.width * fraction)), inner.height,
            ))

        pygame.display.flip()

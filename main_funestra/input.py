"""What arrives in a Funestra's viewport, and what it is taken to mean.

SDL hands the frame a queue of events; the Funestra answers the mouse and the
viewport being closed, and a key reaches nothing: Fun Time's hotkeys are the
keyboard for all of Fun Time.  Nothing is decided here beyond WHICH event is
which, and the viewport size travels with the frame rather than being read off
the viewport, so everything in one frame is answered against one viewport.
"""
from __future__ import annotations

import pygame


class Input:
    def __init__(self, funestra) -> None:
        self._funestra = funestra

    def deal(self, events, viewport: tuple[int, int]) -> None:
        for ev in events:
            if ev.type == pygame.QUIT:
                self._funestra.close_requested()
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                self._funestra.press(*ev.pos, viewport=viewport)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                self._funestra.release()
            elif ev.type == pygame.MOUSEMOTION:
                self._funestra.motion(*ev.pos, held=bool(ev.buttons[0]), viewport=viewport)
            elif ev.type == pygame.WINDOWLEAVE:
                self._funestra.leave()

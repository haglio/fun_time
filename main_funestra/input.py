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

from __future__ import annotations

import pygame

_LEFT, _WHEEL_BUTTON, _RIGHT = 1, 2, 3
_WHEEL_STEPS = {4: 1, 5: -1}


class Input:
    def __init__(self, funestra) -> None:
        self._funestra = funestra

    def deal(self, events, viewport: tuple[int, int]) -> None:
        for ev in events:
            if ev.type == pygame.QUIT:
                self._funestra.close_requested()
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == _LEFT:
                self._funestra.press(*ev.pos, viewport=viewport)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == _RIGHT:
                self._funestra.right_press(*ev.pos, viewport=viewport)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == _WHEEL_BUTTON:
                self._funestra.wheel_press(*ev.pos, viewport=viewport)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button in _WHEEL_STEPS:
                self._funestra.wheel(*ev.pos, _WHEEL_STEPS[ev.button], viewport=viewport)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == _LEFT:
                self._funestra.release()
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == _WHEEL_BUTTON:
                self._funestra.wheel_release()
            elif ev.type == pygame.MOUSEMOTION:
                self._funestra.motion(*ev.pos, held=bool(ev.buttons[0]), viewport=viewport)
            elif ev.type == pygame.WINDOWLEAVE:
                self._funestra.leave()

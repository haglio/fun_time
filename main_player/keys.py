"""The main player's keyboard, over the player under it.

A table rather than a chain of comparisons, because a chain is where two
bindings quietly swap: every one of these reached the player through ten
``elif``s inside ``main_player.app``'s run loop, where no test could press a key at all.

Ctrl+Q is not in the table.  It is the only binding that reads a modifier, and
it does not act on the player: in a session it asks the session to come down —
see :mod:`main_player.dashboard`.
"""
from __future__ import annotations

from functools import partial

import pygame

from .controls import SEEK_STEP_MS


class Keys:
    """What each key does, and nothing about which key it was."""

    def __init__(self, session, modes, dashboard) -> None:
        self._dashboard = dashboard
        self._on_press = {
            pygame.K_ESCAPE: session.toggle_pause,
            # Held rather than tapped: pressing marks the loop's in point and
            # letting go marks its out point, so the two halves are one gesture.
            pygame.K_r: session.record_down,
            pygame.K_LEFTBRACKET: partial(session.step, -1),
            pygame.K_RIGHTBRACKET: partial(session.step, 1),
            pygame.K_MINUS: partial(session.seek_by, -SEEK_STEP_MS),
            pygame.K_EQUALS: partial(session.seek_by, SEEK_STEP_MS),
            pygame.K_v: session.cycle_version,
            pygame.K_l: modes.toggle_length,
        }
        self._on_release = {pygame.K_r: session.record_up}

    def press(self, key: int, mod: int = 0) -> bool:
        """Answer a key going down; False if nothing is bound to it."""
        if key == pygame.K_q and mod & pygame.KMOD_CTRL:
            self._dashboard.take_quit_gesture()
            return True
        return self._act(self._on_press, key)

    def release(self, key: int) -> bool:
        """Answer a key coming up; False if nothing is bound to it."""
        return self._act(self._on_release, key)

    @staticmethod
    def _act(table, key: int) -> bool:
        action = table.get(key)
        if action is None:
            return False
        action()
        return True

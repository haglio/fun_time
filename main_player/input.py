"""What arrives in the main player's window, and who it is meant for.

SDL hands the frame a queue of events; two things answer them.  The pointer
takes the mouse -- where a press lands, what a drag is dragging, where the
cursor is hovering.  The window being closed is the other: in a session it is
the session that goes, so the gesture is asked of :mod:`main_player.dashboard`
rather than answered here.  A key is neither, and reaches nothing: Fun Time's
hotkeys are the keyboard for the whole room.

Nothing is decided in this module beyond WHICH of the two an event belongs
to.  It holds no state of its own -- the hover lives on the pointer, the drag
latch on the console painter -- so the whole of it is that mapping, and the
mapping is where two branches quietly swap.  It lived inside ``main_player.app``'s run loop, where feeding it an
event meant opening a window.

The window size travels with each event rather than being read off the window,
because it is sampled once at the top of the frame: everything in one frame is
answered against one window, even if the window is being resized under it.
"""
from __future__ import annotations

import pygame


class Input:
    """One frame's events, dealt to the things that answer them."""

    def __init__(self, pointer, dashboard) -> None:
        self._pointer = pointer
        self._dashboard = dashboard

    def deal(self, events, win_w: int, win_h: int) -> None:
        """Answer *events*, in the order SDL queued them."""
        for ev in events:
            if ev.type == pygame.QUIT:
                self._dashboard.take_quit_gesture()
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                self._pointer.press(*ev.pos, win_w=win_w, win_h=win_h)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                self._pointer.release()
            elif ev.type == pygame.MOUSEMOTION:
                self._pointer.motion(*ev.pos, held=bool(ev.buttons[0]),
                                     win_w=win_w, win_h=win_h)

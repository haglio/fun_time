"""What arrives in the main player's window, and what it is taken to mean.

The translation itself: which SDL event reaches the pointer, which reaches the
keyboard, and which is the window being closed.  Six branches on ``ev.type``,
two of them gated on the button as well, and until now no test anywhere fed a
pygame event to anything -- so the whole mapping from ``ev.button`` /
``ev.buttons`` / ``ev.pos`` / ``ev.key`` to a collaborator's call was unpinned,
including the two places a wrong index quietly turns a drag into a hover.

What each collaborator then DOES is theirs, and is tested in test_main_player_pointer,
test_main_player_keys and test_main_player_dashboard.
"""
from __future__ import annotations

from types import SimpleNamespace

import pygame

from main_player.input import Input

WIN_W, WIN_H = 1000, 600


class SpyPointer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def press(self, mx, my, *, win_w, win_h) -> None:
        self.calls.append(("press", mx, my, win_w, win_h))

    def release(self) -> None:
        self.calls.append(("release",))

    def motion(self, mx, my, *, held, win_w, win_h) -> None:
        self.calls.append(("motion", mx, my, held, win_w, win_h))


class SpyKeys:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def press(self, key, mod) -> None:
        self.calls.append(("press", key, mod))

    def release(self, key) -> None:
        self.calls.append(("release", key))


class SpyDashboard:
    def __init__(self) -> None:
        self.asked = 0

    def take_quit_gesture(self) -> None:
        self.asked += 1


def _input() -> tuple[Input, SpyPointer, SpyKeys, SpyDashboard]:
    pointer, keys, dashboard = SpyPointer(), SpyKeys(), SpyDashboard()
    return Input(pointer, keys, dashboard), pointer, keys, dashboard


def _deal(window_input, *events) -> None:
    window_input.deal(events, WIN_W, WIN_H)


class TestTheWindowBeingClosed:
    def test_a_quit_gesture_is_asked_of_the_session_rather_than_taken(self):
        """The close button and Alt+F4 arrive here.  In a session it is the session
        that goes, not this one window out of six -- see main_player.dashboard."""
        window_input, _p, _k, dashboard = _input()

        _deal(window_input, SimpleNamespace(type=pygame.QUIT))

        assert dashboard.asked == 1

class TestTheMouse:
    def test_a_left_press_lands_at_its_place_in_this_window(self):
        """The size comes from the frame rather than the event, so everything in
        one frame is read against one window."""
        window_input, pointer, _k, _d = _input()

        _deal(window_input, SimpleNamespace(
            type=pygame.MOUSEBUTTONDOWN, button=1, pos=(120, 480)))

        assert pointer.calls == [("press", 120, 480, WIN_W, WIN_H)]

    def test_a_left_release_ends_whatever_was_held(self):
        window_input, pointer, _k, _d = _input()

        _deal(window_input, SimpleNamespace(type=pygame.MOUSEBUTTONUP, button=1))

        assert pointer.calls == [("release",)]

    def test_the_other_buttons_do_nothing_at_all(self):
        """Right, middle and the two wheel buttons: nothing on this HUD answers
        them, and a press that fell through to the left button's branch would
        seek the video from wherever the right button was clicked."""
        window_input, pointer, _k, _d = _input()

        for button in (2, 3, 4, 5):
            _deal(window_input,
                  SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=button, pos=(1, 2)),
                  SimpleNamespace(type=pygame.MOUSEBUTTONUP, button=button))

        assert pointer.calls == []

    def test_a_drag_is_a_move_with_the_left_button_down(self):
        """``buttons`` is the whole mouse, left first; reading another slot
        turns every drag into a hover and the timeline stops scrubbing."""
        window_input, pointer, _k, _d = _input()

        _deal(window_input, SimpleNamespace(
            type=pygame.MOUSEMOTION, pos=(300, 200), buttons=(1, 0, 0)))

        assert pointer.calls == [("motion", 300, 200, True, WIN_W, WIN_H)]

    def test_a_move_with_nothing_held_is_a_hover(self):
        window_input, pointer, _k, _d = _input()

        _deal(window_input, SimpleNamespace(
            type=pygame.MOUSEMOTION, pos=(300, 200), buttons=(0, 1, 0)))

        assert pointer.calls == [("motion", 300, 200, False, WIN_W, WIN_H)]


class TestTheKeyboard:
    def test_a_key_going_down_carries_its_modifiers(self):
        """Ctrl+Q is a binding, so the modifier has to travel with the key --
        dropped, Ctrl+Q becomes a bare Q and quits nothing."""
        window_input, _p, keys, _d = _input()

        _deal(window_input, SimpleNamespace(
            type=pygame.KEYDOWN, key=pygame.K_q, mod=pygame.KMOD_CTRL))

        assert keys.calls == [("press", pygame.K_q, pygame.KMOD_CTRL)]

    def test_a_key_coming_up_is_its_own_gesture(self):
        """Holding R marks a loop's in point and letting go marks its out
        point, so the release is half of it rather than nothing."""
        window_input, _p, keys, _d = _input()

        _deal(window_input, SimpleNamespace(type=pygame.KEYUP, key=pygame.K_r))

        assert keys.calls == [("release", pygame.K_r)]


class TestAFrameOfEvents:
    def test_they_are_answered_in_the_order_they_arrived(self):
        """SDL's queue is the order the user did things in, and a press
        answered after the release that ended it leaves the drag latched."""
        window_input, pointer, _k, _d = _input()

        _deal(window_input,
              SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=1, pos=(10, 20)),
              SimpleNamespace(type=pygame.MOUSEMOTION, pos=(11, 20), buttons=(1, 0, 0)),
              SimpleNamespace(type=pygame.MOUSEBUTTONUP, button=1))

        assert [call[0] for call in pointer.calls] == ["press", "motion", "release"]

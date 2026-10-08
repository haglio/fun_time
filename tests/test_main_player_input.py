"""What arrives in the Main Funestra's window, and what it is taken to mean.

The translation itself: which SDL event reaches the Funestra as a press, a
release, a move or the window being closed -- and that a key reaches nothing,
the room's keyboard being Fun Time's.
"""
from __future__ import annotations

from types import SimpleNamespace

import pygame

from main_player.input import Input

WINDOW = (1000, 600)


class SpyFunestra:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def close_requested(self) -> None:
        self.calls.append(("close_requested",))

    def press(self, x, y, *, window) -> None:
        self.calls.append(("press", x, y, window))

    def release(self) -> None:
        self.calls.append(("release",))

    def motion(self, x, y, *, held, window) -> None:
        self.calls.append(("motion", x, y, held, window))


def _input() -> tuple[Input, SpyFunestra]:
    funestra = SpyFunestra()
    return Input(funestra), funestra


def _deal(window_input, *events) -> None:
    window_input.deal(events, WINDOW)


class TestTheWindowBeingClosed:
    def test_a_quit_gesture_is_the_funestras_to_answer(self):
        """The close button and Alt+F4 arrive here; in a session it is the
        session that goes, not this one window, and the Funestra knows."""
        window_input, funestra = _input()

        _deal(window_input, SimpleNamespace(type=pygame.QUIT))

        assert funestra.calls == [("close_requested",)]


class TestTheMouse:
    def test_a_left_press_lands_at_its_place_in_this_window(self):
        window_input, funestra = _input()

        _deal(window_input, SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=1, pos=(120, 480)))

        assert funestra.calls == [("press", 120, 480, WINDOW)]

    def test_a_left_release_ends_whatever_was_held(self):
        window_input, funestra = _input()

        _deal(window_input, SimpleNamespace(type=pygame.MOUSEBUTTONUP, button=1))

        assert funestra.calls == [("release",)]

    def test_the_other_buttons_do_nothing_at_all(self):
        window_input, funestra = _input()

        for button in (2, 3, 4, 5):
            _deal(window_input,
                  SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=button, pos=(1, 2)),
                  SimpleNamespace(type=pygame.MOUSEBUTTONUP, button=button))

        assert funestra.calls == []

    def test_a_drag_is_a_move_with_the_left_button_down(self):
        window_input, funestra = _input()

        _deal(window_input, SimpleNamespace(type=pygame.MOUSEMOTION, pos=(300, 200), buttons=(1, 0, 0)))

        assert funestra.calls == [("motion", 300, 200, True, WINDOW)]

    def test_a_move_with_nothing_held_is_a_hover(self):
        window_input, funestra = _input()

        _deal(window_input, SimpleNamespace(type=pygame.MOUSEMOTION, pos=(300, 200), buttons=(0, 1, 0)))

        assert funestra.calls == [("motion", 300, 200, False, WINDOW)]


class TestTheKeyboard:
    def test_a_key_pressed_on_the_window_reaches_nothing(self):
        window_input, funestra = _input()

        _deal(window_input,
              SimpleNamespace(type=pygame.KEYDOWN, key=pygame.K_q, mod=pygame.KMOD_CTRL),
              SimpleNamespace(type=pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0),
              SimpleNamespace(type=pygame.KEYUP, key=pygame.K_r, mod=0))

        assert funestra.calls == []


class TestAFrameOfEvents:
    def test_they_are_answered_in_the_order_they_arrived(self):
        window_input, funestra = _input()

        _deal(window_input,
              SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=1, pos=(10, 20)),
              SimpleNamespace(type=pygame.MOUSEMOTION, pos=(11, 20), buttons=(1, 0, 0)),
              SimpleNamespace(type=pygame.MOUSEBUTTONUP, button=1))

        assert [call[0] for call in funestra.calls] == ["press", "motion", "release"]

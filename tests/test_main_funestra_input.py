from __future__ import annotations

from types import SimpleNamespace

import pygame

from main_funestra.input import Input

WINDOW = (1000, 600)


class SpyFunestra:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def close_requested(self) -> None:
        self.calls.append(("close_requested",))

    def press(self, x, y, *, viewport) -> None:
        self.calls.append(("press", x, y, viewport))

    def release(self) -> None:
        self.calls.append(("release",))

    def motion(self, x, y, *, held, viewport) -> None:
        self.calls.append(("motion", x, y, held, viewport))

    def leave(self) -> None:
        self.calls.append(("leave",))


def _input() -> tuple[Input, SpyFunestra]:
    funestra = SpyFunestra()
    return Input(funestra), funestra


def _deal(window_input, *events) -> None:
    window_input.deal(events, WINDOW)


class TestTheWindowBeingClosed:
    def test_the_close_button_and_alt_f4_are_the_funestras_to_answer(self):
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

    def test_the_pointer_going_out_of_the_window_is_the_funestras_to_know(self):
        window_input, funestra = _input()

        _deal(window_input, SimpleNamespace(type=pygame.WINDOWLEAVE))

        assert funestra.calls == [("leave",)]

    def test_a_move_with_nothing_held_is_a_hover(self):
        window_input, funestra = _input()

        _deal(window_input, SimpleNamespace(type=pygame.MOUSEMOTION, pos=(300, 200), buttons=(0, 1, 0)))

        assert funestra.calls == [("motion", 300, 200, False, WINDOW)]


class TestTheKeyboardIsFunTimesAlone:
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

    def test_every_event_of_one_frame_is_answered_against_its_viewport(self):
        window_input, funestra = _input()

        window_input.deal([
            SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=1, pos=(10, 20)),
            SimpleNamespace(type=pygame.MOUSEMOTION, pos=(11, 20), buttons=(1, 0, 0)),
        ], (640, 480))

        assert [call[-1] for call in funestra.calls] == [(640, 480), (640, 480)]

from __future__ import annotations

from unittest.mock import patch

import cv2

from fun_time.robot_hand.clipper.ui import build_ui, handle_key, on_mouse

from tests.test_clipper_state import _make_state


class TestHandleKey:
    def test_space_toggles_loop_pause(self):
        state = _make_state()
        handle_key(state, 32)
        assert state.loop_paused is True
        handle_key(state, 32)
        assert state.loop_paused is False


class TestMouseControls:
    def test_clicking_play_pause_button_toggles_loop_pause(self):
        state = _make_state()
        build_ui(state)
        x1, y1, x2, y2 = state.buttons["play_pause"]
        on_mouse(cv2.EVENT_LBUTTONDOWN, (x1 + x2) // 2, (y1 + y2) // 2, 0, state)
        assert state.loop_paused is True

    def test_build_ui_sets_play_icon_when_paused(self):
        state = _make_state()
        state.loop_paused = True
        icons: list[str | None] = []

        def capture_button(_img, _rect, text, **kwargs):
            if text == "":
                icons.append(kwargs.get("icon"))

        with patch("fun_time.robot_hand.clipper.ui.draw_button", side_effect=capture_button):
            build_ui(state)

        assert "play" in icons


class TestLayout:
    def test_speed_and_play_buttons_are_square_and_evenly_spaced(self):
        state = _make_state()
        build_ui(state)
        speed_down = state.buttons["speed_down"]
        speed_up = state.buttons["speed_up"]
        play_pause = state.buttons["play_pause"]

        def width(rect):
            return rect[2] - rect[0]

        def height(rect):
            return rect[3] - rect[1]

        def gap(left, right):
            return right[0] - left[2]

        assert width(speed_down) == height(speed_down)
        assert width(speed_up) == height(speed_up)
        assert width(play_pause) == height(play_pause)
        assert gap(speed_down, speed_up) == gap(speed_up, play_pause)

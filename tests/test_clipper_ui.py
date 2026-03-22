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

    def test_zero_accepts_suggested_out(self):
        state = _make_state(active_start=10, active_end=30, initial_active_start=0, initial_active_end=30)
        state.suggested_out = 24
        handle_key(state, ord("0"))
        assert state.active_end == 24

    def test_nine_accepts_suggested_in(self):
        state = _make_state(active_start=10, active_end=30, initial_active_start=10, initial_active_end=79)
        state.suggested_in = 14
        handle_key(state, ord("9"))
        assert state.active_start == 14

    def test_l_cycles_loop_mode(self):
        state = _make_state(loop_mode="base-tip-base")
        handle_key(state, ord("l"))
        assert state.loop_mode == "tip-base-tip"

    def test_comma_shifts_active_range_left(self):
        state = _make_state(active_start=20, active_end=30, current=25)
        handle_key(state, ord(","))
        assert state.active_start == 10
        assert state.active_end == 20

    def test_period_shifts_active_range_right(self):
        state = _make_state(active_start=10, active_end=20, current=15)
        handle_key(state, ord("."))
        assert state.active_start == 20
        assert state.active_end == 30

    def test_a_extends_loaded_left(self):
        state = _make_state(loaded_start=10, active_start=20, base_step=5)
        with patch("fun_time.robot_hand.clipper.state.ensure_loaded") as ensure_loaded:
            ensure_loaded.side_effect = lambda s, want_start, _want_end: setattr(s, "loaded_start", want_start)
            handle_key(state, ord("a"))
        assert state.loaded_start == 5

    def test_s_contracts_loaded_left(self):
        state = _make_state(loaded_start=10, active_start=20, base_step=5)
        handle_key(state, ord("s"))
        assert state.loaded_start == 15

    def test_d_contracts_loaded_right(self):
        state = _make_state(loaded_end=40, active_end=30, base_step=5)
        handle_key(state, ord("d"))
        assert state.loaded_end == 35

    def test_f_extends_loaded_right(self):
        state = _make_state(loaded_end=30, active_end=20, total_frames=50, base_step=5)
        with patch("fun_time.robot_hand.clipper.state.ensure_loaded") as ensure_loaded:
            ensure_loaded.side_effect = lambda s, _want_start, want_end: setattr(s, "loaded_end", want_end)
            handle_key(state, ord("f"))
        assert state.loaded_end == 35


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

    def test_clicking_loop_mode_button_cycles_mode(self):
        state = _make_state(loop_mode="base-tip-base")
        build_ui(state)
        x1, y1, x2, y2 = state.buttons["loop_mode"]
        on_mouse(cv2.EVENT_LBUTTONDOWN, (x1 + x2) // 2, (y1 + y2) // 2, 0, state)
        assert state.loop_mode == "tip-base-tip"

    def test_clicking_shift_right_button_shifts_active_range(self):
        state = _make_state(active_start=10, active_end=20, current=15)
        build_ui(state)
        x1, y1, x2, y2 = state.buttons["shift_right"]
        on_mouse(cv2.EVENT_LBUTTONDOWN, (x1 + x2) // 2, (y1 + y2) // 2, 0, state)
        assert state.active_start == 20
        assert state.active_end == 30


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

    def test_legend_mentions_shift_accept_wrap_and_loop_mode_hotkeys(self):
        state = _make_state()
        labels: list[str] = []

        def capture_centered(_img, text, *_args, **_kwargs):
            labels.append(text)

        with patch("fun_time.robot_hand.clipper.ui.put_text_centered", side_effect=capture_centered):
            build_ui(state)

        assert any("< or >: Shift In-Out" in text for text in labels)
        assert any("A/S/D/F: Loaded bounds" in text for text in labels)
        assert any("(: Accept In suggestion" in text for text in labels)
        assert any("): Accept Out suggestion" in text for text in labels)
        assert any("M: Wrap" in text for text in labels)
        assert any("L: Loop mode" in text for text in labels)
        assert any("-/+: Speed" in text for text in labels)
        assert any("Enter: Export" in text for text in labels)

    def test_shift_buttons_render_above_timeline_and_mark_wrap_controls_render_below(self):
        state = _make_state(active_start=10, active_end=30, current=20)
        build_ui(state)
        timeline = state.buttons["timeline"]
        shift_left = state.buttons["shift_left"]
        shift_right = state.buttons["shift_right"]
        mark_in = state.buttons["mark_in"]
        mark_out = state.buttons["mark_out"]
        wrap = state.buttons["wrap"]

        assert shift_left[3] <= timeline[1]
        assert shift_right[3] <= timeline[1]
        assert mark_in[1] >= timeline[3]
        assert mark_out[1] >= timeline[3]
        assert wrap[1] > mark_in[3]

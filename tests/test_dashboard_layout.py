"""Where the dashboard's controls sit, now that it is a bar and not a schematic."""
from __future__ import annotations

from fun_time.dashboard_layout import (
    BUTTON,
    GAP,
    PAD,
    Rect,
    client_rect_filling_frame,
    compute_dashboard_bar_layout,
    dashboard_window_height,
)


def _rects(layout) -> list[Rect]:
    return [
        layout.app_icon, layout.app_title,
        layout.quit_button, layout.omnipause_button, layout.help_button,
        layout.voice_panel,
    ]


def test_the_bar_reads_left_to_right_in_the_order_it_is_written():
    """The app's mark, then the controls — grouped by what they are rather than
    by which player they used to stand for."""
    layout = compute_dashboard_bar_layout()

    xs = [rect.x for rect in _rects(layout)]
    assert xs == sorted(xs)
    assert layout.app_icon.x == PAD


def test_nothing_in_the_bar_overlaps_anything_else():
    layout = compute_dashboard_bar_layout()
    rects = _rects(layout)

    for index, first in enumerate(rects):
        for second in rects[index + 1:]:
            assert (first.x + first.width <= second.x
                    or second.x + second.width <= first.x)


def test_the_microphone_is_one_of_the_buttons_not_a_light_beside_them():
    """F-mode was the other light, and it has gone to the players' own HUDs.  A
    lone chip past a group gap read as adrift from the bar, so the microphone is
    a button in the same run, at the same size and the same spacing."""
    layout = compute_dashboard_bar_layout()

    assert layout.voice_panel.height == layout.quit_button.height == BUTTON
    assert layout.voice_panel.y == layout.quit_button.y
    assert layout.voice_panel.x == layout.help_button.x + BUTTON + GAP


def test_everything_sits_on_one_line_inside_the_bars_height():
    layout = compute_dashboard_bar_layout()

    for rect in _rects(layout):
        assert rect.y >= 0
        assert rect.y + rect.height <= layout.height


def test_the_window_leaves_the_log_the_room_the_bar_does_not_take():
    layout = compute_dashboard_bar_layout()

    assert dashboard_window_height() - layout.height >= 150


def test_client_rect_filling_frame_insets_by_the_chrome():
    """A window's frame is drawn outside its client area, so a popup asked to
    fill a rect has to be inset by the chrome or its title bar overhangs."""
    rect = Rect(100, 200, 400, 300)

    assert client_rect_filling_frame(rect, left=8, top=31, right=8, bottom=8) == (
        108, 231, 384, 261)
    assert client_rect_filling_frame(rect, left=0, top=0, right=0, bottom=0) == (
        100, 200, 400, 300)

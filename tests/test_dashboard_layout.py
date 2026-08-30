"""Where the dashboard's controls sit, now that it is a bar and not a schematic."""
from __future__ import annotations

import pytest

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


def test_the_bar_uses_the_familys_button_metrics():
    """A control here is the same object a control in Origenerator's bank is.
    Both were this bar's own numbers, which is what left four apps on one screen
    reading as four different kinds of chrome."""
    from shared_ui.spacing import BUTTON_GAP, BUTTON_SIZE

    from fun_time.dashboard_layout import BUTTON, GAP

    assert BUTTON == BUTTON_SIZE
    assert GAP == BUTTON_GAP


def test_one_rectangle_under_every_name_the_session_calls_it():
    """Six frozen dataclasses declared the same four ints under six names —
    Rect, MonitorRect, WindowRect, MonitorInfo, DashboardLaunchGeometry,
    DashboardWindowSnapshot — and callers paid for it in hand-written
    conversions between types that were already identical."""
    from fun_time.dashboard_app import DashboardLaunchGeometry
    from fun_time.dashboard_layout import Rect
    from fun_time.dashboard_runtime import DashboardWindowSnapshot
    from fun_time.monitors import MonitorInfo
    from fun_time.window_layout import MonitorRect, WindowRect

    every_name = (MonitorRect, WindowRect, MonitorInfo,
                  DashboardLaunchGeometry, DashboardWindowSnapshot)

    assert all(name is Rect for name in every_name)


def test_a_rect_is_still_four_ints_in_that_order():
    """Every one of those names was constructed positionally somewhere."""
    from dataclasses import fields

    from fun_time.dashboard_layout import Rect

    assert [f.name for f in fields(Rect)] == ["x", "y", "width", "height"]
    assert Rect(1, 2, 3, 4) == Rect(x=1, y=2, width=3, height=4)


class TestARectOnACommandLine:
    """A rect reaches a child as four separate flags.  Two entry points spelled
    the quartet out — one of them twice — and each followed it with the same
    `if None not in {...}` idiom, which nothing pinned in either direction."""

    def test_all_four_flags_name_a_rect(self):
        from fun_time.dashboard_app import parse_args
        from fun_time.dashboard_layout import Rect, rect_from_arguments

        args = parse_args(["state/m.ini", "--x", "100", "--y", "200",
                           "--width", "300", "--height", "400"])

        assert rect_from_arguments(args) == Rect(100, 200, 300, 400)

    @pytest.mark.parametrize("given", ["--x", "--y", "--width", "--height"])
    def test_any_one_of_them_missing_is_not_a_rect(self, given: str):
        """Three of four would place a window somewhere nobody asked for."""
        from fun_time.dashboard_app import parse_args
        from fun_time.dashboard_layout import rect_from_arguments

        args = parse_args(["state/m.ini", given, "100"])

        assert rect_from_arguments(args) is None

    def test_a_prefixed_quartet_is_its_own_rect(self):
        """The panel is handed two: its own, and the browser's."""
        from fun_time.dashboard_app import parse_args
        from fun_time.dashboard_layout import Rect, rect_from_arguments

        args = parse_args(["state/m.ini", "--rfb-x", "1", "--rfb-y", "2",
                           "--rfb-width", "3", "--rfb-height", "4"])

        assert rect_from_arguments(args, prefix="rfb_") == Rect(1, 2, 3, 4)
        assert rect_from_arguments(args) is None

"""The dashboard hanging in the headset: what it shows, and what a press does."""
from __future__ import annotations

import logging

import numpy as np

from fun_time.dashboard_actions import (
    HELP_REFERENCE,
    OMNIPAUSE_TOGGLE,
    QUIT_BUTTON,
    VOICE_TOGGLE,
)
from fun_time.dashboard_layout import compute_dashboard_bar_layout
from fun_time.event_log import (
    LEVELS_BY_NAME,
    NOTICE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
    SOURCES,
    EventRecord,
)
from fun_time_vr.dash_panel import (
    DASH_WIDTH_PX,
    LOG_ROWS,
    VERBOSITY_CHIP,
    DashPointer,
    DashState,
    dash_actions,
    dash_height,
    format_row,
    next_verbosity,
    paint_dash,
    verbosity_name,
)


def _record(message: str, *, level: int = NOTICE, source: str = SOURCE_SYSTEM) -> EventRecord:
    return EventRecord(ts=1_700_000_000.0, level=level, source=source, message=message)


def _pointer(**state):
    posted: list[str] = []
    return DashPointer(post=posted.append, state=DashState(**state)), posted


def _middle(action: str) -> tuple[int, int]:
    rect = dash_actions()[action]
    return rect.x + rect.width // 2, rect.y + rect.height // 2


class TestItIsTheDesktopsBar:
    def test_the_four_controls_sit_where_the_desktop_puts_them(self):
        """Same layout function, so the bar in the headset is that bar rather
        than a lookalike drawn to match."""
        bar = compute_dashboard_bar_layout()
        actions = dash_actions()

        assert actions[QUIT_BUTTON] == bar.quit_button
        assert actions[OMNIPAUSE_TOGGLE] == bar.omnipause_button
        assert actions[HELP_REFERENCE] == bar.help_button
        assert actions[VOICE_TOGGLE] == bar.voice_panel

    def test_every_window_has_a_filter_chip(self):
        actions = dash_actions()

        for source in SOURCES:
            assert source in actions

    def test_a_row_reads_the_way_the_log_panel_writes_it(self):
        row = format_row(_record("portrait next", source=SOURCE_PORTRAIT))

        assert row.endswith("portrait next")
        assert "portrait" in row
        assert row[2] == ":" and row[5] == ":"  # a clock leads


class TestWhatAPressDoes:
    def test_a_control_posts_the_command_the_desktop_posts(self):
        pointer, posted = _pointer()

        for action in (QUIT_BUTTON, OMNIPAUSE_TOGGLE, HELP_REFERENCE, VOICE_TOGGLE):
            pointer.press(*_middle(action))

        assert posted == [QUIT_BUTTON, OMNIPAUSE_TOGGLE, HELP_REFERENCE, VOICE_TOGGLE]

    def test_the_dial_turns_without_the_session_hearing_it(self):
        """Which way the log is filtered is the panel's own business; the
        desktop's dial posts nothing either."""
        pointer, posted = _pointer()
        was = pointer.state.verbosity

        pointer.press(*_middle(VERBOSITY_CHIP))

        assert pointer.state.verbosity == next_verbosity(was)
        assert posted == []

    def test_a_source_chip_toggles_that_window_off_and_back(self):
        pointer, posted = _pointer()

        pointer.press(*_middle(SOURCE_PORTRAIT))
        assert SOURCE_PORTRAIT not in pointer.state.sources

        pointer.press(*_middle(SOURCE_PORTRAIT))
        assert SOURCE_PORTRAIT in pointer.state.sources
        assert posted == []

    def test_a_press_on_nothing_posts_nothing(self):
        pointer, posted = _pointer()

        assert pointer.press(DASH_WIDTH_PX - 1, dash_height() - 1) is None
        assert posted == []

    def test_the_session_half_of_the_state_does_not_disturb_the_filters(self):
        pointer, _posted = _pointer()
        pointer.press(*_middle(SOURCE_MAIN))
        filtered = pointer.state.sources

        pointer.session_state(omni_paused=True, voice_active=False)

        assert pointer.state.omni_paused is True
        assert pointer.state.voice_active is False
        assert pointer.state.sources == filtered


class TestTheDial:
    def test_it_names_its_stop(self):
        assert verbosity_name(LEVELS_BY_NAME["NOTICE"]) == "NOTICE"
        assert verbosity_name(logging.ERROR) == "ERROR"

    def test_it_wraps_round_the_stops(self):
        stop = LEVELS_BY_NAME["DEBUG"]
        seen = []
        for _ in range(len(LEVELS_BY_NAME)):
            seen.append(stop)
            stop = next_verbosity(stop)

        assert sorted(seen) == sorted(LEVELS_BY_NAME.values())
        assert stop == LEVELS_BY_NAME["DEBUG"]  # back where it started


class TestWhatItDraws:
    def test_it_keeps_its_size_whatever_the_log_says(self):
        """A screen in a scene that changes size is a screen that moves."""
        quiet = paint_dash(DashState(), [])
        busy = paint_dash(DashState(), [_record(f"line {i}") for i in range(LOG_ROWS * 3)])

        assert quiet.size == busy.size == (DASH_WIDTH_PX, dash_height())

    def test_a_line_shows_up_on_it(self):
        quiet = np.asarray(paint_dash(DashState(), []))
        spoken = np.asarray(paint_dash(DashState(), [_record("portrait next")]))

        assert not np.array_equal(quiet, spoken)

    def test_the_dial_hides_what_it_is_set_above(self):
        loud_only = DashState(verbosity=logging.ERROR)
        quiet = np.asarray(paint_dash(loud_only, []))
        spoken = np.asarray(paint_dash(loud_only, [_record("portrait next", level=NOTICE)]))

        assert np.array_equal(quiet, spoken)

    def test_a_window_switched_off_stops_showing(self):
        without = DashState(sources=frozenset(SOURCES) - {SOURCE_PORTRAIT})
        quiet = np.asarray(paint_dash(without, []))
        spoken = np.asarray(
            paint_dash(without, [_record("portrait next", source=SOURCE_PORTRAIT)]))

        assert np.array_equal(quiet, spoken)

    def test_the_pause_control_says_which_way_it_goes(self):
        """The desktop's mark flips to a play triangle while the room is held,
        because that is what pressing it does next."""
        running = np.asarray(paint_dash(DashState(omni_paused=False), []))
        held = np.asarray(paint_dash(DashState(omni_paused=True), []))

        assert not np.array_equal(running, held)

    def test_the_microphone_lights_while_voice_is_live(self):
        live = np.asarray(paint_dash(DashState(voice_active=True), []))
        muted = np.asarray(paint_dash(DashState(voice_active=False), []))

        assert not np.array_equal(live, muted)

"""The dashboard hanging in the headset: what it shows, and what a press does."""
from __future__ import annotations

import logging

import numpy as np
from shared_ui.spacing import BUTTON_ICON, BUTTON_SIZE

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
    SOURCE_LABELS,
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
    VERBOSITY_STOP,
    DashPointer,
    DashState,
    dash_actions,
    dash_height,
    dial_stops,
    format_row,
    paint_dash,
    verbosity_name,
)


def _record(message: str, *, level: int = NOTICE, source: str = SOURCE_SYSTEM) -> EventRecord:
    return EventRecord(ts=1_700_000_000.0, level=level, source=source, message=message)


def _pointer(**state):
    posted: list[str] = []
    return DashPointer(post=posted.append, state=DashState(**state)), posted


def _middle(action: str, *, dial_open: bool = False) -> tuple[int, int]:
    rect = dash_actions(dial_open=dial_open)[action]
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

    def test_a_window_wears_the_short_name_the_log_panel_gives_it(self):
        """"Sat" and "Land", not "portrait" and "landscape": the same row of
        word-buttons, reading the same, in both apps."""
        labelled = np.asarray(paint_dash(DashState(), []))
        bare = np.asarray(paint_dash(
            DashState(sources=frozenset(SOURCES) - {SOURCE_PORTRAIT}), []))

        assert SOURCE_LABELS[SOURCE_PORTRAIT] == "Sat"
        assert not np.array_equal(labelled, bare)

    def test_a_control_is_the_family_button_size_and_corner(self):
        """One rule across the family: the same square, the same corner, the
        same mark inside it, whichever app is drawing it."""
        rect = dash_actions()[QUIT_BUTTON]

        assert rect.width == rect.height == BUTTON_SIZE
        assert min(BUTTON_ICON, rect.width) == BUTTON_ICON

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

    def test_the_dial_opens_a_list_rather_than_cycling(self):
        """The desktop's is a dropdown; picking a level out of a list is the
        same gesture here, not a chip you press until it comes round."""
        pointer, posted = _pointer()
        was = pointer.state.verbosity

        pointer.press(*_middle(VERBOSITY_CHIP))

        assert pointer.state.dial_open
        assert pointer.state.verbosity == was
        assert posted == []

    def test_picking_a_level_off_the_list_sets_it_and_closes(self):
        pointer, posted = _pointer()
        pointer.press(*_middle(VERBOSITY_CHIP))

        pointer.press(*_middle(f"{VERBOSITY_STOP}ERROR", dial_open=True))

        assert pointer.state.verbosity == logging.ERROR
        assert not pointer.state.dial_open
        assert posted == []

    def test_the_list_is_only_pressable_while_it_is_open(self):
        pointer, _posted = _pointer()

        assert f"{VERBOSITY_STOP}ERROR" not in dash_actions()
        assert f"{VERBOSITY_STOP}ERROR" in dash_actions(dial_open=True)
        assert pointer.press(*_middle(f"{VERBOSITY_STOP}ERROR", dial_open=True)) != (
            f"{VERBOSITY_STOP}ERROR")

    def test_a_press_anywhere_else_closes_it(self):
        """What clicking off an open dropdown does."""
        pointer, _posted = _pointer()
        pointer.press(*_middle(VERBOSITY_CHIP))

        pointer.press(DASH_WIDTH_PX - 1, dash_height() - 1)

        assert not pointer.state.dial_open

    def test_it_covers_the_log_it_hangs_over(self):
        """A stop must be pressed before whatever row is under it."""
        opened = list(dash_actions(dial_open=True))

        assert opened[0].startswith(VERBOSITY_STOP)

    def test_the_open_list_shows(self):
        shut = np.asarray(paint_dash(DashState(), []))
        open_ = np.asarray(paint_dash(DashState(dial_open=True), []))

        assert not np.array_equal(shut, open_)

    def test_every_level_is_on_the_list(self):
        assert len(dial_stops()) == len(LEVELS_BY_NAME)

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

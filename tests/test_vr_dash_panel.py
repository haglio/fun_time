from __future__ import annotations

import logging
import winsound
from itertools import pairwise

import numpy as np
import pytest
from funestra.main_hud_painter import MainHud
from funestra.osr2_controls_host import Osr2ControlsHost
from PIL import Image, ImageDraw
from shared_ui.lettering import BOLD_FACE, WORDMARK_FACE, load_font
from shared_ui.palette import (
    BG_BUTTON,
    BG_PRIMARY,
    BG_TERTIARY,
    BLUE,
    LOADING_ACCENT,
    MAGENTA,
    PREVIEW_INK,
    TEXT_MUTED,
)
from shared_ui.preview import Preview
from shared_ui.spacing import (
    BUTTON_GAP,
    BUTTON_GROUP_GAP,
    BUTTON_ICON_INSET,
    BUTTON_PAD_H_TIGHT,
    BUTTON_SIZE_HUD,
)

from fun_time import preview_marker
from fun_time.dashboard_actions import (
    EXIT_VR,
    FMODE_TOGGLE,
    HELP_REFERENCE,
    OMNIPAUSE_TOGGLE,
    QUIT_BUTTON,
    RESET_ALL,
    VOICE_TOGGLE,
    VR_RESET,
)
from fun_time.dashboard_layout import PAD, compute_dashboard_bar_layout
from fun_time.dashboard_osr2_model import room_for_a_whole_part
from fun_time.event_log import (
    LEVELS_BY_NAME,
    MESSAGE,
    SOURCE_LABELS,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
    SOURCES,
    EventRecord,
)
from fun_time.log_panel_model import format_record
from fun_time_vr import dash_panel
from fun_time_vr.dash_panel import (
    _LABEL_PX,
    _WORDMARK_PX,
    DASH_WIDTH_PX,
    LOG_ROWS,
    QUIT_NO,
    QUIT_YES,
    VERBOSITY_DROPDOWN,
    VERBOSITY_STOP,
    DashPointer,
    DashState,
    dash_actions,
    dash_height,
    dial_stops,
    log_lines,
    osr2_part_rect,
    paint_dash,
    verbosity_name,
    with_the_osr2_part,
)
from tests.made_up_osr2_parts import a_part, a_readout, center_of

_A_PREVIEW = Preview(feature="the new reference popup")


def _record(message: str, *, level: int = MESSAGE, source: str = SOURCE_SYSTEM) -> EventRecord:
    return EventRecord(ts=1_700_000_000.0, level=level, source=source, message=message)


_LOG_FONT = load_font(_LABEL_PX)
_LOG_WIDTH = osr2_part_rect().x - 2 * PAD


def _written(rows: list[str]) -> str:
    return "".join(rows).replace(" ", "")


def _a_spot_on_the_log() -> tuple[int, int]:
    dial = dash_actions()[VERBOSITY_DROPDOWN]
    return dial.x + dial.width + 1, dash_height() - 1


def _on_the_part(x: int, y: int) -> tuple[int, int]:
    place = osr2_part_rect()
    return place.x + x, place.y + y


class _HeardByThePart:
    def __init__(self) -> None:
        self.heard: list[tuple] = []

    def press(self, x: int, y: int) -> None:
        self.heard.append(("press", x, y))

    def move(self, x: int, y: int) -> None:
        self.heard.append(("move", x, y))

    def release(self) -> None:
        self.heard.append(("release",))

    def leave(self) -> None:
        self.heard.append(("leave",))


def _apart(first, second) -> bool:
    return (first.x + first.width <= second.x or second.x + second.width <= first.x
            or first.y + first.height <= second.y or second.y + second.height <= first.y)


def _pointer(**state):
    posted: list[str] = []
    return DashPointer(post=posted.append, state=DashState(**state)), posted


def _middle(action: str, *, dial_open: bool = False,
            asking_to_quit: bool = False) -> tuple[int, int]:
    rect = dash_actions(dial_open=dial_open, asking_to_quit=asking_to_quit)[action]
    return rect.x + rect.width // 2, rect.y + rect.height // 2


def _app_name_mask(image) -> np.ndarray:
    wanted = np.asarray(LOADING_ACCENT)
    title = compute_dashboard_bar_layout().app_title
    band = np.asarray(image)[title.y:title.y + title.height,
                             title.x:title.x + title.width, :3].astype(int)
    return np.abs(band - wanted).sum(axis=2) < 60


class TestItIsTheDesktopsBar:
    def test_the_four_controls_sit_where_the_desktop_puts_them(self):
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

    def test_the_apps_mark_leads_the_bar(self):
        icon = compute_dashboard_bar_layout().app_icon
        painted = np.asarray(paint_dash(DashState(), [])).astype(int)
        region = painted[icon.y:icon.y + icon.height, icon.x:icon.x + icon.width, :3]
        inked = np.abs(region - np.asarray(MAGENTA)).sum(axis=2) < 60

        assert inked.sum() > icon.width * icon.height // 8

    def test_the_apps_name_is_written_in_the_wordmark_tone(self):
        panel = paint_dash(DashState(), [])
        title = compute_dashboard_bar_layout().app_title
        band = np.asarray(panel)[title.y:title.y + title.height,
                                 title.x:title.x + title.width, :3].astype(int)

        assert _app_name_mask(panel).sum() > 0
        assert not (np.abs(band - np.asarray(MAGENTA)).sum(axis=2) < 60).any()

    def test_a_branch_sessions_mark_and_name_are_in_the_preview_ink(self, monkeypatch, tmp_path):
        monkeypatch.setattr(preview_marker, "INKED_ICON_FOLDER", tmp_path)
        bar = compute_dashboard_bar_layout()
        panel = np.asarray(paint_dash(DashState(), [], shown_as=_A_PREVIEW)).astype(int)

        for rect in (bar.app_icon, bar.app_title):
            region = panel[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width, :3]
            assert (np.abs(region - np.asarray(PREVIEW_INK)).sum(axis=2) < 60).any()
            assert not (np.abs(region - np.asarray(MAGENTA)).sum(axis=2) < 60).any()

    def test_pointing_at_a_branch_sessions_name_says_what_it_demos(self, monkeypatch, tmp_path):
        monkeypatch.setattr(preview_marker, "INKED_ICON_FOLDER", tmp_path)
        title = compute_dashboard_bar_layout().app_title
        on_the_name = (title.x + title.width // 2, title.y + title.height // 2)
        just_below = (title.x + 2, title.y + title.height + 6)

        pointed = paint_dash(DashState(), [], hover=on_the_name, shown_as=_A_PREVIEW)
        live = paint_dash(DashState(), [], hover=on_the_name)

        assert pointed.getpixel(just_below)[:3] == BG_TERTIARY
        assert live.getpixel(just_below)[:3] != BG_TERTIARY

    def test_the_apps_name_leans_the_way_the_covers_write_it(self):
        title = compute_dashboard_bar_layout().app_title

        def written_in(face):
            image = Image.new("RGBA", (DASH_WIDTH_PX, dash_height()), (*BG_PRIMARY, 235))
            ImageDraw.Draw(image).text((title.x, title.y + 4), "Fun Time",
                                       font=load_font(_WORDMARK_PX, face), fill=LOADING_ACCENT)
            return _app_name_mask(image)

        painted = _app_name_mask(paint_dash(DashState(), []))

        assert np.array_equal(painted, written_in(WORDMARK_FACE))
        assert not np.array_equal(painted, written_in(BOLD_FACE))

    def test_a_window_wears_the_short_name_the_log_panel_gives_it(self):
        labelled = np.asarray(paint_dash(DashState(), []))
        bare = np.asarray(paint_dash(
            DashState(sources=frozenset(SOURCES) - {SOURCE_PORTRAIT}), []))

        assert SOURCE_LABELS[SOURCE_PORTRAIT] == "Sat"
        assert not np.array_equal(labelled, bare)

    def test_a_control_is_the_huds_square_with_its_mark_inset_as_a_huds_is(
            self, monkeypatch):
        asked: list[tuple[str, int]] = []
        drawn = dash_panel.pil_icon

        def recording(name: str, size: int, color):
            asked.append((name, size))
            return drawn(name, size, color)

        monkeypatch.setattr(dash_panel, "pil_icon", recording)
        paint_dash(DashState(), [])
        rect = dash_actions()[QUIT_BUTTON]

        assert rect.width == rect.height == BUTTON_SIZE_HUD
        assert ("power", BUTTON_SIZE_HUD - 2 * BUTTON_ICON_INSET) in asked


class TestTheLogWrapsAsTheDesktopsDoes:
    def test_a_record_reads_the_way_the_log_panel_writes_it(self):
        record = _record("portrait next", source=SOURCE_PORTRAIT)

        assert log_lines(DashState(), [record]) == [(format_record(record), MESSAGE)]

    def test_a_record_too_long_for_a_row_goes_on_to_the_rows_under_it_whole(self):
        record = _record(" ".join(f"word{n}" for n in range(40)))

        rows = [row for row, _level in log_lines(DashState(), [record])]

        assert len(rows) > 1
        assert all(_LOG_FONT.getlength(row) <= _LOG_WIDTH for row in rows)
        assert _written(rows) == _written([format_record(record)])

    def test_a_word_too_wide_for_a_row_keeps_a_row_of_its_own_whole(self):
        a_path = "C:" + "\\made_up_folder" * 12 + "\\scene_one.mp4"
        record = _record(f"Could not open {a_path}")

        rows = [row for row, _level in log_lines(DashState(), [record])]

        assert rows == [format_record(record).removesuffix(f" {a_path}"), a_path]

    def test_a_line_break_in_a_record_starts_a_new_row_there(self):
        record = _record("first made up line\nsecond made up line")

        rows = [row for row, _level in log_lines(DashState(), [record])]

        assert rows == format_record(record).split("\n")

    def test_it_shows_the_newest_records_newest_last_as_many_as_its_rows_hold(self):
        records = [_record(f"line {n}") for n in range(LOG_ROWS * 4)]

        rows = log_lines(DashState(), records)

        assert len(rows) >= LOG_ROWS
        assert rows == [(format_record(record), MESSAGE) for record in records[-len(rows):]]

    def test_a_record_too_long_for_the_rows_left_is_left_out_whole_rather_than_cut(self):
        room = len(log_lines(DashState(), [_record(f"line {n}") for n in range(LOG_ROWS * 4)]))
        too_long = _record(" ".join(f"word{n}" for n in range(40)))
        newer = [_record(f"line {n}") for n in range(room - 1)]

        assert log_lines(DashState(), [too_long, *newer]) == [
            (format_record(record), MESSAGE) for record in newer]


class TestTheColumnBesideTheOsr2Part:
    def test_the_bar_has_its_row_to_itself(self):
        bar = compute_dashboard_bar_layout()
        actions = dash_actions()
        buttons = [actions[action] for action in (
            QUIT_BUTTON, OMNIPAUSE_TOGGLE, HELP_REFERENCE, VOICE_TOGGLE,
            FMODE_TOGGLE, RESET_ALL, EXIT_VR, VR_RESET)]

        assert {rect.y for rect in buttons} == {bar.quit_button.y}
        assert all(actions[name].y >= bar.height for name in (VERBOSITY_DROPDOWN, *SOURCES))

    def test_the_dial_and_the_windows_run_along_a_row_of_their_own_at_the_logs_left_margin(self):
        bar = compute_dashboard_bar_layout()
        actions = dash_actions()
        dial = actions[VERBOSITY_DROPDOWN]
        buttons_of_a_row = [actions[source] for source in SOURCES]

        assert (dial.x, dial.y) == (PAD, bar.height + PAD)
        assert {rect.y for rect in buttons_of_a_row} == {dial.y}
        assert {rect.height for rect in (dial, *buttons_of_a_row)} == {BUTTON_SIZE_HUD}
        assert buttons_of_a_row[0].x == dial.x + dial.width + BUTTON_GROUP_GAP
        assert all(right.x == left.x + left.width + BUTTON_GAP
                   for left, right in pairwise(buttons_of_a_row))
        for source, button in zip(SOURCES, buttons_of_a_row, strict=True):
            label = int(load_font(_LABEL_PX).getlength(SOURCE_LABELS[source]))
            assert button.width == label + 2 * BUTTON_PAD_H_TIGHT, source

    def test_the_osr2_part_has_its_right_end_from_the_top_with_room_for_a_whole_one(self):
        part = osr2_part_rect()

        assert (part.x + part.width, part.y) == (DASH_WIDTH_PX, 0)
        assert (part.width, part.height) == room_for_a_whole_part()
        assert part.height <= dash_height()

    def test_the_column_left_of_it_is_as_wide_as_the_bar_and_holds_the_filters(self):
        last_window = dash_actions()[SOURCES[-1]]

        assert osr2_part_rect().x == compute_dashboard_bar_layout().width
        assert last_window.x + last_window.width + PAD <= osr2_part_rect().x

    def test_nothing_on_it_is_drawn_over_anything_else(self):
        placed = [*dash_actions().items(), ("the OSR2 part", osr2_part_rect())]

        for index, (name, first) in enumerate(placed):
            for other, second in placed[index + 1:]:
                assert _apart(first, second), f"{name} over {other}"

    def test_the_log_is_written_under_the_filters_and_never_under_the_osr2_part(self):
        dial = dash_actions()[VERBOSITY_DROPDOWN]
        quiet = np.asarray(paint_dash(DashState(), [])).astype(int)
        spoken = np.asarray(paint_dash(
            DashState(), [_record(" ".join(f"word{n}" for n in range(40)))])).astype(int)
        inked = np.argwhere((quiet != spoken).any(axis=2))

        assert inked[:, 0].min() >= dial.y + dial.height
        assert inked[:, 1].max() <= osr2_part_rect().x - PAD

    def test_a_word_too_wide_for_a_row_is_clipped_at_the_columns_edge(self):
        quiet = np.asarray(paint_dash(DashState(), [])).astype(int)
        clipped = np.asarray(paint_dash(DashState(), [_record("x" * 200)])).astype(int)
        inked = np.argwhere((quiet != clipped).any(axis=2))

        assert osr2_part_rect().x - PAD - 8 < inked[:, 1].max() <= osr2_part_rect().x - PAD


class TestWhatAPressDoes:
    def test_a_control_posts_the_command_the_desktop_posts(self):
        pointer, posted = _pointer()

        for action in (OMNIPAUSE_TOGGLE, HELP_REFERENCE, VOICE_TOGGLE):
            pointer.press(*_middle(action))

        assert posted == [OMNIPAUSE_TOGGLE, HELP_REFERENCE, VOICE_TOGGLE]

    def test_the_power_button_asks_first_with_the_desktops_question(self, alert_sounds):
        pointer, posted = _pointer()

        pointer.press(*_middle(QUIT_BUTTON))

        assert posted == []
        assert pointer.state.asking_to_quit
        assert alert_sounds == [winsound.MB_ICONEXCLAMATION]

    def test_yes_quits(self):
        pointer, posted = _pointer()
        pointer.press(*_middle(QUIT_BUTTON))

        pointer.press(*_middle(QUIT_YES, asking_to_quit=True))

        assert posted == [QUIT_BUTTON]
        assert not pointer.state.asking_to_quit

    @pytest.mark.parametrize("answer", ["no", "elsewhere"])
    def test_no_or_a_press_anywhere_else_leaves_fun_time_running(self, answer):
        pointer, posted = _pointer()
        pointer.press(*_middle(QUIT_BUTTON))

        pointer.press(*(_middle(QUIT_NO, asking_to_quit=True) if answer == "no"
                        else _middle(VOICE_TOGGLE)))

        assert posted == []
        assert not pointer.state.asking_to_quit

    def test_the_question_is_painted_over_the_dash_while_it_asks(self):
        rect = dash_actions(asking_to_quit=True)[QUIT_YES]
        middle = rect.y + rect.height // 2, rect.x + rect.width // 2

        asked = np.asarray(paint_dash(DashState(asking_to_quit=True), []))
        quiet = np.asarray(paint_dash(DashState(), []))

        assert not np.array_equal(asked[middle], quiet[middle])

    def test_reset_all_is_drawn_where_the_desktop_puts_it_and_posts_its_command(self):
        pointer, posted = _pointer()
        rect = compute_dashboard_bar_layout().reset_all_button
        painted = np.asarray(paint_dash(DashState(), []))

        pointer.press(*_middle(RESET_ALL))

        assert dash_actions()[RESET_ALL] == rect
        assert posted == [RESET_ALL]
        assert not np.all(painted[rect.y + rect.height // 2, rect.x:rect.x + rect.width, :3]
                          == painted[rect.y + rect.height // 2, rect.x - 1, :3])

    def test_reset_all_takes_no_press_when_every_funestra_is_at_its_defaults(self):
        pointer, posted = _pointer(nothing_to_reset=True)

        pointer.press(*_middle(RESET_ALL))

        assert posted == []

    def test_the_dial_opens_a_list_rather_than_cycling(self):
        pointer, posted = _pointer()
        was = pointer.state.verbosity

        pointer.press(*_middle(VERBOSITY_DROPDOWN))

        assert pointer.state.dial_open
        assert pointer.state.verbosity == was
        assert posted == []

    def test_picking_a_level_off_the_list_sets_it_and_closes(self):
        pointer, posted = _pointer()
        pointer.press(*_middle(VERBOSITY_DROPDOWN))

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
        pointer, _posted = _pointer()
        pointer.press(*_middle(VERBOSITY_DROPDOWN))

        pointer.press(*_a_spot_on_the_log())

        assert not pointer.state.dial_open

    def test_it_covers_the_log_it_hangs_over(self):
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

        assert pointer.press(*_a_spot_on_the_log()) is None
        assert posted == []

    def test_the_session_half_of_the_state_does_not_disturb_the_filters(self):
        pointer, _posted = _pointer()
        pointer.press(*_middle(SOURCE_MAIN))
        filtered = pointer.state.sources

        pointer.session_state(omni_paused=True, voice_active=False)

        assert pointer.state.omni_paused is True
        assert pointer.state.voice_active is False
        assert pointer.state.sources == filtered

    def test_the_session_says_when_every_funestra_is_already_at_its_defaults(self):
        pointer, posted = _pointer()

        pointer.session_state(omni_paused=False, voice_active=True, nothing_to_reset=True)
        pointer.press(*_middle(RESET_ALL))

        assert posted == []


class TestTheDial:
    def test_it_names_its_stop(self):
        assert verbosity_name(LEVELS_BY_NAME["NOTICE"]) == "NOTICE"
        assert verbosity_name(logging.ERROR) == "ERROR"


class TestItLooksLikeADropdown:
    def _column(self, image, x: int):
        return np.asarray(image)[:, x, :3]

    def test_the_closed_field_wears_an_arrow(self):
        rect = dash_actions()[VERBOSITY_DROPDOWN]
        painted = np.asarray(paint_dash(DashState(), []))
        right = painted[rect.y:rect.y + rect.height,
                        rect.x + rect.width - 20:rect.x + rect.width - 2, :3]
        ground = np.asarray(BG_BUTTON, dtype=right.dtype)

        assert not np.all(right == ground)

    def test_every_control_wears_the_huds_edge(self):
        painted = np.asarray(paint_dash(DashState(voice_active=True), []))
        actions = dash_actions()

        for action, edge in ((QUIT_BUTTON, TEXT_MUTED), (VERBOSITY_DROPDOWN, TEXT_MUTED),
                             (VOICE_TOGGLE, BLUE), (SOURCE_MAIN, BLUE)):
            rect = actions[action]
            pixel = painted[rect.y + rect.height // 2, rect.x, :3]
            assert np.array_equal(pixel, np.asarray(edge, dtype=pixel.dtype)), action

    def test_a_windows_label_is_bright_off_and_white_on(self):
        painted = np.asarray(paint_dash(
            DashState(sources=frozenset(SOURCES) - {SOURCE_MAIN}), []))
        actions = dash_actions()

        def brightest(action: str) -> int:
            rect = actions[action]
            inside = painted[rect.y + 2:rect.y + rect.height - 2,
                             rect.x + 2:rect.x + rect.width - 2, :3]
            return int(inside.min(axis=2).max())

        assert brightest(SOURCE_MAIN) > 200
        assert brightest(SOURCE_PORTRAIT) >= 250

    def test_the_level_reads_from_the_left_as_a_field_does(self):
        rect = dash_actions()[VERBOSITY_DROPDOWN]
        painted = np.asarray(paint_dash(DashState(verbosity=logging.ERROR), []))
        rows = painted[rect.y + 2:rect.y + rect.height - 2,
                       rect.x:rect.x + rect.width // 2, :3]

        assert not np.all(rows == np.asarray(BG_BUTTON, dtype=rows.dtype))

    def test_the_open_list_marks_the_chosen_row_in_blue(self):
        painted = np.asarray(paint_dash(
            DashState(verbosity=logging.ERROR, dial_open=True), []))
        chosen = dial_stops()[f"{VERBOSITY_STOP}ERROR"]
        band = painted[chosen.y + chosen.height // 2,
                       chosen.x + 2:chosen.x + chosen.width - 2, :3]

        assert (band == np.asarray(BLUE, dtype=band.dtype)).all(axis=1).any()

    def test_the_row_that_is_not_chosen_is_not(self):
        painted = np.asarray(paint_dash(
            DashState(verbosity=logging.ERROR, dial_open=True), []))
        other = dial_stops()[f"{VERBOSITY_STOP}DEBUG"]
        band = painted[other.y + other.height // 2,
                       other.x + 2:other.x + other.width - 2, :3]

        assert not (band == np.asarray(BLUE, dtype=band.dtype)).all(axis=1).any()

    def test_the_list_sits_on_the_ground_a_menu_sits_on(self):
        painted = np.asarray(paint_dash(DashState(dial_open=True), []))
        row = dial_stops()[f"{VERBOSITY_STOP}INFO"]
        pixel = painted[row.y + row.height // 2, row.x + row.width - 4, :3]

        assert np.array_equal(pixel, np.asarray(BG_TERTIARY, dtype=pixel.dtype))


class TestWhatItDraws:
    def test_it_keeps_its_size_whatever_the_log_says(self):
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
        spoken = np.asarray(paint_dash(loud_only, [_record("portrait next", level=MESSAGE)]))

        assert np.array_equal(quiet, spoken)

    def test_a_window_switched_off_stops_showing(self):
        without = DashState(sources=frozenset(SOURCES) - {SOURCE_PORTRAIT})
        quiet = np.asarray(paint_dash(without, []))
        spoken = np.asarray(
            paint_dash(without, [_record("portrait next", source=SOURCE_PORTRAIT)]))

        assert np.array_equal(quiet, spoken)

    def test_the_pause_control_says_which_way_it_goes(self):
        running = np.asarray(paint_dash(DashState(omni_paused=False), []))
        held = np.asarray(paint_dash(DashState(omni_paused=True), []))

        assert not np.array_equal(running, held)

    def test_reset_all_is_drawn_faded_when_every_funestra_is_at_its_defaults(self):
        rect = dash_actions()[RESET_ALL]

        def brightest(state: DashState) -> int:
            painted = np.asarray(paint_dash(state, []))
            return painted[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width, :3].max()

        assert brightest(DashState(nothing_to_reset=True)) <= max(TEXT_MUTED)
        assert brightest(DashState()) > max(TEXT_MUTED)

    def test_a_reset_with_nothing_to_put_back_does_not_light_under_the_ray(self):
        rect = dash_actions()[RESET_ALL]
        idle = DashState(nothing_to_reset=True)
        aimed = paint_dash(idle, [], hover=(rect.x + rect.width // 2,
                                            rect.y + rect.height // 2))

        assert np.array_equal(np.asarray(aimed), np.asarray(paint_dash(idle, [])))

    def test_the_microphone_lights_while_voice_is_live(self):
        live = np.asarray(paint_dash(DashState(voice_active=True), []))
        muted = np.asarray(paint_dash(DashState(voice_active=False), []))

        assert not np.array_equal(live, muted)

    def test_the_question_mark_lights_while_the_reference_is_open(self):
        rect = dash_actions()[HELP_REFERENCE]

        def edge(state: DashState) -> tuple[int, ...]:
            painted = np.asarray(paint_dash(state, []))
            return tuple(int(value) for value in painted[rect.y + rect.height // 2, rect.x, :3])

        assert edge(DashState(reference_open=True)) == BLUE
        assert edge(DashState()) != BLUE


class TestTheVrReset:
    def test_a_press_on_it_puts_the_funestras_back(self):
        rect = dash_actions()[VR_RESET]
        pointer, posted = _pointer()

        pointer.press(rect.x + rect.width // 2, rect.y + rect.height // 2)

        assert posted == [VR_RESET]

    def test_it_wears_a_mark_as_every_control_on_the_bar_does(self):
        rect = dash_actions()[VR_RESET]
        painted = np.asarray(paint_dash(DashState(), []))
        inside = painted[rect.y + 2:rect.y + rect.height - 2, rect.x + 2:rect.x + rect.width - 2, :3]

        assert len(np.unique(inside.reshape(-1, 3), axis=0)) > 2


class TestTheOsr2Part:
    def test_its_frame_is_drawn_in_the_room_kept_for_it(self):
        dash = paint_dash(DashState(), [])
        raw, (width, height) = Osr2ControlsHost(post=[].append).drawing(
            MainHud(main_hud_model=a_part(), drive=a_readout()))
        place = osr2_part_rect()

        shown = np.asarray(with_the_osr2_part(dash, (raw, (width, height))))

        part = np.frombuffer(raw, np.uint8).reshape(height, width, 4)
        opaque = part[..., 3] == 255
        region = shown[place.y:place.y + height, place.x:place.x + width]
        assert np.array_equal(region[opaque], part[opaque])
        assert np.array_equal(shown[:, :place.x], np.asarray(dash)[:, :place.x])

    def test_until_the_room_publishes_one_the_dash_is_as_painted(self):
        dash = paint_dash(DashState(), [])

        assert with_the_osr2_part(dash, None) is dash

    def test_a_press_on_one_of_its_buttons_posts_that_buttons_command(self):
        pointer, posted = _pointer()
        part = a_part()
        pointer.part.drawing(MainHud(main_hud_model=part))

        pointer.press(*_on_the_part(*center_of(part, "broker_panel")))

        assert posted == ["broker_panel"]

    def test_a_drag_after_a_press_on_it_reaches_it_in_its_own_pixels_until_let_go(self):
        pointer, _posted = _pointer()
        pointer.part = heard = _HeardByThePart()

        pointer.press(*_on_the_part(10, 20))
        pointer.drag(*_on_the_part(30, 40))
        pointer.release()
        pointer.drag(*_on_the_part(50, 60))

        assert heard.heard == [("press", 10, 20), ("move", 30, 40), ("release",)]

    def test_a_drag_begun_anywhere_else_never_reaches_it(self):
        pointer, _posted = _pointer()
        pointer.part = heard = _HeardByThePart()

        pointer.press(*_a_spot_on_the_log())
        pointer.drag(*_on_the_part(30, 40))
        pointer.release()

        assert heard.heard == [("release",)]

    def test_the_ray_resting_on_it_reaches_it_and_resting_anywhere_else_takes_it_off(self):
        pointer, _posted = _pointer()
        pointer.part = heard = _HeardByThePart()

        pointer.hover(_on_the_part(5, 6))
        pointer.hover(_middle(QUIT_BUTTON))
        pointer.hover(None)

        assert heard.heard == [("move", 5, 6), ("leave",), ("leave",)]

    def test_while_a_press_is_held_where_the_ray_rests_is_the_drags_to_say(self):
        pointer, _posted = _pointer()
        pointer.part = heard = _HeardByThePart()

        pointer.press(*_a_spot_on_the_log())
        pointer.hover(_on_the_part(5, 6))
        pointer.release()
        pointer.hover(_on_the_part(7, 8))

        assert heard.heard == [("release",), ("move", 7, 8)]

    def test_a_press_on_it_closes_the_open_list_as_a_press_anywhere_off_the_list_does(self):
        pointer, _posted = _pointer()
        pointer.press(*_middle(VERBOSITY_DROPDOWN))

        pointer.press(*_on_the_part(5, 6))

        assert not pointer.state.dial_open

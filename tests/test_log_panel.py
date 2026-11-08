from __future__ import annotations

import json
import logging
from itertools import pairwise
from pathlib import Path

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPointF, QSize, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QMouseEvent
from PyQt6.QtWidgets import QApplication, QToolButton
from shared_ui import colors
from shared_ui.colors import TEXT_PRIMARY, hovered
from shared_ui.fonts import SIZE_TINY
from shared_ui.icons import icon_pixmap
from shared_ui.palette import BG_BUTTON, BLUE, TEXT_MUTED
from shared_ui.spacing import (
    BUTTON_GAP,
    BUTTON_GROUP_GAP,
    BUTTON_PAD_H_TIGHT,
    BUTTON_SIZE_HUD,
    GAP_SMALL,
    MARGIN_STANDARD,
)

from fun_time.event_log import FAVORITE, MESSAGE, EventRecord, event_line
from fun_time.log_panel import (
    _COPY_BUTTON_MARGIN,
    _COPY_ICON_SIZE,
    LogPanelWidget,
    level_color,
)
from fun_time.log_panel_model import (
    MAX_RECORDS,
    LogFilter,
    LogPanelState,
    append_records,
    copy_button_position,
    format_record,
    load_ui_state,
    remember_what_was_heard,
    save_ui_state,
    visible_records,
)

ALL_SOURCES = frozenset({"main", "portrait", "landscape", "dash", "system"})


def _record(level: int = logging.INFO, source: str = "main", message: str = "hi", ts: float = 0.0):
    return EventRecord(ts=ts, level=level, source=source, message=message)


class TestLogFilter:
    def test_accepts_a_record_at_the_verbosity_floor(self):
        f = LogFilter(verbosity=logging.INFO, sources=ALL_SOURCES)
        assert f.accepts(_record(level=logging.INFO))

    def test_rejects_a_record_below_the_verbosity_floor(self):
        f = LogFilter(verbosity=MESSAGE, sources=ALL_SOURCES)
        assert not f.accepts(_record(level=logging.INFO))

    def test_rejects_a_record_from_a_muted_source(self):
        f = LogFilter(verbosity=logging.DEBUG, sources=frozenset({"landscape"}))
        assert not f.accepts(_record(source="portrait"))

    def test_accepts_a_record_from_an_enabled_source(self):
        f = LogFilter(verbosity=logging.DEBUG, sources=frozenset({"landscape"}))
        assert f.accepts(_record(source="landscape"))


class TestVisibleRecords:
    def test_keeps_only_records_the_filter_accepts_in_order(self):
        records = [
            _record(level=logging.DEBUG, source="dash", message="chatter"),
            _record(level=MESSAGE, source="portrait", message="No other seeds"),
            _record(level=logging.ERROR, source="landscape", message="boom"),
        ]
        f = LogFilter(verbosity=MESSAGE, sources=frozenset({"portrait", "landscape"}))

        assert [r.message for r in visible_records(records, f)] == ["No other seeds", "boom"]


class TestAppendRecords:
    def test_appends_in_order(self):
        buffer = [_record(message="a")]
        out = append_records(buffer, [_record(message="b")])
        assert [r.message for r in out] == ["a", "b"]

    def test_drops_the_oldest_past_the_cap_so_a_long_session_stays_bounded(self):
        buffer = [_record(message=str(i)) for i in range(MAX_RECORDS)]

        out = append_records(buffer, [_record(message="newest")])

        assert len(out) == MAX_RECORDS
        assert out[-1].message == "newest"
        assert out[0].message == "1"


class TestLevelColor:
    def test_an_ordinary_announcement_is_white(self):
        assert level_color(MESSAGE).getRgb()[:3] == (240, 240, 240)

    def test_the_favorites_and_the_funscripts_keep_the_green(self):
        assert level_color(FAVORITE).getRgb()[:3] == (48, 160, 48)

    def test_an_error_is_red_a_warning_yellow_and_chatter_muted(self):
        assert level_color(logging.ERROR).getRgb()[:3] == (255, 60, 60)
        assert level_color(logging.WARNING).getRgb()[:3] == (255, 200, 120)
        assert level_color(logging.INFO).getRgb()[:3] == (120, 120, 120)


class TestFormatRecord:
    def test_shows_the_clock_time_the_source_and_the_message(self):
        line = format_record(_record(source="landscape", message="Similar clip", ts=1_752_000_000.0))

        assert "landscape" in line
        assert "Similar clip" in line
        assert line.count(":") == 2


class TestCopyButtonPosition:
    def test_sits_at_the_rows_top_inset_from_the_viewports_right_edge(self):
        x, y = copy_button_position(
            row_top=40, viewport_width=300, viewport_height=200, button_size=16, margin=2
        )

        assert (x, y) == (300 - 16 - 2, 40 + 2)

    def test_a_row_scrolled_off_the_top_keeps_its_button_inside_the_viewport(self):
        _, y = copy_button_position(
            row_top=-30, viewport_width=300, viewport_height=200, button_size=16, margin=2
        )

        assert y == 2

    def test_a_row_running_past_the_lower_edge_keeps_its_button_inside_the_viewport(self):
        _, y = copy_button_position(
            row_top=195, viewport_width=300, viewport_height=200, button_size=16, margin=2
        )

        assert y == 200 - 16 - 2


def _event_line(message: str) -> str:
    return json.dumps({"ts": 0.0, "level": MESSAGE, "source": "system", "msg": message}) + "\n"


@pytest.fixture
def panel_factory(tmp_path: Path):
    built: list[LogPanelWidget] = []
    users_clipboard = QApplication.clipboard().text()

    def factory(messages: list[str]) -> LogPanelWidget:
        QApplication.clipboard().clear()
        log = tmp_path / "event_log.jsonl"
        log.write_text("".join(_event_line(m) for m in messages), encoding="utf-8")
        panel = LogPanelWidget(log, tmp_path / "log_panel_state.ini")
        panel.resize(300, 200)
        panel.show()
        built.append(panel)
        return panel

    yield factory

    for panel in built:
        if not sip.isdeleted(panel):
            panel.shutdown()
            panel.close()
    QApplication.clipboard().setText(users_clipboard)


def _hover_row(panel: LogPanelWidget, row: int) -> None:
    viewport = panel._list.viewport()
    point = QPointF(panel._list.visualItemRect(panel._list.item(row)).center())
    QApplication.sendEvent(
        viewport,
        QMouseEvent(
            QEvent.Type.MouseMove,
            point,
            viewport.mapToGlobal(point),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def _copy_button(panel: LogPanelWidget) -> QToolButton:
    return panel._list.viewport().findChild(QToolButton)


def _tail_advances(panel: LogPanelWidget, message: str) -> None:
    with panel._event_log.open("a", encoding="utf-8") as fh:
        fh.write(_event_line(message))
    panel._read_the_lines_other_processes_appended()


class TestHoverCopyButton:
    def test_hovering_a_row_offers_a_button_that_copies_that_line(self, panel_factory):
        panel = panel_factory(["Clip state", "No other seeds"])

        _hover_row(panel, 1)
        _copy_button(panel).click()

        assert QApplication.clipboard().text() == panel._list.item(1).text()

    def test_the_button_wears_the_familys_copy_mark_and_its_tick(self, panel_factory):
        panel = panel_factory(["Clip state"])
        _hover_row(panel, 0)
        button = _copy_button(panel)
        size = QSize(_COPY_ICON_SIZE, _COPY_ICON_SIZE)

        assert button.icon().pixmap(size).toImage() ==             icon_pixmap("copy", _COPY_ICON_SIZE, TEXT_PRIMARY).toImage()

        button.click()
        assert button.icon().pixmap(size).toImage() ==             icon_pixmap("check", _COPY_ICON_SIZE, TEXT_PRIMARY).toImage()

    def test_one_button_serves_every_row_however_many_are_rebuilt(self, panel_factory):
        panel = panel_factory([f"Clip state {n}" for n in range(60)])
        _hover_row(panel, 0)
        the_button = _copy_button(panel)

        _tail_advances(panel, "Similar clip")
        _hover_row(panel, 5)

        assert panel._list.viewport().findChildren(QToolButton) == [the_button]

    def test_a_click_on_the_copy_button_moves_the_keyboard_focus_nowhere(self, panel_factory):
        panel = panel_factory(["Clip state"])

        assert _copy_button(panel).focusPolicy() == Qt.FocusPolicy.NoFocus

    def test_the_timer_that_puts_the_copy_mark_back_dies_with_the_panel(self, panel_factory):
        panel = panel_factory(["Clip state"])
        _hover_row(panel, 0)
        _copy_button(panel).click()
        timer = panel._flash_timer
        assert timer.isActive()

        sip.delete(panel)

        assert sip.isdeleted(timer)

    def test_the_button_goes_away_once_the_cursor_leaves_the_log(self, panel_factory):
        panel = panel_factory(["Clip state"])
        _hover_row(panel, 0)
        assert _copy_button(panel).isVisible()

        QApplication.sendEvent(panel._list, QEvent(QEvent.Type.Leave))

        assert not _copy_button(panel).isVisible()

    def test_aiming_at_the_button_does_not_take_it_away(self, panel_factory):
        panel = panel_factory(["Clip state"])
        _hover_row(panel, 0)

        QApplication.sendEvent(panel._list.viewport(), QEvent(QEvent.Type.Leave))

        assert _copy_button(panel).isVisible()

    def test_a_line_wrapped_over_several_rows_keeps_its_button_where_the_line_begins(
        self, panel_factory
    ):
        panel = panel_factory([" ".join(["alpha beta gamma"] * 12)])
        row = panel._list.visualItemRect(panel._list.item(0))
        assert row.height() > 2 * QFontMetrics(panel._list.font()).height()

        _hover_row(panel, 0)

        assert _copy_button(panel).y() == row.top() + _COPY_BUTTON_MARGIN

    def test_a_line_arriving_mid_hover_does_not_strand_the_button_on_a_dead_row(
        self, panel_factory
    ):
        panel = panel_factory(["Clip state", "No other seeds"])
        _hover_row(panel, 0)

        _tail_advances(panel, "Similar clip")
        _copy_button(panel).click()

        assert QApplication.clipboard().text().endswith("Clip state")

    def test_filtering_the_hovered_row_away_takes_its_button_with_it(self, panel_factory):
        panel = panel_factory(["Clip state", "No other seeds"])
        _hover_row(panel, 0)
        assert _copy_button(panel).isVisible()

        panel._source_buttons["system"].setChecked(False)

        assert not _copy_button(panel).isVisible()


COUNTED = "not sure enough of a command (1 word) (press Enter to accept)"
WORDED = "not sure enough of: next (press Enter to accept)"


def _said(message: str, *, spoken_at: float, flashes: bool = True) -> EventRecord:
    return EventRecord(ts=0.0, level=logging.WARNING, source="system", message=message,
                       flashes=flashes, spoken_at=spoken_at)


class TestWhatWasHeard:
    def test_only_as_much_is_remembered_as_there_are_lines_for_it_to_stand_in(self):
        heard = remember_what_was_heard({1.0: "one"}, [_said("two", spoken_at=2.0),
                                                       _said("three", spoken_at=3.0)], keep=2)

        assert heard == {2.0: "two", 3.0: "three"}

    def _logged_by_count(self, panel: LogPanelWidget) -> None:
        with panel._event_log.open("a", encoding="utf-8") as fh:
            fh.write(event_line(_said(COUNTED, spoken_at=1.0, flashes=False)) + "\n")
        panel._read_the_lines_other_processes_appended()

    @pytest.mark.parametrize("words_first", [False, True])
    def test_a_line_about_something_said_lists_the_words_heard_in_place_of_their_count(
        self, panel_factory, words_first,
    ):
        panel = panel_factory(["Clip state"])

        if words_first:
            panel.show_what_was_heard([_said(WORDED, spoken_at=1.0)])
            self._logged_by_count(panel)
        else:
            self._logged_by_count(panel)
            panel.show_what_was_heard([_said(WORDED, spoken_at=1.0)])

        assert [panel._list.item(row).text().endswith(WORDED)
                for row in range(panel._list.count())] == [False, True]


class TestTheSavedViewState:
    def test_round_trips_verbosity_and_sources(self, tmp_path: Path):
        path = tmp_path / "log_panel_state.ini"
        state = LogPanelState(verbosity=logging.WARNING, sources=frozenset({"main", "dash"}))

        save_ui_state(path, state)

        assert load_ui_state(path) == state

    def test_a_missing_file_gives_message_level_and_every_source(self, tmp_path: Path):
        state = load_ui_state(tmp_path / "absent.ini")

        assert state.verbosity == MESSAGE
        assert state.sources == ALL_SOURCES

    def test_an_unreadable_file_falls_back_to_the_defaults(self, tmp_path: Path):
        path = tmp_path / "log_panel_state.ini"
        path.write_text("this is not an ini section", encoding="utf-8")

        state = load_ui_state(path)

        assert state.verbosity == MESSAGE
        assert state.sources == ALL_SOURCES

    def test_a_source_the_app_no_longer_has_is_dropped_from_the_saved_set(self, tmp_path: Path):
        path = tmp_path / "log_panel_state.ini"
        path.write_text("[panel]\nverbosity = 25\nsources = main,retired\n", encoding="utf-8")

        assert load_ui_state(path).sources == frozenset({"main"})

    def test_saving_creates_the_parent_directory(self, tmp_path: Path):
        path = tmp_path / "nested" / "log_panel_state.ini"

        save_ui_state(path, LogPanelState(verbosity=MESSAGE, sources=ALL_SOURCES))

        assert path.exists()


def test_the_level_dial_fits_its_longest_name(panel_factory):
    panel = panel_factory(["Clip state"])
    dial = panel._verbosity

    assert dial.minimumWidth() >= dial.sizeHint().width()
    assert dial.width() >= dial.sizeHint().width(), "the longest level name is cut off"


def test_the_level_dial_wears_the_huds_button_edge_ground_and_height(panel_factory):
    dial = panel_factory(["Clip state"])._verbosity
    pixel = _grabbed(dial)
    middle = dial.height() // 2

    assert dial.height() == BUTTON_SIZE_HUD
    assert pixel(0, middle) == TEXT_MUTED
    assert pixel(2, middle) == BG_BUTTON


def test_the_level_dial_shows_an_arrow_pointing_down(panel_factory):
    dial = panel_factory(["Clip state"])._verbosity
    pixel = _grabbed(dial)
    inked = [(y, sum(pixel(x, y) != BG_BUTTON for x in range(dial.width() - 16, dial.width() - 2)))
             for y in range(2, dial.height() - 2)]
    rows = [(y, count) for y, count in inked if count]

    assert rows, "the dial draws no arrow"
    apex = min(rows, key=lambda row: row[1])[0]
    widest = max(rows, key=lambda row: row[1])[0]
    assert apex > widest


def test_the_filter_row_sits_above_the_list_inside_the_panels_own_margins(panel_factory):
    panel = panel_factory(["Clip state"])
    panel.layout().activate()
    dial, toggles = panel._verbosity, list(panel._source_buttons.values())

    assert (dial.x(), dial.y()) == (MARGIN_STANDARD, MARGIN_STANDARD)
    assert {toggle.y() for toggle in toggles} == {MARGIN_STANDARD}
    assert toggles[-1].x() + toggles[-1].width() <= panel.width() - MARGIN_STANDARD
    assert panel._list.y() == MARGIN_STANDARD + BUTTON_SIZE_HUD + GAP_SMALL


def test_the_filter_row_sets_the_dial_a_group_apart_from_the_toggles(panel_factory):
    panel = panel_factory(["Clip state"])
    panel.layout().activate()
    toggles = list(panel._source_buttons.values())

    def gap(left, right) -> int:
        return right.x() - (left.x() + left.width())

    assert gap(panel._verbosity, toggles[0]) == BUTTON_GROUP_GAP
    assert all(gap(left, right) == BUTTON_GAP for left, right in pairwise(toggles))


def test_a_source_toggle_is_sized_the_way_a_huds_mode_button_is(panel_factory):
    panel = panel_factory(["Clip state"])

    for button in panel._source_buttons.values():
        label = QFontMetrics(button.font()).horizontalAdvance(button.text())
        assert button.width() == label + 2 * BUTTON_PAD_H_TIGHT, button.text()
        assert button.height() == BUTTON_SIZE_HUD
        assert button.font().pointSize() == SIZE_TINY
        assert button.autoRaise()
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextOnly
        assert button.isCheckable()


def _grabbed(widget):
    image = widget.grab().toImage()
    return lambda x, y: QColor(image.pixel(x, y)).getRgb()[:3]


def _brightest_ink(widget) -> int:
    pixel = _grabbed(widget)
    return max(min(pixel(x, y))
               for x in range(2, widget.width() - 2) for y in range(2, widget.height() - 2))


def _one_off_and_one_on(panel):
    off, on = panel._source_buttons["main"], panel._source_buttons["system"]
    off.setChecked(False)
    on.setChecked(True)
    return off, on


def test_a_word_button_wears_the_huds_mode_button_edge_and_ground(panel_factory):
    off, on = _one_off_and_one_on(panel_factory(["Clip state"]))

    for button, edge, ground in ((off, TEXT_MUTED, BG_BUTTON), (on, BLUE, BLUE)):
        pixel = _grabbed(button)
        middle = button.height() // 2
        assert pixel(0, middle) == edge, button.text()
        assert pixel(2, middle) == ground, button.text()


def test_a_word_buttons_label_is_bright_off_and_white_on(panel_factory):
    off, on = _one_off_and_one_on(panel_factory(["Clip state"]))

    assert _brightest_ink(off) > 200
    assert _brightest_ink(on) >= 250


def test_a_word_button_still_lightens_under_the_pointer(panel_factory):
    # The Qt spelling of each color, not the palette's raw triple beside it.
    sheet = panel_factory(["Clip state"])._source_buttons["system"].styleSheet()

    assert (hovered(colors.BG_BUTTON).name() in sheet)
    assert (hovered(colors.BLUE).name() in sheet)



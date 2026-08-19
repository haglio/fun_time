from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QPointF, QSize, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QToolButton

from fun_time.event_log import FAVORITE, NOTICE, EventRecord
from fun_time.log_panel import (
    MAX_RECORDS,
    LogFilter,
    LogPanelPrefs,
    LogPanelWidget,
    append_records,
    copy_button_position,
    format_record,
    level_color,
    load_prefs,
    save_prefs,
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
        f = LogFilter(verbosity=NOTICE, sources=ALL_SOURCES)
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
            _record(level=NOTICE, source="portrait", message="No other seeds"),
            _record(level=logging.ERROR, source="landscape", message="boom"),
        ]
        f = LogFilter(verbosity=NOTICE, sources=frozenset({"portrait", "landscape"}))

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
    """What each level looks like — here in the stream, and on the flash the
    notice overlay draws from the same table."""

    def test_an_ordinary_announcement_is_white(self):
        """Green used to mean "a command did something", which put it on the
        volume, the browse order and every other confirmation.  It means one thing
        now: the favorites and the funscripts."""
        assert level_color(NOTICE).getRgb()[:3] == (240, 240, 240)

    def test_the_favorites_and_the_funscripts_keep_the_green(self):
        assert level_color(FAVORITE).getRgb()[:3] == (48, 160, 48)

    def test_a_dead_end_is_still_red_and_chatter_still_muted(self):
        assert level_color(logging.ERROR).getRgb()[:3] == (255, 60, 60)
        assert level_color(logging.INFO).getRgb()[:3] == (120, 120, 120)


class TestFormatRecord:
    def test_shows_the_clock_time_the_source_and_the_message(self):
        # 2026-07-09 00:00:00 UTC + local offset — assert on structure, not the hour.
        line = format_record(_record(source="landscape", message="Similar clip", ts=1_752_000_000.0))

        assert "landscape" in line
        assert "Similar clip" in line
        assert line.count(":") == 2  # HH:MM:SS


class TestCopyButtonPosition:
    def test_sits_at_the_rows_top_inset_from_the_viewports_right_edge(self):
        x, y = copy_button_position(
            row_top=40, viewport_width=300, viewport_height=200, button_size=16, margin=2
        )

        assert (x, y) == (300 - 16 - 2, 40 + 2)

    def test_a_row_scrolled_off_the_top_keeps_its_button_inside_the_viewport(self):
        # The topmost row is usually half-scrolled under the top edge; a button
        # placed at its true top would be drawn where nobody can click it.
        _, y = copy_button_position(
            row_top=-30, viewport_width=300, viewport_height=200, button_size=16, margin=2
        )

        assert y == 2

    def test_a_row_running_past_the_bottom_keeps_its_button_inside_the_viewport(self):
        _, y = copy_button_position(
            row_top=195, viewport_width=300, viewport_height=200, button_size=16, margin=2
        )

        assert y == 200 - 16 - 2


def _event_line(message: str) -> str:
    return json.dumps({"ts": 0.0, "level": NOTICE, "source": "system", "msg": message}) + "\n"


@pytest.fixture()
def panel_factory(tmp_path: Path):
    """Build a live LogPanelWidget over an event log already holding *messages*."""
    built: list[LogPanelWidget] = []

    def factory(messages: list[str]) -> LogPanelWidget:
        QApplication.clipboard().clear()
        log = tmp_path / "event_log.jsonl"
        log.write_text("".join(_event_line(m) for m in messages), encoding="utf-8")
        panel = LogPanelWidget(log, tmp_path / "log_panel.ini")
        panel.resize(300, 200)
        panel.show()
        built.append(panel)
        return panel

    yield factory

    for panel in built:
        panel.shutdown()
        panel.close()


def _hover_row(panel: LogPanelWidget, row: int) -> None:
    """Send a real mouse-move over *row*, the way the cursor would."""
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
    """Append a line to the log the panel is tailing and let it pick the line up."""
    with panel._event_log.open("a", encoding="utf-8") as fh:
        fh.write(_event_line(message))
    panel._poll()


class TestHoverCopyButton:
    def test_hovering_a_row_offers_a_button_that_copies_that_line(self, panel_factory):
        panel = panel_factory(["Clip saved", "No other seeds"])

        _hover_row(panel, 1)
        _copy_button(panel).click()

        assert QApplication.clipboard().text() == panel._list.item(1).text()

    def test_the_button_wears_the_familys_copy_mark_and_its_tick(self, panel_factory):
        """Origenerator's copy button wears this same two-sheets drawing.

        Each app drew its own before, at its own proportions -- the drift the
        microphone had, one layer down -- so both marks now come out of
        shared_ui rather than out of a copy kept in either app.
        """
        from shared_ui.colors import TEXT_PRIMARY
        from shared_ui.icons import glyph_pixmap
        from fun_time.log_panel import _COPY_ICON_SIZE

        panel = panel_factory(["Clip saved"])
        _hover_row(panel, 0)
        button = _copy_button(panel)
        size = QSize(_COPY_ICON_SIZE, _COPY_ICON_SIZE)

        assert button.icon().pixmap(size).toImage() ==             glyph_pixmap("copy", _COPY_ICON_SIZE, TEXT_PRIMARY).toImage()

        button.click()  # and the tick it shows for a moment afterwards
        assert button.icon().pixmap(size).toImage() ==             glyph_pixmap("check", _COPY_ICON_SIZE, TEXT_PRIMARY).toImage()

    def test_the_button_goes_away_once_the_cursor_leaves_the_log(self, panel_factory):
        panel = panel_factory(["Clip saved"])
        _hover_row(panel, 0)
        assert _copy_button(panel).isVisible()  # it was there to be dismissed

        QApplication.sendEvent(panel._list, QEvent(QEvent.Type.Leave))

        assert not _copy_button(panel).isVisible()

    def test_a_line_arriving_mid_hover_does_not_strand_the_button_on_a_dead_row(
        self, panel_factory
    ):
        # Every tail advance clears and refills the list, so a button holding the
        # item it was shown for would be pointing at a destroyed row by now.
        panel = panel_factory(["Clip saved", "No other seeds"])
        _hover_row(panel, 0)

        _tail_advances(panel, "Similar clip")
        _copy_button(panel).click()

        assert QApplication.clipboard().text().endswith("Clip saved")

    def test_filtering_the_hovered_row_away_takes_its_button_with_it(self, panel_factory):
        panel = panel_factory(["Clip saved", "No other seeds"])
        _hover_row(panel, 0)
        assert _copy_button(panel).isVisible()

        panel._source_boxes["system"].setChecked(False)  # empties the list

        assert not _copy_button(panel).isVisible()


class TestPrefs:
    def test_round_trips_verbosity_and_sources(self, tmp_path: Path):
        path = tmp_path / "log_panel.ini"
        prefs = LogPanelPrefs(verbosity=logging.WARNING, sources=frozenset({"main", "dash"}))

        save_prefs(path, prefs)

        assert load_prefs(path) == prefs

    def test_a_missing_file_gives_notice_level_and_every_source(self, tmp_path: Path):
        prefs = load_prefs(tmp_path / "absent.ini")

        assert prefs.verbosity == NOTICE
        assert prefs.sources == ALL_SOURCES

    def test_an_unreadable_file_falls_back_to_the_defaults(self, tmp_path: Path):
        path = tmp_path / "log_panel.ini"
        path.write_text("this is not an ini section", encoding="utf-8")

        prefs = load_prefs(path)

        assert prefs.verbosity == NOTICE
        assert prefs.sources == ALL_SOURCES

    def test_saving_creates_the_parent_directory(self, tmp_path: Path):
        path = tmp_path / "nested" / "log_panel.ini"

        save_prefs(path, LogPanelPrefs(verbosity=NOTICE, sources=ALL_SOURCES))

        assert path.exists()


def test_a_source_toggle_comes_forward_when_it_is_on(panel_factory):
    """It brightened only its label before, which from any distance left a
    toggled source looking like an untoggled one.  Across the family, a control
    that is on sits on the lighter ground."""
    from shared_ui.colors import BG_BUTTON, BG_BUTTON_ACTIVE

    panel = panel_factory(["Clip saved"])
    sheet = panel._source_boxes["system"].styleSheet()

    assert BG_BUTTON.name() in sheet
    assert BG_BUTTON_ACTIVE.name() in sheet
    assert sheet.index(BG_BUTTON_ACTIVE.name()) > sheet.index(":checked")

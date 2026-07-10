from __future__ import annotations

import logging
from pathlib import Path

from fun_time.event_log import NOTICE, EventRecord
from fun_time.log_panel import (
    MAX_RECORDS,
    LogFilter,
    LogPanelPrefs,
    append_records,
    format_record,
    load_prefs,
    save_prefs,
    visible_records,
)

ALL_SOURCES = frozenset({"primary", "portrait", "landscape", "dash", "system"})


def _record(level: int = logging.INFO, source: str = "primary", message: str = "hi", ts: float = 0.0):
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


class TestFormatRecord:
    def test_shows_the_clock_time_the_source_and_the_message(self):
        # 2026-07-09 00:00:00 UTC + local offset — assert on structure, not the hour.
        line = format_record(_record(source="landscape", message="Similar clip", ts=1_752_000_000.0))

        assert "landscape" in line
        assert "Similar clip" in line
        assert line.count(":") == 2  # HH:MM:SS


class TestPrefs:
    def test_round_trips_verbosity_and_sources(self, tmp_path: Path):
        path = tmp_path / "log_panel.ini"
        prefs = LogPanelPrefs(verbosity=logging.WARNING, sources=frozenset({"primary", "dash"}))

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

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from fun_time.event_log import (
    LEVEL_NAMES,
    NOTICE,
    SOURCES,
    EventLogHandler,
    event_log_path,
    notice,
    read_events,
    start_event_log,
)


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "event_log.jsonl"


def _logger(log_path: Path, name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(EventLogHandler(log_path))
    return logger


class TestNoticeLevel:
    def test_notice_sits_between_info_and_warning(self):
        assert logging.INFO < NOTICE < logging.WARNING

    def test_notice_level_has_a_name(self):
        assert logging.getLevelName(NOTICE) == "NOTICE"

    def test_level_names_are_ordered_least_to_most_severe(self):
        assert LEVEL_NAMES == ("DEBUG", "INFO", "NOTICE", "WARNING", "ERROR")


class TestEventLogHandler:
    def test_emit_appends_one_json_line_per_record(self, log_path: Path):
        logger = _logger(log_path, "test.event_log.emit")

        logger.warning("disk is %s", "full")

        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["msg"] == "disk is full"
        assert payload["level"] == logging.WARNING

    def test_emit_defaults_the_source_to_system(self, log_path: Path):
        logger = _logger(log_path, "test.event_log.default_source")

        logger.info("no source given")

        payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert payload["source"] == "system"

    def test_emit_carries_an_explicit_source(self, log_path: Path):
        logger = _logger(log_path, "test.event_log.explicit_source")

        logger.info("locked", extra={"source": "landscape"})

        payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert payload["source"] == "landscape"

    def test_emit_never_raises_when_the_file_cannot_be_written(self, tmp_path: Path):
        logger = _logger(tmp_path / "missing_dir" / "deep" / "e.jsonl", "test.event_log.unwritable")
        logger.handlers[0].path = tmp_path  # a directory: opening it for append fails

        logger.info("swallowed")  # must not raise


class TestNotice:
    def test_notice_logs_at_notice_level_with_its_source(self, log_path: Path):
        logger = _logger(log_path, "test.event_log.notice")

        notice(logger, "Clip saved", source="primary")

        payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert payload["level"] == NOTICE
        assert payload["source"] == "primary"
        assert payload["msg"] == "Clip saved"


class TestReadEvents:
    def test_reads_records_and_reports_the_new_offset(self, log_path: Path):
        logger = _logger(log_path, "test.event_log.read")
        notice(logger, "Clip saved", source="primary")

        records, offset = read_events(log_path)

        assert [r.message for r in records] == ["Clip saved"]
        assert records[0].source == "primary"
        assert records[0].level == NOTICE
        assert offset == log_path.stat().st_size

    def test_a_second_read_from_the_offset_returns_only_new_records(self, log_path: Path):
        logger = _logger(log_path, "test.event_log.tail")
        notice(logger, "first", source="primary")
        _first, offset = read_events(log_path)

        notice(logger, "second", source="portrait")
        records, _offset = read_events(log_path, offset)

        assert [r.message for r in records] == ["second"]

    def test_a_missing_file_reads_as_empty(self, tmp_path: Path):
        assert read_events(tmp_path / "absent.jsonl") == ([], 0)

    def test_a_truncated_file_is_re_read_from_the_start(self, log_path: Path):
        """A new session truncates the log; a stale offset must not skip it.

        Written by hand rather than through a logger: the timestamps a logger
        stamps vary in digit count, so the two files' sizes would not reliably
        differ, and it is the shrink that read_events detects.
        """
        log_path.write_text(
            '{"ts": 1.0, "level": 25, "source": "primary", "msg": "a long line from the old session"}\n',
            encoding="utf-8",
        )
        _records, stale_offset = read_events(log_path)

        log_path.write_text(
            '{"ts": 2.0, "level": 25, "source": "primary", "msg": "new"}\n', encoding="utf-8",
        )

        records, _offset = read_events(log_path, stale_offset)
        assert [r.message for r in records] == ["new"]

    def test_a_half_written_trailing_line_is_left_for_the_next_read(self, log_path: Path):
        log_path.write_text('{"ts": 1.0, "level": 20, "source": "dash", "msg": "whole"}\n{"ts": 2.0, "lev',
                            encoding="utf-8")

        records, offset = read_events(log_path)

        assert [r.message for r in records] == ["whole"]
        assert offset < log_path.stat().st_size

    def test_a_corrupt_line_is_skipped_rather_than_killing_the_read(self, log_path: Path):
        log_path.write_text(
            'not json at all\n{"ts": 2.0, "level": 20, "source": "dash", "msg": "kept"}\n',
            encoding="utf-8",
        )

        records, _offset = read_events(log_path)

        assert [r.message for r in records] == ["kept"]


class TestStartEventLog:
    def test_start_truncates_the_previous_session_and_returns_the_path(self, tmp_path: Path):
        path = event_log_path(tmp_path)
        path.write_text('{"ts": 1.0, "level": 20, "source": "dash", "msg": "last session"}\n', encoding="utf-8")

        returned = start_event_log(tmp_path)

        assert returned == path
        assert path.read_text(encoding="utf-8") == ""

    def test_start_creates_the_state_dir_when_absent(self, tmp_path: Path):
        state_dir = tmp_path / "state"

        path = start_event_log(state_dir)

        assert path.exists()


class TestSources:
    def test_the_four_windows_plus_a_catch_all(self):
        assert SOURCES == ("primary", "portrait", "landscape", "dash", "system")

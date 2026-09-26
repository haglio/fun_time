from __future__ import annotations

import logging
import time
from pathlib import Path

from fun_time.event_log import EventRecord
from fun_time.unlogged_notices import UnloggedNotices, flash_unlogged

WORDS = "alpha beta gamma"


def _arrived(notices: UnloggedNotices, *, timeout: float = 5.0) -> list[EventRecord]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        taken = notices.take_all()
        if taken:
            return taken
        time.sleep(0.02)
    return []


def _files_holding(directory: Path, text: str) -> list[Path]:
    return [path for path in directory.rglob("*")
            if path.is_file() and text in path.read_text(encoding="utf-8", errors="replace")]


def test_a_flash_reaches_the_process_drawing_the_notices_and_no_file(tmp_path: Path):
    notices = UnloggedNotices(tmp_path)
    try:
        flash_unlogged(tmp_path, f"unrecognized voice command: {WORDS}",
                       source="portrait", level=logging.WARNING)
        [flashed] = _arrived(notices)
    finally:
        notices.stop()

    assert (flashed.message, flashed.source, flashed.level) == (
        f"unrecognized voice command: {WORDS}", "portrait", logging.WARNING)
    assert _files_holding(tmp_path, WORDS) == []


def test_a_long_run_of_words_arrives_whole_and_the_next_flash_after_it(tmp_path: Path):
    words = " ".join([WORDS] * 1000)
    notices = UnloggedNotices(tmp_path)
    try:
        flash_unlogged(tmp_path, words, source="main", level=logging.WARNING)
        long_one = _arrived(notices)
        flash_unlogged(tmp_path, WORDS, source="main", level=logging.WARNING)
        next_one = _arrived(notices)
    finally:
        notices.stop()

    assert [flashed.message for flashed in long_one + next_one] == [words, WORDS]


def test_with_nothing_drawing_notices_a_flash_is_dropped_quietly(tmp_path: Path):
    assert flash_unlogged(tmp_path, WORDS, source="main", level=logging.WARNING) is False
    assert list(tmp_path.iterdir()) == []

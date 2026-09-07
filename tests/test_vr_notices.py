"""What the headset is shown of the session's log, and where each line goes."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fun_time.event_log import (
    FAVORITE,
    NOTICE,
    SOURCE_DASH,
    SOURCE_LANDSCAPE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
)
from fun_time_vr.notices import KEPT, PRIMARY, NoticeBoard, screen_for


def _write(path: Path, message: str, level: int = NOTICE, source: str = SOURCE_SYSTEM) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"ts": 1.0, "level": level, "source": source, "msg": message}) + "\n")


def _log(tmp_path: Path) -> Path:
    path = tmp_path / "event_log.jsonl"
    path.write_text("", encoding="utf-8")
    return path


class TestWhichScreenALineBelongsTo:
    def test_a_satellites_line_flashes_over_that_satellite(self):
        assert screen_for(SOURCE_PORTRAIT) == SOURCE_PORTRAIT
        assert screen_for(SOURCE_LANDSCAPE) == SOURCE_LANDSCAPE

    def test_everything_else_belongs_to_the_primary(self):
        """The desktop falls back to the main player for a line about no one
        player; there is no dash screen in the headset to fall back to either."""
        for source in (SOURCE_MAIN, SOURCE_SYSTEM, SOURCE_DASH, "something new"):
            assert screen_for(source) == PRIMARY


class TestWhatItPicksUp:
    def test_an_announcement_written_after_it_started_is_held(self, tmp_path):
        path = _log(tmp_path)
        board = NoticeBoard(path)
        _write(path, "landscape next")

        board.pump(None, now=1.0)

        assert [line.message for line in board.lines] == ["landscape next"]

    def test_the_backlog_it_opened_on_is_not_news(self, tmp_path):
        """The session's history is not what a person is told the moment they
        put the headset on; the board is for what happens next."""
        path = _log(tmp_path)
        _write(path, "an older command")

        board = NoticeBoard(path)
        board.pump(None, now=1.0)

        assert board.lines == ()

    def test_the_diagnostic_chatter_under_a_notice_is_not_shown(self, tmp_path):
        """The same bar the desktop's toasts flash on — a notice, or louder —
        so the two surfaces say the same things."""
        path = _log(tmp_path)
        board = NoticeBoard(path)
        _write(path, "Voice command: landscape_next", level=logging.INFO)
        _write(path, "unrecognized voice command: portrait net", level=logging.ERROR)

        board.pump(None, now=1.0)

        assert [line.message for line in board.lines] == [
            "unrecognized voice command: portrait net"]

    def test_the_level_rides_along_for_the_color(self, tmp_path):
        path = _log(tmp_path)
        board = NoticeBoard(path)
        _write(path, "clip locked", level=FAVORITE)

        board.pump(None, now=1.0)

        assert [line.level for line in board.lines] == [FAVORITE]


class TestTheToastOverEachPlayer:
    def test_a_line_flashes_over_the_player_it_names(self, tmp_path):
        path = _log(tmp_path)
        board = NoticeBoard(path)
        _write(path, "portrait next", source=SOURCE_PORTRAIT)

        board.pump(None, now=1.0)

        assert board.toast(SOURCE_PORTRAIT).message == "portrait next"
        assert board.toast(SOURCE_LANDSCAPE) is None
        assert board.toast(PRIMARY) is None

    def test_the_newest_line_wins_its_screen(self, tmp_path):
        """One banner per player, as on the desktop: a second notice replaces
        the first rather than waiting its turn."""
        path = _log(tmp_path)
        board = NoticeBoard(path)
        _write(path, "portrait next", source=SOURCE_PORTRAIT)
        _write(path, "portrait lock", source=SOURCE_PORTRAIT)

        board.pump(None, now=1.0)

        assert board.toast(SOURCE_PORTRAIT).message == "portrait lock"

    def test_each_screen_flashes_its_own(self, tmp_path):
        path = _log(tmp_path)
        board = NoticeBoard(path)
        _write(path, "portrait next", source=SOURCE_PORTRAIT)
        _write(path, "skip", source=SOURCE_MAIN)

        board.pump(None, now=1.0)

        assert board.toast(SOURCE_PORTRAIT).message == "portrait next"
        assert board.toast(PRIMARY).message == "skip"

    def test_a_toast_clears_sooner_than_the_strip_keeps_it(self, tmp_path):
        """It sits over the picture, so it goes while the strip beside the
        console still has it."""
        path = _log(tmp_path)
        board = NoticeBoard(path, seconds=8.0, toast_seconds=2.0)
        _write(path, "skip", source=SOURCE_MAIN)
        board.pump(None, now=100.0)

        board.pump(None, now=102.5)

        assert board.toast(PRIMARY) is None
        assert [line.message for line in board.lines] == ["skip"]


class TestWhatItDrops:
    def test_a_line_fades_after_its_seconds(self, tmp_path):
        path = _log(tmp_path)
        board = NoticeBoard(path, seconds=5.0)
        _write(path, "landscape next")
        board.pump(None, now=100.0)

        board.pump(None, now=104.9)
        assert len(board.lines) == 1

        board.pump(None, now=105.1)
        assert board.lines == ()

    def test_a_line_is_timed_from_when_the_board_saw_it(self, tmp_path):
        """The writer stamps wall time and the pump runs on a monotonic clock, so
        a board that faded a line against the record's own timestamp would drop
        it by the difference between the two — which on this machine is days."""
        path = _log(tmp_path)
        board = NoticeBoard(path, seconds=5.0)
        _write(path, "landscape next")

        board.pump(None, now=9_999.0)

        assert len(board.lines) == 1

    def test_only_the_last_few_are_kept(self, tmp_path):
        path = _log(tmp_path)
        board = NoticeBoard(path)
        for index in range(KEPT + 3):
            _write(path, f"command {index}")

        board.pump(None, now=1.0)

        assert [line.message for line in board.lines] == [
            f"command {index}" for index in range(3, KEPT + 3)]

    def test_a_missing_log_is_simply_nothing_to_say(self, tmp_path):
        board = NoticeBoard(tmp_path / "never-written.jsonl")

        board.pump(None, now=1.0)

        assert board.lines == ()
        assert board.toast(PRIMARY) is None

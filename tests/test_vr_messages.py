from __future__ import annotations

import logging
import time
from pathlib import Path

from voice_core.commands import Recognition
from voice_core.listening import Heard

from fun_time.config import VoiceControlConfig
from fun_time.event_log import (
    FAVORITE,
    MESSAGE,
    SOURCE_DASH,
    SOURCE_LANDSCAPE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
    EventLogHandler,
    EventRecord,
    event_line,
)
from fun_time.unlogged_messages import UnloggedMessages
from fun_time.voice_control import VoiceController
from fun_time_vr.messages import MAIN, MessageBoard, screen_for


def _event(message: str, level: int = MESSAGE, source: str = SOURCE_SYSTEM,
           *, flashes: bool = True, spoken_at: float | None = None) -> EventRecord:
    return EventRecord(1.0, level, source, message, flashes, spoken_at)


def _write(path: Path, record: EventRecord) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event_line(record) + "\n")


class _Unlogged:
    def __init__(self, *records: EventRecord) -> None:
        self._waiting = list(records)
        self.stopped = False

    def take_all(self) -> list[EventRecord]:
        taken, self._waiting = self._waiting, []
        return taken

    def stop(self) -> None:
        self.stopped = True


def _log(tmp_path: Path) -> Path:
    path = tmp_path / "event_log.jsonl"
    path.write_text("", encoding="utf-8")
    return path


class TestWhichScreenALineBelongsTo:
    def test_a_satellites_line_flashes_over_that_satellite(self):
        assert screen_for(SOURCE_PORTRAIT) == SOURCE_PORTRAIT
        assert screen_for(SOURCE_LANDSCAPE) == SOURCE_LANDSCAPE

    def test_everything_else_belongs_to_the_primary(self):
        for source in (SOURCE_MAIN, SOURCE_SYSTEM, SOURCE_DASH, "something new"):
            assert screen_for(source) == MAIN


class TestWhatItPicksUp:
    def test_a_flash_from_inside_the_process_shows_at_once_and_fades_like_the_rest(self, tmp_path):
        board = MessageBoard(_log(tmp_path), message_seconds=2.0)

        board.flash("Fisheye · 158° · height 1.20", level=MESSAGE, screen=MAIN, now=1.0)

        assert board.flashed(MAIN).message == "Fisheye · 158° · height 1.20"
        board.pump(None, now=2.5)
        assert board.flashed(MAIN).message == "Fisheye · 158° · height 1.20"
        board.pump(None, now=3.1)
        assert board.flashed(MAIN) is None

    def test_an_announcement_written_after_it_started_flashes(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("landscape next"))

        board.pump(None, now=1.0)

        assert board.flashed(MAIN).message == "landscape next"

    def test_the_backlog_it_opened_on_is_not_news(self, tmp_path):
        path = _log(tmp_path)
        _write(path, _event("an older command"))

        board = MessageBoard(path)
        board.pump(None, now=1.0)

        assert board.flashed(MAIN) is None

    def test_the_diagnostic_chatter_under_a_message_is_not_shown(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("Voice command: landscape_next", level=logging.INFO))
        _write(path, _event("No video that way", level=logging.WARNING))

        board.pump(None, now=1.0)

        assert board.flashed(MAIN).message == "No video that way"

    def test_the_level_rides_along_for_the_color(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("clip locked", level=FAVORITE))

        board.pump(None, now=1.0)

        assert board.flashed(MAIN).level == FAVORITE


class TestTheBannerOverEachFunestra:
    def test_a_line_flashes_over_the_funestra_it_names(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("portrait next", source=SOURCE_PORTRAIT))

        board.pump(None, now=1.0)

        assert board.flashed(SOURCE_PORTRAIT).message == "portrait next"
        assert board.flashed(SOURCE_LANDSCAPE) is None
        assert board.flashed(MAIN) is None

    def test_the_newest_line_wins_its_screen(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("portrait next", source=SOURCE_PORTRAIT))
        _write(path, _event("portrait lock", source=SOURCE_PORTRAIT))

        board.pump(None, now=1.0)

        assert board.flashed(SOURCE_PORTRAIT).message == "portrait lock"

    def test_each_screen_flashes_its_own(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("portrait next", source=SOURCE_PORTRAIT))
        _write(path, _event("skip", source=SOURCE_MAIN))

        board.pump(None, now=1.0)

        assert board.flashed(SOURCE_PORTRAIT).message == "portrait next"
        assert board.flashed(MAIN).message == "skip"


class TestWhatItDrops:
    def test_a_banner_fades_after_its_seconds(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path, message_seconds=2.0)
        _write(path, _event("skip", source=SOURCE_MAIN))
        board.pump(None, now=100.0)

        board.pump(None, now=101.9)
        assert board.flashed(MAIN).message == "skip"

        board.pump(None, now=102.1)
        assert board.flashed(MAIN) is None


class TestWordsFlashedWithNoLogLine:
    def test_they_flash_over_the_funestra_they_name(self, tmp_path):
        board = MessageBoard(_log(tmp_path), unlogged=_Unlogged(EventRecord(
            2.0, logging.WARNING, SOURCE_PORTRAIT, "unrecognized voice command: alpha beta")))

        board.pump(None, now=1.0)

        assert board.flashed(SOURCE_PORTRAIT).message == "unrecognized voice command: alpha beta"

    def test_they_never_join_what_the_dash_lists(self, tmp_path):
        board = MessageBoard(_log(tmp_path), unlogged=_Unlogged(EventRecord(
            2.0, logging.WARNING, SOURCE_PORTRAIT, "unrecognized voice command: alpha beta")))

        board.pump(None, now=1.0)

        assert board.records == ()

    def test_the_line_logged_in_their_place_is_listed_and_not_flashed(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("unrecognized voice command (2 words)", level=logging.WARNING, flashes=False))

        board.pump(None, now=1.0)

        assert [record.message for record in board.records] == [
            "unrecognized voice command (2 words)"]
        assert board.flashed(MAIN) is None

    def test_the_dash_lists_the_words_heard_in_place_of_their_count(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path, unlogged=_Unlogged(EventRecord(
            2.0, logging.WARNING, SOURCE_SYSTEM, "unrecognized voice command: alpha beta",
            spoken_at=2.0)))
        _write(path, _event("unrecognized voice command (2 words)", level=logging.WARNING, flashes=False, spoken_at=2.0))

        board.pump(None, now=1.0)

        assert [record.message for record in board.records] == [
            "unrecognized voice command: alpha beta"]

    def test_closing_the_board_closes_their_inbox(self, tmp_path):
        unlogged = _Unlogged()

        MessageBoard(_log(tmp_path), unlogged=unlogged).close()

        assert unlogged.stopped

    def test_speech_that_is_no_command_reaches_the_headset_in_its_words_and_no_file_does(
            self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path, unlogged=UnloggedMessages(tmp_path))
        voice_log = logging.getLogger("fun_time.voice_control")
        written = EventLogHandler(path)
        voice_log.addHandler(written)
        try:
            VoiceController(
                cmd_file=tmp_path / "dashboard_cmd.txt",
                settings=VoiceControlConfig(enabled=True, model_path="unused"),
            ).handle_heard(Heard(
                Recognition(unrecognized_text="put the kettle on"),
                spoken_at=1.0, peak=2000, audio=b"", candidates={}, words_formed=True))
            deadline = time.monotonic() + 5.0
            while board.flashed(MAIN) is None and time.monotonic() < deadline:
                board.pump(None, now=1.0)
                time.sleep(0.02)
        finally:
            voice_log.removeHandler(written)
            board.close()

        assert board.flashed(MAIN).message == "unrecognized voice command: put the kettle on"
        assert [record.message for record in board.records] == [
            "unrecognized voice command: put the kettle on"]
        assert "kettle" not in path.read_text(encoding="utf-8")


    def test_a_banner_is_timed_from_when_the_board_saw_it(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path, message_seconds=5.0)
        _write(path, _event("landscape next"))

        board.pump(None, now=9_999.0)

        assert board.flashed(MAIN) is not None

    def test_a_missing_log_is_simply_nothing_to_say(self, tmp_path):
        board = MessageBoard(tmp_path / "never-written.jsonl")

        board.pump(None, now=1.0)

        assert board.flashed(MAIN) is None


class TestWhatTheChannelWorkerAsksOfIt:
    def test_the_board_can_be_closed_as_well_as_pumped(self, tmp_path):
        board = MessageBoard(_log(tmp_path))

        board.close()

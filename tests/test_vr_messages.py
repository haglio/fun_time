from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest
from shared_ui.message_shape import FAVORITE as FAVORITE_KIND
from shared_ui.message_shape import HEARD_MARK, time_to_read_ms
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
from fun_time.shown_message import ShownMessage
from fun_time.unlogged_messages import UnloggedMessages
from fun_time.voice_control import VoiceController
from fun_time_vr.messages import MAIN, MessageBoard, screen_for


def _event(message: str, level: int = MESSAGE, source: str = SOURCE_SYSTEM,
           *, flashes: bool = True, spoken_at: float | None = None, mark: str = "") -> EventRecord:
    return EventRecord(1.0, level, source, message, flashes, spoken_at, mark)


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
    def test_a_message_that_answers_what_he_said_reaches_the_headset_with_the_microphone(
            self, tmp_path):
        log = _log(tmp_path)
        board = MessageBoard(log)
        _write(log, _event("Videos only", source=SOURCE_PORTRAIT, mark=HEARD_MARK))

        board.pump(None, now=1.0)

        assert board.flashed(SOURCE_PORTRAIT).shown == ShownMessage(
            "Videos only", mark=HEARD_MARK)

    def test_a_flash_from_inside_the_process_shows_at_once_and_fades_like_the_rest(self, tmp_path):
        board = MessageBoard(_log(tmp_path))
        said = "Fisheye · 158° · height 1.20"
        lasts_s = time_to_read_ms(said) / 1000

        board.flash(ShownMessage(said), screen=MAIN, now=1.0)

        assert board.flashed(MAIN).shown.text == said
        board.pump(None, now=1.0 + lasts_s - 0.01)
        assert board.flashed(MAIN).shown.text == said
        board.pump(None, now=1.0 + lasts_s)
        assert board.flashed(MAIN) is None

    @pytest.mark.parametrize("said", ["Saved", "Including no other items, so this one stays up "
                                               "a while longer than a word or two would"])
    def test_a_flash_stays_up_as_long_as_one_on_the_desktop(self, tmp_path, said):
        path = _log(tmp_path)
        board = MessageBoard(path)
        lasts_s = time_to_read_ms(said) / 1000
        _write(path, _event(said))

        board.pump(None, now=0.0)
        board.pump(None, now=lasts_s - 0.01)
        still_up = board.flashed(MAIN)
        board.pump(None, now=lasts_s)

        assert still_up is not None
        assert board.flashed(MAIN) is None

    def test_an_announcement_written_after_it_started_flashes(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("landscape next"))

        board.pump(None, now=1.0)

        assert board.flashed(MAIN).shown.text == "landscape next"

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

        assert board.flashed(MAIN).shown.text == "No video that way"

    def test_the_level_rides_along_for_the_color(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("clip locked", level=FAVORITE))

        board.pump(None, now=1.0)

        assert board.flashed(MAIN).shown.kind == FAVORITE_KIND


class TestTheBannerOverEachFunestra:
    def test_a_line_flashes_over_the_funestra_it_names(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("portrait next", source=SOURCE_PORTRAIT))

        board.pump(None, now=1.0)

        assert board.flashed(SOURCE_PORTRAIT).shown.text == "portrait next"
        assert board.flashed(SOURCE_LANDSCAPE) is None
        assert board.flashed(MAIN) is None

    def test_the_newest_line_wins_its_screen(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("portrait next", source=SOURCE_PORTRAIT))
        _write(path, _event("portrait lock", source=SOURCE_PORTRAIT))

        board.pump(None, now=1.0)

        assert board.flashed(SOURCE_PORTRAIT).shown.text == "portrait lock"

    def test_each_screen_flashes_its_own(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("portrait next", source=SOURCE_PORTRAIT))
        _write(path, _event("skip", source=SOURCE_MAIN))

        board.pump(None, now=1.0)

        assert board.flashed(SOURCE_PORTRAIT).shown.text == "portrait next"
        assert board.flashed(MAIN).shown.text == "skip"


class TestWhatItDrops:
    def test_a_banner_fades_once_its_words_have_had_their_time_to_read(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        lasts_s = time_to_read_ms("skip") / 1000
        _write(path, _event("skip", source=SOURCE_MAIN))
        board.pump(None, now=100.0)

        board.pump(None, now=100.0 + lasts_s - 0.1)
        assert board.flashed(MAIN).shown.text == "skip"

        board.pump(None, now=100.0 + lasts_s + 0.1)
        assert board.flashed(MAIN) is None


class TestWordsFlashedWithNoLogLine:
    def test_they_flash_over_the_funestra_they_name(self, tmp_path):
        board = MessageBoard(_log(tmp_path), unlogged=_Unlogged(EventRecord(
            2.0, logging.WARNING, SOURCE_PORTRAIT, "unrecognized voice command: alpha beta")))

        board.pump(None, now=1.0)

        assert board.flashed(SOURCE_PORTRAIT).shown.text == "unrecognized voice command: alpha beta"

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

    def test_a_record_of_any_level_is_kept_for_the_dash_to_filter(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("a detail", level=logging.DEBUG, flashes=False))

        board.pump(None, now=1.0)

        assert [record.level for record in board.records] == [logging.DEBUG]

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

        assert board.flashed(MAIN).shown.text == "unrecognized voice command: put the kettle on"
        assert [record.message for record in board.records] == [
            "unrecognized voice command: put the kettle on"]
        assert "kettle" not in path.read_text(encoding="utf-8")


    def test_a_banner_is_timed_on_the_pumps_clock_not_by_the_records_wall_stamp(self, tmp_path):
        path = _log(tmp_path)
        board = MessageBoard(path)
        _write(path, _event("landscape next"))
        a_monotonic_now_far_from_the_writers_wall_time = 9_999.0

        board.pump(None, now=a_monotonic_now_far_from_the_writers_wall_time)

        assert board.flashed(MAIN) is not None

    def test_a_missing_log_is_simply_nothing_to_say(self, tmp_path):
        board = MessageBoard(tmp_path / "never-written.jsonl")

        board.pump(None, now=1.0)

        assert board.flashed(MAIN) is None


class TestWhatTheChannelWorkerAsksOfIt:
    def test_the_board_can_be_closed_as_well_as_pumped(self, tmp_path):
        board = MessageBoard(_log(tmp_path))

        board.close()

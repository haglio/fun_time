from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from fun_time.event_log import FAVORITE, MESSAGE
from fun_time.funestra_messages import FunestraMessages, read_funestra_message
from tests.bridge_configs import made_up_bridge_config


def _messages(tmp_path: Path) -> FunestraMessages:
    config = made_up_bridge_config(tmp_path)
    return FunestraMessages(replace(config, main_funestra=replace(
        config.main_funestra, notice_file=tmp_path / "main_player_notice.txt")))


def _write(path: Path, seq: int, level: str, message: str) -> None:
    path.write_text(f"seq={seq}\nlevel={level}\nmessage={message}\n", encoding="utf-8")


class TestReadFunestraNotice:
    def test_reads_a_published_message(self, tmp_path):
        path = tmp_path / "main_player_notice.txt"
        _write(path, 3, "warning", "full video not available")

        assert read_funestra_message(path) == (3.0, "warning", "full video not available")

    def test_missing_file_is_empty(self, tmp_path):
        assert read_funestra_message(tmp_path / "nope.txt") == (0, "", "")

    def test_malformed_sequence_is_empty(self, tmp_path):
        path = tmp_path / "main_player_notice.txt"
        path.write_text("seq=nonsense\nmessage=hi\n", encoding="utf-8")

        assert read_funestra_message(path) == (0, "", "")


class TestFlashFunestraNotices:
    def _loop(self, tmp_path):
        return _messages(tmp_path)

    def test_flashes_once_then_stays_quiet(self, tmp_path, caplog):
        loop = self._loop(tmp_path)
        _write(tmp_path / "main_player_notice.txt", 1, "warning", "full video not available")

        with caplog.at_level(logging.DEBUG):
            loop.flash_new()
            first = [r for r in caplog.records if "full video not available" in r.message]
            loop.flash_new()
            again = [r for r in caplog.records if "full video not available" in r.message]

        assert len(first) == 1
        assert len(again) == 1
        assert first[0].levelno == logging.WARNING

    def test_the_funestra_names_the_kind_and_this_satellite_picks_the_color(self, tmp_path, caplog):
        loop = self._loop(tmp_path)
        with caplog.at_level(logging.DEBUG):
            _write(tmp_path / "main_player_notice.txt", 1, "favorite", "funscript jump")
            loop.flash_new()
            _write(tmp_path / "main_player_notice.txt", 2, "message", "full video")
            loop.flash_new()
            _write(tmp_path / "main_player_notice.txt", 3, "warning", "money shot not available")
            loop.flash_new()

        by_message = {r.message: r.levelno for r in caplog.records}
        assert by_message["funscript jump"] == FAVORITE
        assert by_message["full video"] == MESSAGE
        assert by_message["money shot not available"] == logging.WARNING

    def test_a_new_sequence_flashes_again(self, tmp_path, caplog):
        loop = self._loop(tmp_path)
        _write(tmp_path / "main_player_notice.txt", 1, "warning", "full video not available")
        with caplog.at_level(logging.DEBUG):
            loop.flash_new()
            _write(tmp_path / "main_player_notice.txt", 2, "warning", "money shot not available")
            loop.flash_new()

        assert any("money shot not available" in r.message for r in caplog.records)

    def test_a_satellites_message_flashes_over_that_satellite(self, tmp_path, caplog):
        loop = self._loop(tmp_path)
        _write(tmp_path / "portrait_notice.txt", 1, "warning", "no funscripting ahead")
        _write(tmp_path / "landscape_notice.txt", 1, "favorite", "next funscripted")

        with caplog.at_level(logging.DEBUG):
            loop.flash_new()
            loop.flash_new()

        assert sorted((r.getMessage(), r.source, r.levelno) for r in caplog.records) == [
            ("next funscripted", "landscape", FAVORITE),
            ("no funscripting ahead", "portrait", logging.WARNING),
        ]


class TestFlashOrigeneratorsMessagesOnItsSatellite:
    def test_a_new_message_of_the_apps_flashes_on_its_satellite_with_its_level(
            self, tmp_path, caplog):
        loop = _messages(tmp_path)
        _write(tmp_path / "origenerator_portrait_notice.txt", 1, "warning", "enhanced only")

        with caplog.at_level(logging.DEBUG):
            loop.flash_new()

        assert [(r.getMessage(), r.source, r.levelno) for r in caplog.records] == [
            ("enhanced only", "portrait", logging.WARNING)]

    def test_an_older_message_of_the_apps_does_not_flash_again(self, tmp_path, caplog):
        loop = _messages(tmp_path)
        path = tmp_path / "origenerator_landscape_notice.txt"
        _write(path, 2, "notice", "f-mode on")
        with caplog.at_level(logging.DEBUG):
            loop.flash_new()
            _write(path, 1, "notice", "f-mode off")
            loop.flash_new()

        assert [r.getMessage() for r in caplog.records] == ["f-mode on"]

    def test_the_funestras_own_message_and_the_apps_each_flash(self, tmp_path, caplog):
        loop = _messages(tmp_path)
        _write(tmp_path / "portrait_notice.txt", 7, "warning", "no funscripting ahead")
        _write(tmp_path / "origenerator_portrait_notice.txt", 3, "favorite", "next funscripted")

        with caplog.at_level(logging.DEBUG):
            loop.flash_new()

        assert sorted((r.getMessage(), r.source, r.levelno) for r in caplog.records) == [
            ("next funscripted", "portrait", FAVORITE),
            ("no funscripting ahead", "portrait", logging.WARNING),
        ]


def test_a_message_from_a_previous_session_does_not_flash_on_open(tmp_path):
    _write(tmp_path / "main_player_notice.txt", 500, "warning", "stale from last time")
    _write(tmp_path / "portrait_notice.txt", 500, "warning", "stale on the side")

    loop = _messages(tmp_path)

    seen: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(record.getMessage())
    logger = logging.getLogger("fun_time.funestra_messages")
    logger.addHandler(handler)
    try:
        loop.flash_new()
    finally:
        logger.removeHandler(handler)

    assert not [m for m in seen if "stale" in m]

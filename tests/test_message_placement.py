from __future__ import annotations

import logging

from fun_time.config import MAIN_FUNESTRA_STATUS
from fun_time.dashboard_layout import Rect
from fun_time.event_log import MESSAGE, EventRecord, is_announcement
from fun_time.message_placement import (
    FunestraRects,
    funestra_window,
    message_target_rect,
)


class TestIsAnnouncement:
    def test_a_message_is_an_announcement(self):
        assert is_announcement(EventRecord(0.0, MESSAGE, "main", "Clip saved"))

    def test_a_warning_is_louder_so_it_counts_too(self):
        assert is_announcement(EventRecord(0.0, logging.WARNING, "main", "careful"))

    def test_plain_info_chatter_is_not_an_announcement(self):
        assert not is_announcement(EventRecord(0.0, logging.INFO, "main", "polled"))


class TestNoticeTargetRect:
    def _rects(self) -> FunestraRects:
        return FunestraRects(
            main=Rect(2560, 1000, 1440, 2440),
            portrait=Rect(2560, 0, 1440, 1000),
            landscape=Rect(1700, 0, 860, 1392),
            dash=Rect(0, 0, 540, 399),
        )

    def test_each_funestra_source_maps_to_its_own_rect(self):
        rects = self._rects()
        assert message_target_rect("main", rects) == rects.main
        assert message_target_rect("portrait", rects) == rects.portrait
        assert message_target_rect("landscape", rects) == rects.landscape
        assert message_target_rect("dash", rects) == rects.dash

    def test_a_system_message_has_no_funestra_so_it_falls_back_to_the_main_funestra(self):
        rects = self._rects()
        assert message_target_rect("system", rects) == rects.main

    def test_an_unknown_source_falls_back_to_the_main_funestra(self):
        rects = self._rects()
        assert message_target_rect("nonsense", rects) == rects.main


class TestFunestraWindow:
    def test_a_satellites_message_sits_on_the_window_its_status_names(self, tmp_path):
        (tmp_path / "portrait_status.txt").write_text("video=a.mp4\nwindow=4242\n", encoding="utf-8")

        assert funestra_window(tmp_path, "portrait") == 4242

    def test_the_main_funestras_window_is_read_from_its_own_status(self, tmp_path):
        (tmp_path / MAIN_FUNESTRA_STATUS).write_text("window=777\n", encoding="utf-8")

        assert (funestra_window(tmp_path, "main"), funestra_window(tmp_path, "system")) == (777, 777)

    def test_a_funestra_that_has_not_named_its_window_yet_has_none(self, tmp_path):
        (tmp_path / "landscape_status.txt").write_text("video=a.mp4\n", encoding="utf-8")

        assert (funestra_window(tmp_path, "landscape"), funestra_window(tmp_path, "portrait")) == (
            None, None)

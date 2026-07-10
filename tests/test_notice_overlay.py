from __future__ import annotations

import logging

from fun_time.dashboard_layout import Rect, Size
from fun_time.event_log import NOTICE, EventRecord
from fun_time.notice_overlay import (
    PlayerRects,
    is_announcement,
    notice_target_rect,
    top_center_position,
)


class TestIsAnnouncement:
    def test_a_notice_is_an_announcement(self):
        assert is_announcement(EventRecord(0.0, NOTICE, "primary", "Clip saved"))

    def test_a_warning_is_louder_so_it_counts_too(self):
        assert is_announcement(EventRecord(0.0, logging.WARNING, "primary", "careful"))

    def test_plain_info_chatter_is_not_an_announcement(self):
        assert not is_announcement(EventRecord(0.0, logging.INFO, "primary", "polled"))


class TestNoticeTargetRect:
    def _rects(self) -> PlayerRects:
        return PlayerRects(
            primary=Rect(2560, 1000, 1440, 2440),
            portrait=Rect(2560, 0, 1440, 1000),
            landscape=Rect(1700, 0, 860, 1392),
            dash=Rect(0, 0, 540, 399),
        )

    def test_each_player_source_maps_to_its_own_rect(self):
        rects = self._rects()
        assert notice_target_rect("primary", rects) == rects.primary
        assert notice_target_rect("portrait", rects) == rects.portrait
        assert notice_target_rect("landscape", rects) == rects.landscape
        assert notice_target_rect("dash", rects) == rects.dash

    def test_a_system_notice_has_no_player_so_it_falls_back_to_the_primary(self):
        rects = self._rects()
        assert notice_target_rect("system", rects) == rects.primary

    def test_an_unknown_source_falls_back_to_the_primary(self):
        rects = self._rects()
        assert notice_target_rect("nonsense", rects) == rects.primary


class TestTopCenterPosition:
    def test_centers_horizontally_and_sits_near_the_top(self):
        target = Rect(2560, 0, 1440, 1000)
        x, y = top_center_position(target, Size(300, 40), margin=24)

        assert x == 2560 + (1440 - 300) // 2
        assert y == 24

    def test_respects_the_targets_screen_offset(self):
        target = Rect(1700, 500, 860, 1392)
        x, y = top_center_position(target, Size(200, 40), margin=10)

        assert x == 1700 + (860 - 200) // 2
        assert y == 500 + 10

    def test_an_overlay_wider_than_its_target_is_clamped_to_the_left_edge(self):
        target = Rect(100, 0, 120, 400)
        x, _y = top_center_position(target, Size(400, 40), margin=8)

        assert x == 100

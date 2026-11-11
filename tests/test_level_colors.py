from __future__ import annotations

import logging

from shared_ui.palette import GREEN, RED, TEXT_MUTED, TEXT_PRIMARY, YELLOW

from fun_time.event_log import FAVORITE, MESSAGE
from fun_time.level_colors import level_rgb


class TestTheColorOfANotice:
    def test_each_level_has_its_own_color(self):
        assert level_rgb(MESSAGE) == TEXT_PRIMARY
        assert level_rgb(FAVORITE) == GREEN
        assert level_rgb(logging.WARNING) == YELLOW
        assert level_rgb(logging.ERROR) == RED

    def test_chatter_is_muted(self):
        assert level_rgb(logging.DEBUG) == TEXT_MUTED
        assert level_rgb(logging.INFO) == TEXT_MUTED

    def test_a_level_between_two_takes_the_loudest_it_reaches(self):
        assert level_rgb(logging.WARNING + 1) == YELLOW

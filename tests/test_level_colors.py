from __future__ import annotations

import logging

from shared_ui import message_shape
from shared_ui.palette import GREEN, RED, TEXT_MUTED, TEXT_PRIMARY, YELLOW

from fun_time.event_log import FAVORITE, MESSAGE
from fun_time.level_colors import level_rgb, message_kind


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


def test_each_level_a_message_flashes_at_is_one_of_the_familys_kinds_of_message():
    assert [message_kind(level) for level in (MESSAGE, FAVORITE, logging.WARNING,
                                              logging.ERROR)] == [
        message_shape.INFO, message_shape.FAVORITE, message_shape.WARNING, message_shape.ERROR]


def test_a_message_is_listed_in_the_color_it_flashes_in():
    for level in (MESSAGE, FAVORITE, logging.WARNING, logging.ERROR):
        assert level_rgb(level) == message_shape.MESSAGE_INK[message_kind(level)]

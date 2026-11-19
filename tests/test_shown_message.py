from __future__ import annotations

import logging

from shared_ui.message_shape import HEARD_MARK, INFO, WARNING, time_to_read_ms

from fun_time.event_log import MESSAGE, EventRecord
from fun_time.shown_message import ShownMessage


def test_a_record_is_read_as_its_words_its_kind_and_its_drawing():
    record = EventRecord(1.0, logging.WARNING, "portrait", "Nothing that way", mark=HEARD_MARK)

    assert ShownMessage.of(record) == ShownMessage("Nothing that way", WARNING, HEARD_MARK)


def test_a_record_with_no_drawing_is_an_unmarked_message_of_its_kind():
    assert ShownMessage.of(EventRecord(1.0, MESSAGE, "main", "skip")) == ShownMessage("skip", INFO)


def test_a_message_stays_up_as_long_as_its_words_take_to_read():
    said = "Including no other items, so this one stays up a while longer"

    assert ShownMessage(said).time_to_read_ms == time_to_read_ms(said)

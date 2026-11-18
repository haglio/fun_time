from __future__ import annotations

import logging

import pytest
from PySide6.QtGui import QColor
from shared_ui.icons import icon_then_text
from shared_ui.message_shape import (
    ERROR,
    FAVORITE,
    HEARD_MARK,
    INFO,
    MESSAGE_INK,
    MESSAGE_TOP_MARGIN,
    WARNING,
    time_to_read_ms,
)

from fun_time.dashboard_layout import Rect
from fun_time.event_log import FAVORITE as FAVORITE_LEVEL
from fun_time.event_log import MESSAGE, EventRecord
from fun_time.message_overlay import MessageOverlay

_A_SATELLITE = Rect(100, 50, 1000, 800)


@pytest.fixture
def overlay():
    made = MessageOverlay()
    yield made
    made.shutdown()


def _record(message: str, level: int = MESSAGE, mark: str = "") -> EventRecord:
    return EventRecord(1.0, level, "portrait", message, mark=mark)


@pytest.mark.parametrize("said", ["Videos only", "Including no other items, so this one stays "
                                                "up a while longer than a word or two would"])
def test_a_message_stays_up_as_long_as_its_words_take_to_read(overlay, said):
    overlay.flash(_record(said), _A_SATELLITE)

    assert overlay._hide_timer.interval() == time_to_read_ms(said), (
        "the reading time of its words, the same rule a message over Origenerator's own "
        "slideshow follows")


@pytest.mark.parametrize(("level", "kind"), [(MESSAGE, INFO), (logging.WARNING, WARNING),
                                             (logging.ERROR, ERROR), (FAVORITE_LEVEL, FAVORITE)])
def test_a_message_is_drawn_in_the_color_of_its_kind(overlay, level, kind):
    overlay.flash(_record("scene one", level), _A_SATELLITE)

    assert f"color: {QColor(*MESSAGE_INK[kind]).name()};" in overlay.styleSheet()


def test_a_message_that_answers_what_was_said_starts_with_the_microphone(overlay):
    overlay.flash(_record("Locked", mark=HEARD_MARK), _A_SATELLITE)

    assert overlay.text() == icon_then_text(HEARD_MARK, "Locked", color=QColor(*MESSAGE_INK[INFO]),
                                            size=overlay.fontMetrics().height())


def test_a_message_with_no_mark_is_its_plain_text(overlay):
    overlay.flash(_record("Videos only"), _A_SATELLITE)

    assert overlay.text() == "Videos only"


def test_a_message_too_long_for_its_funestra_is_elided_to_fit(overlay):
    narrow = Rect(0, 0, 300, 800)

    overlay.flash(_record("an answer that runs on and on about every change it made, far "
                          "longer than any Funestra is wide"), narrow)

    assert (overlay.width() <= narrow.width - 2 * MESSAGE_TOP_MARGIN,
            "…" in overlay.text()) == (True, True)


def test_a_message_sits_at_the_top_center_of_its_funestra(overlay):
    overlay.flash(_record("Videos only"), _A_SATELLITE)

    assert (overlay.x(), overlay.y()) == (
        _A_SATELLITE.x + (_A_SATELLITE.width - overlay.width()) // 2,
        _A_SATELLITE.y + MESSAGE_TOP_MARGIN)

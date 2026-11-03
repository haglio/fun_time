from __future__ import annotations

import pytest

from fun_time.text_field_clicks import (
    EVENT_OBJECT_DESTROY,
    EVENT_OBJECT_HIDE,
    EVENT_OBJECT_SHOW,
    EVENT_SYSTEM_CAPTUREEND,
    EVENT_SYSTEM_CAPTURESTART,
    OBJID_CARET,
    OBJID_WINDOW,
    DesktopEvents,
    Seen,
    Sighting,
    TextFieldClicks,
)

WINDOW = 7777
PROCESS = 4242


def _seen(sighting: Sighting, at: float, **kwargs) -> Seen:
    return Seen(sighting, window=kwargs.pop("window", WINDOW),
                process=kwargs.pop("process", PROCESS), at=at, **kwargs)


def press(at: float, *, text_pointer: bool = True, **kwargs) -> Seen:
    return _seen(Sighting.PRESS, at, with_the_text_pointer=text_pointer, **kwargs)


def release(at: float, **kwargs) -> Seen:
    return _seen(Sighting.RELEASE, at, **kwargs)


def caret_shown(at: float, **kwargs) -> Seen:
    return _seen(Sighting.CARET_SHOWN, at, **kwargs)


def caret_gone(at: float, **kwargs) -> Seen:
    return _seen(Sighting.CARET_GONE, at, **kwargs)


def watching_every_window() -> TextFieldClicks:
    return TextFieldClicks(watched=lambda seen: True)


def test_a_press_with_the_text_pointer_that_leaves_a_caret_showing_is_a_click_into_a_text_field():
    clicks = watching_every_window()
    for seen in (press(10.0), caret_shown(10.002), release(10.08)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.2) == WINDOW


def test_a_press_on_text_that_takes_the_caret_away_is_not():
    clicks = watching_every_window()
    for seen in (caret_shown(9.0), press(10.0), caret_gone(10.003), release(10.08)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.2) is None


def test_nothing_is_decided_while_the_button_is_still_down():
    clicks = watching_every_window()
    for seen in (press(10.0), caret_shown(10.002)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.05) is None


def test_the_caret_is_read_only_once_the_click_has_settled():
    clicks = watching_every_window()
    for seen in (caret_shown(9.0), press(10.0), release(10.08)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.1) is None
    clicks.saw(caret_gone(10.12))
    assert clicks.clicked_into(now=10.3) is None


def test_each_click_is_answered_once():
    clicks = watching_every_window()
    for seen in (press(10.0), caret_shown(10.002), release(10.08)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.2) == WINDOW
    assert clicks.clicked_into(now=10.3) is None


def test_a_caret_that_shows_a_little_late_still_counts():
    clicks = watching_every_window()
    for seen in (press(10.0), release(10.08)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.2) is None
    clicks.saw(caret_shown(10.3))
    assert clicks.clicked_into(now=10.4) == WINDOW


def test_a_caret_that_comes_long_after_the_click_is_not_the_clicks():
    clicks = watching_every_window()
    for seen in (press(10.0), release(10.08)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.7) is None
    clicks.saw(caret_shown(10.8))
    assert clicks.clicked_into(now=10.9) is None


def test_a_click_into_a_text_field_in_a_window_nobody_watches_is_not_reported():
    other_window = 9191
    clicks = TextFieldClicks(watched=lambda seen: seen.window == WINDOW)
    for seen in (press(10.0, window=other_window), caret_shown(10.002, window=other_window),
                 release(10.08, window=other_window)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.2) is None


def test_a_press_with_any_other_pointer_leaves_a_caret_that_was_already_there_alone():
    clicks = watching_every_window()
    for seen in (caret_shown(9.0), press(10.0, text_pointer=False), release(10.08)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.2) is None


def test_a_click_into_the_field_that_already_has_the_caret_is_one():
    clicks = watching_every_window()
    for seen in (caret_shown(9.0), press(10.0), release(10.08)):
        clicks.saw(seen)

    assert clicks.clicked_into(now=10.2) == WINDOW


_A_PRESS = (EVENT_SYSTEM_CAPTURESTART, OBJID_WINDOW)
_THE_CARET_SHOWN = (EVENT_OBJECT_SHOW, OBJID_CARET)


def _events(*, text_pointer=lambda: False):
    return DesktopEvents(root_of=lambda hwnd: hwnd + 1000 if hwnd < 1000 else 0,
                         process_of=lambda window: window + 1,
                         text_pointer=text_pointer, now=lambda: 12.5)


@pytest.mark.parametrize(("heard", "sighting"), [
    (_A_PRESS, Sighting.PRESS),
    ((EVENT_SYSTEM_CAPTUREEND, OBJID_WINDOW), Sighting.RELEASE),
    (_THE_CARET_SHOWN, Sighting.CARET_SHOWN),
    ((EVENT_OBJECT_HIDE, OBJID_CARET), Sighting.CARET_GONE),
    ((EVENT_OBJECT_DESTROY, OBJID_CARET), Sighting.CARET_GONE),
])
def test_each_thing_the_desktop_reports_is_seen_as_what_it_means(heard, sighting):
    events = _events()

    events.heard(heard[0], 7, heard[1])

    assert events.take() == [Seen(sighting, window=1007, process=1008, at=12.5)]


def test_a_window_being_shown_is_no_caret_and_a_caret_captured_is_no_press():
    events = _events()

    events.heard(EVENT_OBJECT_SHOW, 7, OBJID_WINDOW)
    events.heard(EVENT_SYSTEM_CAPTURESTART, 7, OBJID_CARET)

    assert events.take() == []


def test_an_event_with_no_window_is_no_sighting():
    events = _events()

    events.heard(_A_PRESS[0], 0, _A_PRESS[1])

    assert events.take() == []


def test_a_window_with_no_root_counts_as_its_own():
    events = _events()

    events.heard(_A_PRESS[0], 5000, _A_PRESS[1])

    assert events.take()[0].window == 5000


def test_only_a_press_asks_whether_the_pointer_is_the_text_pointer():
    asked = []
    events = _events(text_pointer=lambda: asked.append(True) or True)

    events.heard(_THE_CARET_SHOWN[0], 7, _THE_CARET_SHOWN[1])
    events.heard(_A_PRESS[0], 7, _A_PRESS[1])

    assert [seen.with_the_text_pointer for seen in events.take()] == [False, True]
    assert asked == [True]


def test_what_was_taken_is_not_taken_twice():
    events = _events()
    events.heard(_A_PRESS[0], 7, _A_PRESS[1])

    events.take()

    assert events.take() == []


def test_a_session_that_stops_taking_keeps_only_the_newest_sightings():
    events = _events()

    for hwnd in range(1, 400):
        events.heard(_A_PRESS[0], hwnd, _A_PRESS[1])

    taken = events.take()
    assert len(taken) == 256
    assert taken[-1].window == 1399

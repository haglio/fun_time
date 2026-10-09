from __future__ import annotations

from fun_time.text_field_clicks import Seen, Sighting, TextFieldClicks

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

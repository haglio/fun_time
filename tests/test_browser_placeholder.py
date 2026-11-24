from __future__ import annotations

import logging
import threading

from fun_time.browser_placeholder import holding_the_browsers_place
from fun_time.window_layout import WindowRect

RECT = WindowRect(x=10, y=20, width=300, height=400)


class _StandsUntilDone:
    def __init__(self) -> None:
        self.events: list[str] = []

    def stand(self, rect, *, up, done):
        self.events.append(f"up over {tuple(rect)}")
        up.set()
        done.wait()
        self.events.append("down")


def test_the_place_is_covered_while_the_browser_opens_and_uncovered_after():
    placeholder = _StandsUntilDone()

    with holding_the_browsers_place(RECT, placeholder=placeholder):
        placeholder.events.append("the browser opens and is placed")

    assert placeholder.events == [
        "up over (10, 20, 300, 400)", "the browser opens and is placed", "down"]


def test_a_placeholder_that_cannot_stand_is_said_and_the_browser_opens_anyway(caplog):
    class Breaks:
        def stand(self, rect, *, up, done):
            raise RuntimeError("no display")

    opened = []
    with caplog.at_level(logging.ERROR, logger="fun_time.browser_placeholder"), \
            holding_the_browsers_place(RECT, placeholder=Breaks()):
        opened.append(True)

    assert opened == [True]
    assert "could not stand" in caplog.text


def test_a_placeholder_slow_to_stand_holds_the_browser_back_only_so_long(caplog):
    released = threading.Event()

    class NeverUp:
        def stand(self, rect, *, up, done):
            released.wait()

    opened = []
    with caplog.at_level(logging.WARNING, logger="fun_time.browser_placeholder"), \
            holding_the_browsers_place(RECT, placeholder=NeverUp(), up_within_s=0):
        opened.append(True)
    released.set()

    assert opened == [True]
    assert "was not up within" in caplog.text

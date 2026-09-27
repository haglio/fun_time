from __future__ import annotations

from unittest.mock import patch

from fun_time.chrome_accessibility import open_chrome_window
from fun_time.rfb_slideshow import (
    SLIDESHOW_INTERVAL_S,
    RfbGlance,
    RfbSlideshow,
    glance_at,
    rfb_slideshow_on,
)

RFB_HWND = 7777
OTHER_HWND = 8888


class Rfb:
    def __init__(self) -> None:
        self.glance = RfbGlance()
        self.tabs_shown = 0
        self.stopped = False
        self.slideshow = RfbSlideshow(tabs=self, glance=lambda: self.glance)

    def show_next_tab(self) -> None:
        self.tabs_shown += 1

    def stop(self) -> None:
        self.stopped = True


def test_the_next_tab_shows_once_the_interval_has_passed():
    rfb = Rfb()

    rfb.slideshow.tick(now=0.0, held=False)
    rfb.slideshow.tick(now=SLIDESHOW_INTERVAL_S, held=False)

    assert rfb.tabs_shown == 1


def test_a_held_slideshow_stays_put_and_then_gives_the_tab_a_full_interval():
    rfb = Rfb()
    rfb.slideshow.tick(now=0.0, held=False)
    rfb.slideshow.tick(now=SLIDESHOW_INTERVAL_S, held=True)
    rfb.slideshow.tick(now=20.0, held=True)

    rfb.slideshow.tick(now=20.0 + SLIDESHOW_INTERVAL_S - 0.1, held=False)
    assert rfb.tabs_shown == 0

    rfb.slideshow.tick(now=20.0 + SLIDESHOW_INTERVAL_S, held=False)
    assert rfb.tabs_shown == 1


def test_a_minimized_or_closed_rfb_shows_no_tabs():
    rfb = Rfb()
    rfb.glance = RfbGlance(showing=False)

    rfb.slideshow.tick(now=0.0, held=False)
    rfb.slideshow.tick(now=SLIDESHOW_INTERVAL_S, held=False)

    assert rfb.tabs_shown == 0


def test_the_mouse_over_the_rfb_holds_it_until_an_interval_after_the_mouse_leaves():
    rfb = Rfb()
    rfb.slideshow.tick(now=0.0, held=False)
    rfb.glance = RfbGlance(under_cursor=True)
    rfb.slideshow.tick(now=SLIDESHOW_INTERVAL_S, held=False)
    rfb.slideshow.tick(now=15.0, held=False)
    rfb.glance = RfbGlance()

    rfb.slideshow.tick(now=15.0 + SLIDESHOW_INTERVAL_S - 0.1, held=False)
    assert rfb.tabs_shown == 0

    rfb.slideshow.tick(now=15.0 + SLIDESHOW_INTERVAL_S, held=False)
    assert rfb.tabs_shown == 1


def test_typing_in_the_rfb_he_clicked_into_holds_it_until_an_interval_after_the_last_key():
    rfb = Rfb()
    rfb.slideshow.tick(now=0.0, held=False)
    rfb.glance = RfbGlance(under_cursor=True, foreground=True, idle_s=0.0)
    rfb.slideshow.tick(now=1.0, held=False)
    rfb.glance = RfbGlance(foreground=True, idle_s=0.0)
    rfb.slideshow.tick(now=12.0, held=False)
    assert rfb.tabs_shown == 0

    rfb.glance = RfbGlance(foreground=True, idle_s=SLIDESHOW_INTERVAL_S - 0.1)
    rfb.slideshow.tick(now=12.0 + SLIDESHOW_INTERVAL_S - 0.1, held=False)
    assert rfb.tabs_shown == 0

    rfb.glance = RfbGlance(foreground=True, idle_s=SLIDESHOW_INTERVAL_S)
    rfb.slideshow.tick(now=12.0 + SLIDESHOW_INTERVAL_S, held=False)
    assert rfb.tabs_shown == 1


def test_hotkeys_pressed_while_a_lock_left_the_rfb_in_front_do_not_hold_it():
    rfb = Rfb()
    rfb.slideshow.tick(now=0.0, held=False)
    rfb.glance = RfbGlance(foreground=True, idle_s=0.0)

    rfb.slideshow.tick(now=SLIDESHOW_INTERVAL_S, held=False)

    assert rfb.tabs_shown == 1


def test_clicking_something_else_ends_his_hold_on_the_rfb():
    rfb = Rfb()
    rfb.slideshow.tick(now=0.0, held=False)
    rfb.glance = RfbGlance(under_cursor=True, foreground=True, idle_s=0.0)
    rfb.slideshow.tick(now=1.0, held=False)
    rfb.glance = RfbGlance(foreground=False, idle_s=0.0)
    rfb.slideshow.tick(now=2.0, held=False)
    rfb.glance = RfbGlance(foreground=True, idle_s=0.0)

    rfb.slideshow.tick(now=2.0 + SLIDESHOW_INTERVAL_S, held=False)

    assert rfb.tabs_shown == 1


def test_a_tab_a_lock_just_opened_gets_a_full_interval():
    rfb = Rfb()
    rfb.slideshow.tick(now=0.0, held=False)
    rfb.slideshow.restart(now=8.0)

    rfb.slideshow.tick(now=SLIDESHOW_INTERVAL_S, held=False)
    assert rfb.tabs_shown == 0

    rfb.slideshow.tick(now=8.0 + SLIDESHOW_INTERVAL_S, held=False)
    assert rfb.tabs_shown == 1


def test_stopping_the_slideshow_stops_its_tab_switching():
    rfb = Rfb()

    rfb.slideshow.stop()

    assert rfb.stopped


def test_a_glance_at_the_rfb_reads_the_cursor_the_foreground_and_his_last_input():
    with (
        patch("fun_time.rfb_slideshow.window_exists", return_value=True),
        patch("fun_time.rfb_slideshow.is_window_minimized", return_value=False),
        patch("fun_time.rfb_slideshow.window_under_cursor", return_value=RFB_HWND),
        patch("fun_time.rfb_slideshow.foreground_window", return_value=OTHER_HWND),
        patch("fun_time.rfb_slideshow.seconds_since_input", return_value=3.5),
    ):
        glance = glance_at(RFB_HWND)

    assert glance == RfbGlance(showing=True, under_cursor=True, foreground=False, idle_s=3.5)


def test_a_closed_or_minimized_rfb_is_not_showing():
    for exists, minimized in ((False, False), (True, True)):
        with (
            patch("fun_time.rfb_slideshow.window_exists", return_value=exists),
            patch("fun_time.rfb_slideshow.is_window_minimized", return_value=minimized),
            patch("fun_time.rfb_slideshow.window_under_cursor", return_value=0),
            patch("fun_time.rfb_slideshow.foreground_window", return_value=0),
            patch("fun_time.rfb_slideshow.seconds_since_input", return_value=0.0),
        ):
            assert glance_at(RFB_HWND).showing is False


def test_the_slideshow_for_a_window_glances_at_that_window_and_switches_its_tabs():
    with (
        patch("fun_time.rfb_slideshow.ChromeTabSwitcher") as switcher,
        patch("fun_time.rfb_slideshow.glance_at", return_value=RfbGlance()) as glance,
    ):
        slideshow = rfb_slideshow_on(RFB_HWND)
        slideshow.tick(now=0.0, held=False)
        slideshow.tick(now=SLIDESHOW_INTERVAL_S, held=False)

    glance.assert_called_with(RFB_HWND)
    switcher.assert_called_once_with(RFB_HWND, open_window=open_chrome_window)
    switcher.return_value.show_next_tab.assert_called_once_with()

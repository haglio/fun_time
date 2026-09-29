from __future__ import annotations

from fun_time.dashboard_actions import HEADSET_OFF, HEADSET_ON
from fun_time_vr.headset_wear import SAID_AGAIN_EVERY_S, HeadsetWear


def test_a_headset_nobody_has_put_on_yet_says_so_at_once():
    assert HeadsetWear().said(False, now=0.0) == [HEADSET_OFF]


def test_putting_it_on_and_taking_it_off_each_say_so():
    wear = HeadsetWear()
    wear.said(False, now=0.0)

    assert wear.said(True, now=0.1) == [HEADSET_ON]
    assert wear.said(False, now=0.2) == [HEADSET_OFF]


def test_nothing_more_is_said_while_nothing_changes():
    wear = HeadsetWear()
    wear.said(True, now=0.0)

    assert wear.said(True, now=SAID_AGAIN_EVERY_S / 2) == []


def test_it_is_said_again_now_and_then_for_a_room_that_missed_it_the_first_time():
    wear = HeadsetWear()
    wear.said(False, now=0.0)

    assert wear.said(False, now=SAID_AGAIN_EVERY_S) == [HEADSET_OFF]
    assert wear.said(False, now=SAID_AGAIN_EVERY_S + 0.1) == []

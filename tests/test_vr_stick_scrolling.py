from __future__ import annotations

from fun_time_vr.stick_scrolling import scroll_from_stick


class TestTheStick:
    def test_a_resting_stick_scrolls_nothing(self):
        assert scroll_from_stick(0.05, 0.1) == 0

    def test_the_stick_pushed_away_scrolls_toward_the_top(self):
        assert scroll_from_stick(1.0, 0.1) > 0 > scroll_from_stick(-1.0, 0.1)

    def test_a_stick_held_over_scrolls_further_the_longer_it_is_held(self):
        held = scroll_from_stick(1.0, 1.0)

        assert scroll_from_stick(1.0, 2.0) == held * 2

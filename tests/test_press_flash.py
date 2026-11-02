from __future__ import annotations

import pytest

from fun_time.press_flash import PRESS_FLASH_S, PressFlash


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return _Clock()


def test_a_press_is_lit_until_the_flash_has_run_its_length(clock):
    flash = PressFlash(clock=clock)

    flash.press("quit")
    clock.now += PRESS_FLASH_S - 0.01
    assert flash.lit() == frozenset({"quit"})

    clock.now += 0.01
    assert flash.lit() == frozenset()


def test_each_press_goes_out_on_its_own_time(clock):
    flash = PressFlash(clock=clock)

    flash.press("quit")
    clock.now += PRESS_FLASH_S / 2
    flash.press("voice")
    clock.now += PRESS_FLASH_S / 2

    assert flash.lit() == frozenset({"voice"})


def test_a_second_press_of_a_lit_control_starts_its_flash_over(clock):
    flash = PressFlash(clock=clock)

    flash.press("quit")
    clock.now += PRESS_FLASH_S / 2
    flash.press("quit")
    clock.now += PRESS_FLASH_S / 2

    assert flash.lit() == frozenset({"quit"})


def test_it_says_how_long_until_the_soonest_light_goes_out(clock):
    flash = PressFlash(clock=clock)
    assert flash.seconds_until_one_goes_out() is None

    flash.press("quit")
    clock.now += PRESS_FLASH_S / 2
    flash.press("voice")

    assert flash.seconds_until_one_goes_out() == pytest.approx(PRESS_FLASH_S / 2)


def test_nothing_is_left_to_wait_for_once_every_light_is_out(clock):
    flash = PressFlash(clock=clock)
    flash.press("quit")
    clock.now += PRESS_FLASH_S

    assert flash.lit() == frozenset()
    assert flash.seconds_until_one_goes_out() is None

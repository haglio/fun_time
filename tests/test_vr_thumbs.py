from __future__ import annotations

import pytest

from fun_time_vr.pointer import LEFT, RIGHT, HandInput
from fun_time_vr.thumbs import CONTROLLER_DEADZONE, DOUBLINGS_PER_S, Thumbs, strongest


class _Squeeze:
    def __init__(self, *, squeezing: bool = False) -> None:
        self.squeezing = squeezing
        self.spent = False

    def spend_the_squeeze(self) -> None:
        self.spent = True


def _hands(*, right: HandInput | None = None, left: HandInput | None = None):
    return {RIGHT: right or HandInput(), LEFT: left or HandInput()}


def test_pulling_the_stick_back_grows_the_main_player():
    thumb = Thumbs().frame(_hands(right=HandInput(stick=-1.0)), _Squeeze(), elapsed_s=0.5)

    assert thumb.grow == pytest.approx(2.0 ** (0.5 * DOUBLINGS_PER_S))


def test_either_hands_stick_does_it_and_the_harder_push_wins():
    hands = _hands(right=HandInput(stick=0.2), left=HandInput(stick=-0.5))

    thumb = Thumbs().frame(hands, _Squeeze(), elapsed_s=1.0)

    assert thumb.grow == pytest.approx(2.0 ** (0.5 * DOUBLINGS_PER_S))


def test_a_stick_drifting_inside_its_deadzone_sizes_nothing():
    drifting = _hands(right=HandInput(stick=-CONTROLLER_DEADZONE / 2))

    assert Thumbs().frame(drifting, _Squeeze(), elapsed_s=1.0).grow == 1.0


def test_with_the_trigger_held_the_stick_brings_the_players_nearer_instead():
    held = _Squeeze(squeezing=True)

    thumb = Thumbs().frame(_hands(right=HandInput(stick=-1.0)), held, elapsed_s=0.5)

    assert thumb.grow == 1.0
    assert thumb.nearer == pytest.approx(2.0 ** (0.5 * DOUBLINGS_PER_S))


def test_b_skips_forward_and_a_skips_back_once_per_press():
    thumbs = Thumbs()

    pressed = thumbs.frame(_hands(right=HandInput(forward=True)), _Squeeze(), elapsed_s=0.01)
    still_down = thumbs.frame(_hands(right=HandInput(forward=True)), _Squeeze(), elapsed_s=0.01)
    back = thumbs.frame(_hands(right=HandInput(back=True)), _Squeeze(), elapsed_s=0.01)

    assert pressed.commands == ("main_nudge_next",)
    assert still_down.commands == ()
    assert back.commands == ("main_nudge_prev",)


def test_with_the_trigger_held_b_and_a_jump_between_scenes_instead():
    thumbs = Thumbs()
    held = _Squeeze(squeezing=True)

    forward = thumbs.frame(_hands(right=HandInput(forward=True)), held, elapsed_s=0.01)
    thumbs.frame(_hands(), held, elapsed_s=0.01)
    back = thumbs.frame(_hands(right=HandInput(back=True)), held, elapsed_s=0.01)

    assert forward.commands == ("main_scene_next",)
    assert back.commands == ("main_scene_prev",)


def test_a_trigger_used_for_the_stick_or_a_button_is_spent_and_one_only_held_is_not():
    only_held, stick_used, button_used = (_Squeeze(squeezing=True) for _ in range(3))

    Thumbs().frame(_hands(), only_held, elapsed_s=0.01)
    Thumbs().frame(_hands(right=HandInput(stick=0.9)), stick_used, elapsed_s=0.01)
    Thumbs().frame(_hands(left=HandInput(back=True)), button_used, elapsed_s=0.01)

    assert not only_held.spent
    assert stick_used.spent
    assert button_used.spent


def test_the_stick_coming_back_to_rest_settles_what_it_moved_once():
    thumbs = Thumbs()

    pushing = thumbs.frame(_hands(right=HandInput(stick=-0.8)), _Squeeze(), elapsed_s=0.01)
    let_go = thumbs.frame(_hands(), _Squeeze(), elapsed_s=0.01)
    resting = thumbs.frame(_hands(), _Squeeze(), elapsed_s=0.01)

    assert not pushing.settled
    assert let_go.settled
    assert not resting.settled


class TestWhichHandsStickCounts:
    """Both hands' sticks are read, and their two readings are combined here
    rather than by the runtime: left to the runtime's own rule, the left stick
    answered in a single direction."""

    def test_a_hand_at_rest_does_not_cancel_the_other(self):
        assert strongest([0.0, -0.8]) == -0.8
        assert strongest([-0.8, 0.0]) == -0.8

    def test_either_direction_carries(self):
        assert strongest([0.0, 0.9]) == 0.9
        assert strongest([0.0, -0.9]) == -0.9

    def test_the_harder_push_wins_whichever_way_it_leans(self):
        assert strongest([0.3, -0.9]) == -0.9
        assert strongest([-0.3, 0.9]) == 0.9

    def test_two_hands_at_rest_move_nothing(self):
        assert strongest([0.0, 0.0]) == 0.0
        assert strongest([]) == 0.0

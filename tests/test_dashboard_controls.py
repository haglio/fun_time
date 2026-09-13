from __future__ import annotations

from shared_ui.palette import BLUE

from fun_time.dashboard_actions import HELP_REFERENCE, VR_RESET
from fun_time.dashboard_controls import bar_controls
from fun_time.dashboard_layout import compute_dashboard_bar_layout


def _lit(action: str, **state):
    controls = bar_controls(compute_dashboard_bar_layout(), **state)
    return next(control.lit for control in controls if control.action == action)


def _actions(**state) -> list[str]:
    return [control.action
            for control in bar_controls(compute_dashboard_bar_layout(), **state)]


def test_the_vr_reset_is_on_the_bar_in_the_headset_alone():
    assert VR_RESET in _actions(in_vr=True)
    assert VR_RESET not in _actions()


def test_the_question_mark_is_lit_blue_exactly_while_the_reference_is_open():
    assert _lit(HELP_REFERENCE, reference_open=True) == BLUE
    assert _lit(HELP_REFERENCE, reference_open=False) is None

from __future__ import annotations

from shared_ui.palette import BLUE

from fun_time.dashboard_actions import ENTER_VR, EXIT_VR, HELP_REFERENCE, VR_RESET
from fun_time.dashboard_controls import bar_controls
from fun_time.dashboard_layout import compute_dashboard_bar_layout


def _lit(action: str, **state):
    controls = bar_controls(compute_dashboard_bar_layout(), **state)
    return next(control.lit for control in controls if control.action == action)


def _actions(**state) -> list[str]:
    return [control.action
            for control in bar_controls(compute_dashboard_bar_layout(), **state)]


def _dim(action: str, **state) -> bool:
    controls = bar_controls(compute_dashboard_bar_layout(), **state)
    return next(control.dim for control in controls if control.action == action)


def test_the_vr_reset_is_on_the_bar_in_the_headset_alone():
    assert VR_RESET in _actions(in_vr=True)
    assert VR_RESET not in _actions()


def test_the_question_mark_is_lit_blue_exactly_while_the_reference_is_open():
    assert _lit(HELP_REFERENCE, reference_open=True) == BLUE
    assert _lit(HELP_REFERENCE, reference_open=False) is None
def test_entering_vr_is_offered_only_once_the_headset_is_ready():
    """Pimax takes about eleven seconds to wake, and nothing can be drawn in
    the headset until it has: pressing Enter VR before then spends the whole
    crossing on a blank headset."""
    assert _dim(ENTER_VR, headset_ready=False) is True
    assert _dim(ENTER_VR, headset_ready=True) is False


def test_leaving_vr_is_always_offered():
    assert _dim(EXIT_VR, in_vr=True, headset_ready=False) is False

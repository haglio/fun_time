from __future__ import annotations

from fun_time.genau_plan import build_genau_toggle_plan


# --- toggle-active: activation ---


def test_toggle_activates_from_inactive():
    plan = build_genau_toggle_plan(genau_mode_on=False, omni_paused=False)

    assert plan.target_active is True
    assert plan.is_transition is True
    assert "activated" in plan.log_message


def test_toggle_deactivates_from_active():
    plan = build_genau_toggle_plan(genau_mode_on=True, omni_paused=False)

    assert plan.target_active is False
    assert plan.is_transition is True
    assert "deactivated" in plan.log_message


def test_toggle_during_omnipause_no_transition():
    plan = build_genau_toggle_plan(genau_mode_on=False, omni_paused=True)

    assert plan.target_active is True
    assert plan.is_transition is False
    assert "omnipaused" in plan.log_message

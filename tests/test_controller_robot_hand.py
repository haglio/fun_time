from __future__ import annotations

from fun_time.controller_robot_hand import build_robot_hand_plan


def test_sync_state_enters_robot_hand_mode_when_effective_mode_turns_on():
    plan = build_robot_hand_plan(
        "sync-state",
        robot_hand_mode_on=False,
        enabled=True,
        mode_state_on=True,
        omni_paused=False,
    )

    assert plan.next_robot_hand_mode is True
    assert plan.enforce_active is True
    assert plan.is_transition is True
    assert plan.log_message == "Entering Robot Hand mode"


def test_sync_state_leaves_robot_hand_mode_when_effective_mode_turns_off():
    plan = build_robot_hand_plan(
        "sync-state",
        robot_hand_mode_on=True,
        enabled=False,
        mode_state_on=True,
        omni_paused=False,
    )

    assert plan.next_robot_hand_mode is False
    assert plan.enforce_active is False
    assert plan.is_transition is True
    assert plan.log_message == "Leaving Robot Hand mode"


def test_sync_state_only_updates_dashboard_when_omni_paused():
    plan = build_robot_hand_plan(
        "sync-state",
        robot_hand_mode_on=True,
        enabled=True,
        mode_state_on=True,
        omni_paused=True,
    )

    assert plan.next_robot_hand_mode is True
    assert plan.enforce_outputs is False
    assert plan.log_message == ""


def test_toggle_enabled_flips_enabled_state_and_triggers_sync():
    plan = build_robot_hand_plan(
        "toggle-enabled",
        robot_hand_mode_on=False,
        enabled=True,
        mode_state_on=True,
        omni_paused=False,
    )

    assert plan.write_enabled is True
    assert plan.enabled_value is False
    assert plan.next_robot_hand_mode is False
    assert plan.enforce_active is False
    assert plan.is_transition is False
    assert plan.log_message == "Robot Hand hotkey: disabled"

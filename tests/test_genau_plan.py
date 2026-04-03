from __future__ import annotations

from fun_time.genau_plan import build_genau_plan


# --- sync-state: entering transition ---


def test_sync_enters_genau_mode_when_effective_mode_turns_on():
    plan = build_genau_plan(
        "sync-state",
        genau_mode_on=False,
        enabled=True,
        mode_state_on=True,
        omni_paused=False,
    )

    assert plan.target_active is True
    assert plan.is_transition is True
    assert plan.write_enabled is False
    assert plan.log_message == "Entering Genau mode"


# --- sync-state: leaving transition ---


def test_sync_leaves_genau_mode_when_effective_mode_turns_off():
    plan = build_genau_plan(
        "sync-state",
        genau_mode_on=True,
        enabled=True,
        mode_state_on=False,
        omni_paused=False,
    )

    assert plan.target_active is False
    assert plan.is_transition is True
    assert plan.write_enabled is False
    assert plan.log_message == "Leaving Genau mode"


def test_sync_leaves_when_disabled():
    plan = build_genau_plan(
        "sync-state",
        genau_mode_on=True,
        enabled=False,
        mode_state_on=True,
        omni_paused=False,
    )

    assert plan.target_active is False
    assert plan.is_transition is True


# --- sync-state: steady state (THE KEY REGRESSION TEST) ---


def test_sync_steady_state_active_is_not_a_transition():
    plan = build_genau_plan(
        "sync-state",
        genau_mode_on=True,
        enabled=True,
        mode_state_on=True,
        omni_paused=False,
    )

    assert plan.target_active is True
    assert plan.is_transition is False
    assert plan.log_message == ""


def test_sync_steady_state_inactive_is_not_a_transition():
    plan = build_genau_plan(
        "sync-state",
        genau_mode_on=False,
        enabled=True,
        mode_state_on=False,
        omni_paused=False,
    )

    assert plan.target_active is False
    assert plan.is_transition is False


# --- sync-state: omnipause gate ---


def test_sync_during_omnipause_is_noop():
    plan = build_genau_plan(
        "sync-state",
        genau_mode_on=True,
        enabled=True,
        mode_state_on=True,
        omni_paused=True,
    )

    assert plan.target_active is True  # preserves current state
    assert plan.is_transition is False
    assert plan.write_enabled is False
    assert plan.log_message == ""


def test_sync_during_omnipause_does_not_enter():
    """Even if effective mode just turned on, omnipause blocks the transition."""
    plan = build_genau_plan(
        "sync-state",
        genau_mode_on=False,
        enabled=True,
        mode_state_on=True,
        omni_paused=True,
    )

    assert plan.target_active is False  # preserves current state, not effective
    assert plan.is_transition is False


# --- toggle-enabled ---


def test_toggle_disables_and_leaves_mode():
    plan = build_genau_plan(
        "toggle-enabled",
        genau_mode_on=True,
        enabled=True,
        mode_state_on=True,
        omni_paused=False,
    )

    assert plan.write_enabled is True
    assert plan.enabled_value is False
    assert plan.target_active is False
    assert plan.is_transition is True
    assert "disabled" in plan.log_message


def test_toggle_enables_and_enters_mode_when_auto_on():
    plan = build_genau_plan(
        "toggle-enabled",
        genau_mode_on=False,
        enabled=False,
        mode_state_on=True,
        omni_paused=False,
    )

    assert plan.write_enabled is True
    assert plan.enabled_value is True
    assert plan.target_active is True
    assert plan.is_transition is True
    assert "enabled" in plan.log_message


def test_toggle_enables_but_no_transition_when_auto_off():
    plan = build_genau_plan(
        "toggle-enabled",
        genau_mode_on=False,
        enabled=False,
        mode_state_on=False,
        omni_paused=False,
    )

    assert plan.write_enabled is True
    assert plan.enabled_value is True
    assert plan.target_active is False
    assert plan.is_transition is False


def test_toggle_disable_during_omnipause_deactivates_mode():
    """Disabling Genau during omnipause must set target_active=False
    so that leaving omnipause does not re-activate the Genau window."""
    plan = build_genau_plan(
        "toggle-enabled",
        genau_mode_on=True,
        enabled=True,
        mode_state_on=True,
        omni_paused=True,
    )

    assert plan.write_enabled is True
    assert plan.enabled_value is False
    assert plan.target_active is False  # mode must reflect the disable
    assert plan.is_transition is False


def test_toggle_enable_during_omnipause_activates_mode_when_auto_on():
    """Re-enabling Genau during omnipause with mode_state on must
    set target_active=True so leaving omnipause restores Genau."""
    plan = build_genau_plan(
        "toggle-enabled",
        genau_mode_on=False,
        enabled=False,
        mode_state_on=True,
        omni_paused=True,
    )

    assert plan.write_enabled is True
    assert plan.enabled_value is True
    assert plan.target_active is True
    assert plan.is_transition is False


# --- invalid action ---


def test_invalid_action_raises():
    import pytest

    with pytest.raises(ValueError, match="Unsupported"):
        build_genau_plan(
            "bogus",
            genau_mode_on=False,
            enabled=True,
            mode_state_on=False,
            omni_paused=False,
        )

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RobotHandPlan:
    target_active: bool
    is_transition: bool
    write_enabled: bool
    enabled_value: bool
    log_message: str


def build_robot_hand_plan(
    action: str,
    *,
    robot_hand_mode_on: bool,
    enabled: bool,
    mode_state_on: bool,
    omni_paused: bool,
) -> RobotHandPlan:
    if action == "sync-state":
        if omni_paused:
            return RobotHandPlan(
                target_active=robot_hand_mode_on,
                is_transition=False,
                write_enabled=False,
                enabled_value=enabled,
                log_message="",
            )

        effective = enabled and mode_state_on
        entering = effective and not robot_hand_mode_on
        leaving = not effective and robot_hand_mode_on

        if entering:
            msg = "Entering Robot Hand mode"
        elif leaving:
            msg = "Leaving Robot Hand mode"
        else:
            msg = ""

        return RobotHandPlan(
            target_active=effective,
            is_transition=entering or leaving,
            write_enabled=False,
            enabled_value=enabled,
            log_message=msg,
        )

    if action == "toggle-enabled":
        next_enabled = not enabled

        if omni_paused:
            return RobotHandPlan(
                target_active=robot_hand_mode_on,
                is_transition=False,
                write_enabled=True,
                enabled_value=next_enabled,
                log_message=f"Robot Hand hotkey: {'enabled' if next_enabled else 'disabled'}",
            )

        effective = next_enabled and mode_state_on
        return RobotHandPlan(
            target_active=effective,
            is_transition=robot_hand_mode_on != effective,
            write_enabled=True,
            enabled_value=next_enabled,
            log_message=f"Robot Hand hotkey: {'enabled' if next_enabled else 'disabled'}",
        )

    raise ValueError(f"Unsupported robot hand action: {action}")

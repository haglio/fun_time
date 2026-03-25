from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RobotHandPlan:
    write_enabled: bool
    enabled_value: bool
    next_robot_hand_mode: bool
    enforce_outputs: bool
    enforce_active: bool
    is_transition: bool
    log_message: str


def build_robot_hand_plan(
    action: str,
    *,
    robot_hand_mode_on: bool,
    enabled: bool,
    mode_state_on: bool,
    omni_paused: bool,
) -> RobotHandPlan:
    effective_mode_on = enabled and mode_state_on

    if action == "toggle-enabled":
        next_enabled = not enabled
        effective_mode_on = next_enabled and mode_state_on
        if omni_paused:
            return RobotHandPlan(
                write_enabled=True,
                enabled_value=next_enabled,
                next_robot_hand_mode=robot_hand_mode_on,
                enforce_outputs=False,
                enforce_active=robot_hand_mode_on,
                is_transition=False,
                log_message=f"Robot Hand hotkey: {'enabled' if next_enabled else 'disabled'}",
            )
        return RobotHandPlan(
            write_enabled=True,
            enabled_value=next_enabled,
            next_robot_hand_mode=effective_mode_on,
            enforce_outputs=True,
            enforce_active=effective_mode_on,
            is_transition=robot_hand_mode_on != effective_mode_on,
            log_message=f"Robot Hand hotkey: {'enabled' if next_enabled else 'disabled'}",
        )

    if action == "sync-state":
        if omni_paused:
            return RobotHandPlan(
                write_enabled=False,
                enabled_value=enabled,
                next_robot_hand_mode=robot_hand_mode_on,
                enforce_outputs=False,
                enforce_active=robot_hand_mode_on,
                is_transition=False,
                log_message="",
            )

        if effective_mode_on and not robot_hand_mode_on:
            return RobotHandPlan(
                write_enabled=False,
                enabled_value=enabled,
                next_robot_hand_mode=True,
                enforce_outputs=True,
                enforce_active=True,
                is_transition=True,
                log_message="Entering Robot Hand mode",
            )
        if not effective_mode_on and robot_hand_mode_on:
            return RobotHandPlan(
                write_enabled=False,
                enabled_value=enabled,
                next_robot_hand_mode=False,
                enforce_outputs=True,
                enforce_active=False,
                is_transition=True,
                log_message="Leaving Robot Hand mode",
            )
        return RobotHandPlan(
            write_enabled=False,
            enabled_value=enabled,
            next_robot_hand_mode=effective_mode_on,
            enforce_outputs=True,
            enforce_active=effective_mode_on,
            is_transition=False,
            log_message="",
        )

    raise ValueError(f"Unsupported robot hand action: {action}")

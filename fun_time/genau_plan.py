from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenauPlan:
    target_active: bool
    is_transition: bool
    write_enabled: bool
    enabled_value: bool
    log_message: str


def build_genau_plan(
    action: str,
    *,
    genau_mode_on: bool,
    enabled: bool,
    mode_state_on: bool,
    omni_paused: bool,
) -> GenauPlan:
    if action == "sync-state":
        if omni_paused:
            return GenauPlan(
                target_active=genau_mode_on,
                is_transition=False,
                write_enabled=False,
                enabled_value=enabled,
                log_message="",
            )

        effective = enabled and mode_state_on
        entering = effective and not genau_mode_on
        leaving = not effective and genau_mode_on

        if entering:
            msg = "Entering Genau mode"
        elif leaving:
            msg = "Leaving Genau mode"
        else:
            msg = ""

        return GenauPlan(
            target_active=effective,
            is_transition=entering or leaving,
            write_enabled=False,
            enabled_value=enabled,
            log_message=msg,
        )

    if action == "toggle-enabled":
        next_enabled = not enabled

        if omni_paused:
            effective = next_enabled and mode_state_on
            return GenauPlan(
                target_active=effective,
                is_transition=False,
                write_enabled=True,
                enabled_value=next_enabled,
                log_message=f"Genau hotkey: {'enabled' if next_enabled else 'disabled'}",
            )

        effective = next_enabled and mode_state_on
        return GenauPlan(
            target_active=effective,
            is_transition=genau_mode_on != effective,
            write_enabled=True,
            enabled_value=next_enabled,
            log_message=f"Genau hotkey: {'enabled' if next_enabled else 'disabled'}",
        )

    raise ValueError(f"Unsupported robot hand action: {action}")

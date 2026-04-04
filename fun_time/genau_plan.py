from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenauPlan:
    target_active: bool
    is_transition: bool
    log_message: str


def build_genau_toggle_plan(
    *,
    genau_mode_on: bool,
    omni_paused: bool,
) -> GenauPlan:
    target_active = not genau_mode_on
    if omni_paused:
        return GenauPlan(
            target_active=target_active,
            is_transition=False,
            log_message=f"Genau {'activated' if target_active else 'deactivated'} (omnipaused)",
        )
    return GenauPlan(
        target_active=target_active,
        is_transition=True,
        log_message=f"Genau {'activated' if target_active else 'deactivated'}",
    )

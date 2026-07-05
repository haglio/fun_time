from __future__ import annotations

from dataclasses import dataclass

from .mode_plan import genau_active, nau_displays


@dataclass(frozen=True)
class OmniPausePlan:
    action: str
    next_omni_paused: bool
    genau_branch: bool
    resume_nau_playback: bool
    log_message: str


def build_omnipause_plan(action: str, *, omni_paused: bool, primary_mode: str) -> OmniPausePlan:
    if action == "toggle":
        action = "leave" if omni_paused else "enter"

    if action == "enter":
        return OmniPausePlan(
            action="enter",
            next_omni_paused=True,
            genau_branch=genau_active(primary_mode),
            resume_nau_playback=False,
            log_message="OmniPause: entering",
        )

    if action == "leave":
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            genau_branch=genau_active(primary_mode),
            # Nau owns the display in nau and hybrid, so leaving omnipause
            # resumes its playback there (in genau mode Genau owns the display).
            resume_nau_playback=nau_displays(primary_mode),
            log_message="OmniPause: leaving",
        )

    raise ValueError(f"Unsupported omnipause action: {action}")

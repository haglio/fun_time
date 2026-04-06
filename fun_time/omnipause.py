from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OmniPausePlan:
    action: str
    next_omni_paused: bool
    genau_branch: bool
    resume_primary_playback: bool
    disable_always_on_top: bool
    log_message: str


def build_omnipause_plan(action: str, *, omni_paused: bool, genau_mode_on: bool, skip_primary_resume: bool) -> OmniPausePlan:
    if action == "toggle":
        if not omni_paused:
            return OmniPausePlan(
                action="enter",
                next_omni_paused=True,
                genau_branch=genau_mode_on,
                resume_primary_playback=False,
                disable_always_on_top=True,
                log_message="OmniPause: entering",
            )
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            genau_branch=genau_mode_on,
            resume_primary_playback=(not genau_mode_on and not skip_primary_resume),
            disable_always_on_top=False,
            log_message="OmniPause: leaving",
        )

    if action == "enter":
        return OmniPausePlan(
            action="enter",
            next_omni_paused=True,
            genau_branch=genau_mode_on,
            resume_primary_playback=False,
            disable_always_on_top=True,
            log_message="OmniPause: entering",
        )

    if action == "leave":
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            genau_branch=genau_mode_on,
            resume_primary_playback=(not genau_mode_on and not skip_primary_resume),
            disable_always_on_top=False,
            log_message="OmniPause: leaving",
        )

    raise ValueError(f"Unsupported omnipause action: {action}")

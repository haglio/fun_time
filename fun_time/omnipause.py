from __future__ import annotations

from dataclasses import dataclass

from .mode_plan import genau_active


@dataclass(frozen=True)
class OmniPausePlan:
    action: str
    next_omni_paused: bool
    genau_branch: bool
    resume_primary_playback: bool
    resume_nau_playback: bool
    log_message: str


def build_omnipause_plan(action: str, *, omni_paused: bool, primary_mode: str, skip_primary_resume: bool) -> OmniPausePlan:
    if action == "toggle":
        action = "leave" if omni_paused else "enter"

    if action == "enter":
        return OmniPausePlan(
            action="enter",
            next_omni_paused=True,
            genau_branch=genau_active(primary_mode),
            resume_primary_playback=False,
            resume_nau_playback=False,
            log_message="OmniPause: entering",
        )

    if action == "leave":
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            genau_branch=genau_active(primary_mode),
            # The primary VLC plays only in hybrid mode; Nau is the primary
            # player in nau mode. skip_primary_resume guards the VLC path
            # only (the file dialog already started VLC playback itself).
            resume_primary_playback=(primary_mode == "hybrid" and not skip_primary_resume),
            resume_nau_playback=(primary_mode == "nau"),
            log_message="OmniPause: leaving",
        )

    raise ValueError(f"Unsupported omnipause action: {action}")

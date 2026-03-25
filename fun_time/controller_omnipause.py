from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OmniPausePlan:
    action: str
    next_omni_paused: bool
    robot_hand_branch: bool
    resume_primary_playback: bool
    log_message: str


def build_omnipause_plan(action: str, *, omni_paused: bool, robot_hand_mode_on: bool, skip_primary_resume: bool) -> OmniPausePlan:
    if action == "toggle":
        if not omni_paused:
            return OmniPausePlan(
                action="enter",
                next_omni_paused=True,
                robot_hand_branch=robot_hand_mode_on,
                resume_primary_playback=False,
                log_message="OmniPause: entering",
            )
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            robot_hand_branch=robot_hand_mode_on,
            resume_primary_playback=(not robot_hand_mode_on and not skip_primary_resume),
            log_message="OmniPause: leaving",
        )

    if action == "enter":
        return OmniPausePlan(
            action="enter",
            next_omni_paused=True,
            robot_hand_branch=robot_hand_mode_on,
            resume_primary_playback=False,
            log_message="OmniPause: entering",
        )

    if action == "leave":
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            robot_hand_branch=robot_hand_mode_on,
            resume_primary_playback=(not robot_hand_mode_on and not skip_primary_resume),
            log_message="OmniPause: leaving",
        )

    raise ValueError(f"Unsupported omnipause action: {action}")

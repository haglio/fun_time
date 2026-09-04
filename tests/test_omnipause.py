from __future__ import annotations

from fun_time.broker_control import RETRACT_CMD
from fun_time.omnipause import build_omnipause_plan


def test_toggle_enters_omnipause_when_not_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=False, main_mode="video")

    assert plan.action == "enter"
    assert plan.next_omni_paused is True
    assert plan.resume_nau_playback is False
    assert plan.log_message == "OmniPause: entering"


def test_toggle_leaves_omnipause_when_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=True, main_mode="genau")

    assert plan.action == "leave"
    assert plan.next_omni_paused is False
    # Genau owns the display in genau mode, so there is no Nau playback to resume.
    assert plan.resume_nau_playback is False
    assert plan.log_message == "OmniPause: leaving"


def test_leave_video_mode_resumes_nau():
    plan = build_omnipause_plan("leave", omni_paused=True, main_mode="video")

    assert plan.action == "leave"
    assert plan.resume_nau_playback is True


def test_relief_enters_omnipause_but_retracts_the_osr2():
    """Relief is an enter in every respect but one: the OSR2 goes away rather
    than home, because the point of it is getting the device off the user."""
    plan = build_omnipause_plan("relief", omni_paused=False, main_mode="video")

    assert plan.next_omni_paused is True
    assert plan.broker_command == RETRACT_CMD



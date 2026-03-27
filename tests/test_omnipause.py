from __future__ import annotations

from fun_time.omnipause import build_omnipause_plan


def test_toggle_enters_omnipause_when_not_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=False, robot_hand_mode_on=False, skip_primary_resume=False)

    assert plan.action == "enter"
    assert plan.next_omni_paused is True
    assert plan.robot_hand_branch is False
    assert plan.log_message == "OmniPause: entering"


def test_toggle_leaves_omnipause_when_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=True, robot_hand_mode_on=True, skip_primary_resume=False)

    assert plan.action == "leave"
    assert plan.next_omni_paused is False
    assert plan.robot_hand_branch is True
    assert plan.resume_primary_playback is False
    assert plan.log_message == "OmniPause: leaving"


def test_leave_controlled_mode_resumes_primary_when_not_skipped():
    plan = build_omnipause_plan("leave", omni_paused=True, robot_hand_mode_on=False, skip_primary_resume=False)

    assert plan.action == "leave"
    assert plan.robot_hand_branch is False
    assert plan.resume_primary_playback is True


def test_leave_controlled_mode_skips_primary_resume_when_requested():
    plan = build_omnipause_plan("leave", omni_paused=True, robot_hand_mode_on=False, skip_primary_resume=True)

    assert plan.resume_primary_playback is False

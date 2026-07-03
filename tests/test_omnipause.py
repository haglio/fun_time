from __future__ import annotations

from fun_time.omnipause import build_omnipause_plan


def test_toggle_enters_omnipause_when_not_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=False, primary_mode="nau", skip_primary_resume=False)

    assert plan.action == "enter"
    assert plan.next_omni_paused is True
    assert plan.genau_branch is False
    assert plan.log_message == "OmniPause: entering"


def test_toggle_leaves_omnipause_when_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=True, primary_mode="genau", skip_primary_resume=False)

    assert plan.action == "leave"
    assert plan.next_omni_paused is False
    assert plan.genau_branch is True
    assert plan.resume_primary_playback is False
    assert plan.log_message == "OmniPause: leaving"


def test_leave_nau_mode_resumes_nau_not_primary_vlc():
    plan = build_omnipause_plan("leave", omni_paused=True, primary_mode="nau", skip_primary_resume=False)

    assert plan.action == "leave"
    assert plan.genau_branch is False
    assert plan.resume_primary_playback is False
    assert plan.resume_nau_playback is True


def test_leave_hybrid_mode_resumes_primary_vlc():
    plan = build_omnipause_plan("leave", omni_paused=True, primary_mode="hybrid", skip_primary_resume=False)

    assert plan.genau_branch is True
    assert plan.resume_primary_playback is True
    assert plan.resume_nau_playback is False


def test_leave_hybrid_skips_primary_resume_when_requested():
    plan = build_omnipause_plan("leave", omni_paused=True, primary_mode="hybrid", skip_primary_resume=True)

    assert plan.resume_primary_playback is False


def test_leave_nau_mode_resumes_nau_even_when_primary_skipped():
    plan = build_omnipause_plan("leave", omni_paused=True, primary_mode="nau", skip_primary_resume=True)

    assert plan.resume_nau_playback is True

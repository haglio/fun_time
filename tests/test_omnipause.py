from __future__ import annotations

from fun_time.omnipause import build_omnipause_plan


def test_toggle_enters_omnipause_when_not_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=False, vlc_primary_active=True, skip_primary_resume=False)

    assert plan.action == "enter"
    assert plan.next_omni_paused is True
    assert plan.log_message == "OmniPause: entering"


def test_leave_resumes_primary_when_vlc_is_active():
    """vlc and hybrid modes both play the primary VLC, so leaving resumes it."""
    plan = build_omnipause_plan("leave", omni_paused=True, vlc_primary_active=True, skip_primary_resume=False)

    assert plan.action == "leave"
    assert plan.resume_primary_playback is True
    assert plan.log_message == "OmniPause: leaving"


def test_leave_does_not_resume_primary_when_vlc_inactive():
    """genau mode has no primary VLC playing, so leaving must not resume it."""
    plan = build_omnipause_plan("leave", omni_paused=True, vlc_primary_active=False, skip_primary_resume=False)

    assert plan.action == "leave"
    assert plan.resume_primary_playback is False


def test_leave_skips_primary_resume_when_requested():
    plan = build_omnipause_plan("leave", omni_paused=True, vlc_primary_active=True, skip_primary_resume=True)

    assert plan.resume_primary_playback is False


def test_enter_always_disables_always_on_top():
    plan = build_omnipause_plan("enter", omni_paused=False, vlc_primary_active=True, skip_primary_resume=False)

    assert plan.disable_always_on_top is True


def test_toggle_enter_always_disables_always_on_top():
    plan = build_omnipause_plan("toggle", omni_paused=False, vlc_primary_active=True, skip_primary_resume=False)

    assert plan.disable_always_on_top is True


def test_leave_does_not_disable_always_on_top():
    plan = build_omnipause_plan("leave", omni_paused=True, vlc_primary_active=True, skip_primary_resume=False)

    assert plan.disable_always_on_top is False


def test_enter_disables_always_on_top_even_when_vlc_inactive():
    plan = build_omnipause_plan("enter", omni_paused=False, vlc_primary_active=False, skip_primary_resume=False)

    assert plan.disable_always_on_top is True

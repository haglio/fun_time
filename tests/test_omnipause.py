from __future__ import annotations

from app_support import osr2_hold
from funestra.console import OSR2_DRIVING, OSR2_PARKED, OSR2_RETRACTED
from funestra.modes import MainMode

from fun_time.omnipause import build_omnipause_plan


def test_toggle_enters_omnipause_when_not_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=False, main_mode=MainMode.KINO)

    assert plan.action == "enter"
    assert plan.next_omni_paused is True
    assert plan.resume_main_funestra_playback is False
    assert plan.log_message == "OmniPause: entering"


def test_toggle_leaves_omnipause_when_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=True, main_mode=MainMode.GENAU)

    assert plan.action == "leave"
    assert plan.next_omni_paused is False
    # Genau owns the display in genau mode, so there is no Main Funestra playback to resume.
    assert plan.resume_main_funestra_playback is False
    assert plan.log_message == "OmniPause: leaving"


def test_leave_kino_mode_resumes_main_funestra():
    plan = build_omnipause_plan("leave", omni_paused=True, main_mode=MainMode.KINO)

    assert plan.action == "leave"
    assert plan.resume_main_funestra_playback is True


def test_leaving_hands_the_osr2_back_unless_a_hold_is_keeping_it():
    commands = {control: build_omnipause_plan("leave", omni_paused=True,
                                              main_mode=MainMode.KINO,
                                              osr2_control=control).broker_command
                for control in (OSR2_PARKED, OSR2_RETRACTED, OSR2_DRIVING)}

    assert commands == {OSR2_PARKED: osr2_hold.PARK, OSR2_RETRACTED: osr2_hold.RETRACT,
                        OSR2_DRIVING: osr2_hold.RESUME}



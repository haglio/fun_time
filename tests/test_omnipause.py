from __future__ import annotations

from player_core.console import OSR2_CONTROL_OFF, OSR2_PARKED, OSR2_RETRACTED
from player_core.modes import MainMode

from fun_time.broker_control import PARK_CMD, RESUME_CMD, RETRACT_CMD
from fun_time.omnipause import build_omnipause_plan


def test_toggle_enters_omnipause_when_not_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=False, main_mode=MainMode.VIDEO)

    assert plan.action == "enter"
    assert plan.next_omni_paused is True
    assert plan.resume_main_player_playback is False
    assert plan.log_message == "OmniPause: entering"


def test_toggle_leaves_omnipause_when_already_paused():
    plan = build_omnipause_plan("toggle", omni_paused=True, main_mode=MainMode.GENAU)

    assert plan.action == "leave"
    assert plan.next_omni_paused is False
    # Genau owns the display in genau mode, so there is no main player playback to resume.
    assert plan.resume_main_player_playback is False
    assert plan.log_message == "OmniPause: leaving"


def test_leave_video_mode_resumes_main_player():
    plan = build_omnipause_plan("leave", omni_paused=True, main_mode=MainMode.VIDEO)

    assert plan.action == "leave"
    assert plan.resume_main_player_playback is True


def test_leaving_gives_genau_the_device_back_in_genau_mode():
    plan = build_omnipause_plan("leave", omni_paused=True, main_mode=MainMode.GENAU)

    assert plan.resume_genau_playback is True


def test_leaving_does_not_give_it_back_while_control_is_off():
    """The console's switch decides who drives, not the way out of a pause: a
    resume here put Genau's motion back on a device the room had let go of."""
    plan = build_omnipause_plan("leave", omni_paused=True, main_mode=MainMode.GENAU,
                                osr2_control=OSR2_CONTROL_OFF)

    assert plan.resume_genau_playback is False


def test_leaving_under_a_hold_plays_the_hand_on_unheard():
    plan = build_omnipause_plan("leave", omni_paused=True, main_mode=MainMode.GENAU,
                                osr2_control=OSR2_PARKED)

    assert plan.resume_genau_playback is True


def test_leaving_hands_the_osr2_back_unless_a_hold_is_keeping_it():
    commands = {control: build_omnipause_plan("leave", omni_paused=True,
                                              main_mode=MainMode.VIDEO,
                                              osr2_control=control).broker_command
                for control in (OSR2_PARKED, OSR2_RETRACTED, OSR2_CONTROL_OFF)}

    assert commands == {OSR2_PARKED: PARK_CMD, OSR2_RETRACTED: RETRACT_CMD,
                        OSR2_CONTROL_OFF: RESUME_CMD}



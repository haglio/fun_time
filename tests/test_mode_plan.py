from __future__ import annotations

import pytest

from fun_time.mode_plan import (
    MAIN_GENAU_MODE,
    MAIN_MODES,
    MAIN_VIDEO_MODE,
    STARTUP_MAIN_MODE,
    build_mode_switch_plan,
    hud_verb,
    nau_display_verb,
    nau_displays,
)


def test_a_session_is_built_in_video_mode():
    assert STARTUP_MAIN_MODE == "video"


def test_nau_displays_in_video_mode_alone():
    assert nau_displays("video") is True
    assert nau_displays("genau") is False


def test_the_verbs_each_mode_says_to_the_two_players():
    # Genau's window is the HUD layer over Nau's video in video mode and the
    # display in genau mode; Nau paints in the one and blanks in the other.
    assert (hud_verb("video"), nau_display_verb("video")) == ("HUD_ON", "DISPLAY_ON")
    assert (hud_verb("genau"), nau_display_verb("genau")) == ("HUD_OFF", "DISPLAY_OFF")


def test_video_to_genau_parks_nau_and_blanks_it():
    plan = build_mode_switch_plan(current_mode="video", target_mode="genau", omni_paused=False)
    assert plan.target_mode == "genau"
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.hud_cmd == "HUD_OFF"
    assert plan.nau_should_play is False
    assert plan.nau_display_cmd == "DISPLAY_OFF"


def test_genau_to_video_starts_nau_under_genaus_hud():
    plan = build_mode_switch_plan(current_mode="genau", target_mode="video", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.hud_cmd == "HUD_ON"
    assert plan.nau_should_play is True
    assert plan.nau_display_cmd == "DISPLAY_ON"


def test_every_transition_resumes_genau():
    # In genau mode the Robot Hand drives from here; in video mode the dispatch
    # loop's arbiter takes it from here, and may have left Genau paused for a
    # funscript's stretch — the switch is authoritative either way.
    for current, target in (("video", "genau"), ("genau", "video")):
        plan = build_mode_switch_plan(current_mode=current, target_mode=target, omni_paused=False)
        assert plan.genau_cmd == "RESUME", f"{current}->{target}"


def test_same_mode_is_noop():
    for mode in ("video", "genau"):
        plan = build_mode_switch_plan(current_mode=mode, target_mode=mode, omni_paused=False)
        assert plan.is_transition is False
        assert plan.genau_cmd is None
        assert plan.hud_cmd is None
        assert plan.nau_should_play is None
        assert plan.nau_display_cmd is None


def test_omnipaused_skips_transition():
    plan = build_mode_switch_plan(current_mode="video", target_mode="genau", omni_paused=True)
    assert plan.target_mode == "genau"
    assert plan.is_transition is False
    assert plan.genau_cmd is None
    assert plan.nau_should_play is None
    assert plan.nau_display_cmd is None


def test_the_main_slot_has_exactly_two_modes_and_they_are_spelled_here():
    assert (MAIN_VIDEO_MODE, MAIN_GENAU_MODE) == ("video", "genau")
    assert MAIN_MODES == (MAIN_VIDEO_MODE, MAIN_GENAU_MODE)
    assert STARTUP_MAIN_MODE in MAIN_MODES


def test_a_mode_nobody_named_is_refused_rather_than_quietly_parking_everything():
    """nau_displays and genau_active both answer False for an unrecognized
    string, so a typo used to build a session that parked every player instead
    of failing."""
    with pytest.raises(ValueError):
        build_mode_switch_plan(
            current_mode=MAIN_VIDEO_MODE, target_mode="genua", omni_paused=False)


def test_a_session_already_in_an_unnamed_mode_is_refused_too():
    """The saved state file is where an unrecognized mode gets in — shared_state
    remaps the modes this app used to have, and anything it does not know comes
    through as written."""
    with pytest.raises(ValueError):
        build_mode_switch_plan(
            current_mode="hybrid", target_mode=MAIN_GENAU_MODE, omni_paused=False)

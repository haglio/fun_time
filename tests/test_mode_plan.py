from __future__ import annotations

import pytest

from fun_time.mode_plan import (
    MAIN_GENAU_MODE,
    MAIN_KINO_MODE,
    MAIN_MODES,
    STARTUP_MAIN_MODE,
    build_mode_switch_plan,
    hud_verb,
    main_player_display_verb,
    main_player_displays,
)


def test_a_session_is_built_in_kino_mode():
    assert STARTUP_MAIN_MODE == "kino"


def test_main_player_displays_in_kino_mode_alone():
    assert main_player_displays("kino") is True
    assert main_player_displays("genau") is False


def test_the_verbs_each_mode_says_to_the_two_players():
    # Genau's window is the HUD layer over the main player's video in kino mode and the
    # display in genau mode; the main player paints in the one and blanks in the other.
    assert (hud_verb("kino"), main_player_display_verb("kino")) == ("HUD_ON", "DISPLAY_ON")
    assert (hud_verb("genau"), main_player_display_verb("genau")) == ("HUD_OFF", "DISPLAY_OFF")


def test_kino_to_genau_makes_genau_the_display_and_pauses_the_main_player():
    plan = build_mode_switch_plan(current_mode="kino", target_mode="genau", omni_paused=False)
    assert plan.target_mode == "genau"
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.hud_cmd == "HUD_OFF"
    assert plan.main_player_should_play is False


def test_genau_to_kino_starts_main_player_under_genau():
    plan = build_mode_switch_plan(current_mode="genau", target_mode="kino", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.main_player_should_play is True
    assert plan.main_player_display_cmd == "DISPLAY_ON"


def test_genau_stays_the_display_through_a_switch_to_kino_paused_or_not():
    for omni_paused in (False, True):
        plan = build_mode_switch_plan(current_mode="genau", target_mode="kino",
                                      omni_paused=omni_paused)
        assert plan.hud_cmd is None


def test_the_main_player_keeps_its_picture_through_a_switch_to_genau_paused_or_not():
    for omni_paused in (False, True):
        plan = build_mode_switch_plan(current_mode="kino", target_mode="genau",
                                      omni_paused=omni_paused)
        assert plan.main_player_display_cmd is None


def test_every_transition_outside_a_pause_resumes_genau():
    # In genau mode the Robot Hand drives from here; in kino mode the dispatch
    # loop's arbiter takes it from here, and may have left Genau paused for a
    # funscript's stretch — the switch is authoritative either way.
    for current, target in (("kino", "genau"), ("genau", "kino")):
        plan = build_mode_switch_plan(current_mode=current, target_mode=target, omni_paused=False)
        assert plan.genau_cmd == "RESUME", f"{current}->{target}"


def test_same_mode_is_noop():
    for mode in ("kino", "genau"):
        plan = build_mode_switch_plan(current_mode=mode, target_mode=mode, omni_paused=False)
        assert plan.is_transition is False
        assert plan.genau_cmd is None
        assert plan.hud_cmd is None
        assert plan.main_player_should_play is None
        assert plan.main_player_display_cmd is None


def test_a_switch_while_paused_changes_what_shows_and_leaves_both_players_frozen():
    for current, target in (("kino", "genau"), ("genau", "kino")):
        plan = build_mode_switch_plan(current_mode=current, target_mode=target, omni_paused=True)
        assert plan.target_mode == target
        assert plan.is_transition is True
        assert plan.genau_cmd is None
        assert plan.main_player_should_play is None


def test_the_main_slot_has_exactly_two_modes_and_they_are_spelled_here():
    assert (MAIN_KINO_MODE, MAIN_GENAU_MODE) == ("kino", "genau")
    assert MAIN_MODES == (MAIN_KINO_MODE, MAIN_GENAU_MODE)
    assert STARTUP_MAIN_MODE in MAIN_MODES


def test_a_mode_nobody_named_is_refused_rather_than_quietly_parking_everything():
    """main_player_displays and genau_active both answer False for an unrecognized
    string, so a typo used to build a session that parked every player instead
    of failing."""
    with pytest.raises(ValueError):
        build_mode_switch_plan(
            current_mode=MAIN_KINO_MODE, target_mode="genua", omni_paused=False)


def test_a_session_already_in_an_unnamed_mode_is_refused_too():
    """The saved state file is where an unrecognized mode gets in — shared_state
    remaps the modes this app used to have, and anything it does not know comes
    through as written."""
    with pytest.raises(ValueError):
        build_mode_switch_plan(
            current_mode="hybrid", target_mode=MAIN_GENAU_MODE, omni_paused=False)

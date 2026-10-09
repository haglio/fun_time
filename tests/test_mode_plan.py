from __future__ import annotations

import pytest

from fun_time.mode_plan import (
    MAIN_GENAU_MODE,
    MAIN_KINO_MODE,
    MAIN_MODES,
    STARTUP_MAIN_MODE,
    build_mode_switch_plan,
    main_player_displays,
    show_verb,
)


def test_a_session_is_built_in_kino_mode():
    assert STARTUP_MAIN_MODE == "kino"


def test_main_player_displays_in_kino_mode_alone():
    assert main_player_displays("kino") is True
    assert main_player_displays("genau") is False


def test_the_verb_each_mode_says_to_the_main_player():
    """Kino and Genau both run on the Main Player's window; a switch tells that
    window which of them has it."""
    assert (show_verb("kino"), show_verb("genau")) == ("SHOW kino", "SHOW genau")


def test_kino_to_genau_gives_genau_the_window_and_pauses_kinos_video():
    plan = build_mode_switch_plan(current_mode="kino", target_mode="genau", omni_paused=False)
    assert plan.target_mode == "genau"
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.show_cmd == "SHOW genau"
    assert plan.main_player_should_play is False


def test_genau_to_kino_gives_kino_the_window_and_starts_its_video():
    plan = build_mode_switch_plan(current_mode="genau", target_mode="kino", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.show_cmd == "SHOW kino"
    assert plan.main_player_should_play is True


def test_a_switch_while_paused_still_says_who_has_the_window_and_starts_nobody():
    for current, target in (("kino", "genau"), ("genau", "kino")):
        plan = build_mode_switch_plan(current_mode=current, target_mode=target, omni_paused=True)
        assert plan.show_cmd == f"SHOW {target}", f"{current}->{target}"
        assert plan.genau_cmd is None
        assert plan.main_player_should_play is None


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
        assert plan.show_cmd is None
        assert plan.main_player_should_play is None


def test_a_mode_that_is_not_the_main_slots_is_refused():
    with pytest.raises(ValueError):
        build_mode_switch_plan(current_mode="kino", target_mode="origenerator", omni_paused=False)


def test_the_main_slots_modes_are_the_two():
    assert MAIN_MODES == (MAIN_KINO_MODE, MAIN_GENAU_MODE)

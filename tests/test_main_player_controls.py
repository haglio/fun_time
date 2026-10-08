"""The verbs Kino answers off the Main Funestra's command file, one at a time.

The Funestra asks Kino first and answers the rest itself, so what is pinned
here is Kino's own vocabulary: the loop gestures, the versions, the length
modes, the compilation and funscript jumps, and the playlist Kino reads through
its library.
"""
from __future__ import annotations

import pytest
from player_core.player_verbs import RELOAD_PLAYLIST, SET_F_MODE

from main_player.controls import VERBS, KinoControls, apply_command
from main_player.loop_verbs import LOOP_CANCEL, RECORD_DOWN, RECORD_TAP, RECORD_UP, SET_LOOP


class Spy:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name: str):
        def record(*args):
            self.calls.append((name, *args))
        return record


class SpyPlayback:
    position_ms = 2_500.5


def _controls(**over) -> tuple[KinoControls, dict[str, Spy]]:
    spies = {name: Spy() for name in ("loops", "versions", "modes", "jumps", "funscript_jumps")}
    reloaded: list[int] = []
    controls = KinoControls(
        playback=SpyPlayback(), reload_playlist=lambda: reloaded.append(1), **spies, **over)
    spies["reloaded"] = reloaded
    return controls, spies


class TestTheLoopGestures:
    def test_each_gesture_is_marked_where_the_playhead_is(self):
        controls, spies = _controls()

        for verb in (RECORD_DOWN, RECORD_UP, RECORD_TAP):
            assert apply_command(verb, controls) is True

        assert spies["loops"].calls == [
            ("record_down", 2500), ("record_up", 2500), ("record_tap", 2500)]

    def test_cancelling_reaches_the_loop(self):
        controls, spies = _controls()

        assert apply_command(LOOP_CANCEL, controls) is True

        assert spies["loops"].calls == [("cancel",)]

    def test_set_loop_puts_a_range_back_without_replaying_the_gesture(self):
        controls, spies = _controls()

        assert apply_command(f"{SET_LOOP} 2000 4000", controls) is True

        assert spies["loops"].calls == [("restore", 2000, 4000)]

    @pytest.mark.parametrize("value", ["", "2000", "2000 later"])
    def test_a_range_it_cannot_read_leaves_the_loop_alone(self, value):
        controls, spies = _controls()

        assert apply_command(f"{SET_LOOP} {value}".strip(), controls) is False

        assert spies["loops"].calls == []


class TestTheVersions:
    def test_the_two_verbs_walk_the_family_either_way(self):
        controls, spies = _controls()

        assert apply_command("CYCLE_VERSION", controls) is True
        assert apply_command("CYCLE_VERSION_BACK", controls) is True

        assert spies["versions"].calls == [("cycle", 1), ("cycle", -1)]


class TestTheLibrary:
    def test_the_length_filter_is_toggled_and_named(self):
        controls, spies = _controls()

        assert apply_command("TOGGLE_LENGTH_MODE", controls) is True
        assert apply_command("SET_LENGTH_MODE shorts", controls) is True

        assert spies["modes"].calls == [("toggle_length",), ("set_length", "shorts")]

    def test_end_compilation_goes_back_to_the_mode_that_was_running(self):
        controls, spies = _controls()

        assert apply_command("END_COMPILATION", controls) is True

        assert spies["modes"].calls == [("end_compilation",)]

    def test_set_f_mode_says_the_flag_outright(self):
        controls, spies = _controls()

        assert apply_command(f"{SET_F_MODE} 1", controls) is True
        assert apply_command(f"{SET_F_MODE} 0", controls) is True

        assert spies["modes"].calls == [("set_scripted_filter", True), ("set_scripted_filter", False)]

    def test_the_three_ways_into_another_slice_of_the_library(self):
        controls, spies = _controls()

        for verb in ("PLAY_COMPILATION", "PLAY_FULL_VID", "PLAY_CLIP_JUMP"):
            assert apply_command(verb, controls) is True

        assert spies["jumps"].calls == [("play_compilation",), ("play_full_vid",), ("play_clip_jump",)]

    def test_the_funscripts_own_two_moves(self):
        controls, spies = _controls()

        assert apply_command("JUMP_TO_FUNSCRIPT", controls) is True
        assert apply_command("NEXT_FUNSCRIPTED", controls) is True

        assert spies["funscript_jumps"].calls == [("jump_to_funscript",), ("next_funscripted",)]

    def test_reload_playlist_reads_the_list_through_the_library(self):
        controls, spies = _controls()

        assert apply_command(RELOAD_PLAYLIST, controls) is True

        assert spies["reloaded"] == [1]


# Every command line Kino answers, one per verb, with fabricated arguments.
# These strings are what Fun Time writes into main_player_cmd.txt, so a verb
# renamed on this side is a control that goes quiet on the other.
ACCEPTED_COMMANDS = [
    "RECORD_DOWN", "RECORD_UP", "RECORD_TAP", "LOOP_CANCEL", "SET_LOOP 1000 2000",
    "CYCLE_VERSION", "CYCLE_VERSION_BACK",
    "RELOAD_PLAYLIST", "TOGGLE_LENGTH_MODE", "SET_LENGTH_MODE shorts", "END_COMPILATION",
    "SET_F_MODE 1",
    "PLAY_COMPILATION", "PLAY_FULL_VID", "PLAY_CLIP_JUMP",
    "JUMP_TO_FUNSCRIPT", "NEXT_FUNSCRIPTED",
]


class TestTheVerbsFunTimeCanSend:
    @pytest.mark.parametrize("command", ACCEPTED_COMMANDS)
    def test_it_is_answered(self, command):
        controls, _spies = _controls()

        assert apply_command(command, controls) is True

    @pytest.mark.parametrize("command", ["RECORD_TAP 1", "CYCLE_VERSION 2", "END_COMPILATION now"])
    def test_a_value_on_a_verb_that_takes_none_is_refused(self, command):
        controls, _spies = _controls()

        assert apply_command(command, controls) is False

    @pytest.mark.parametrize("command", ["SET_LOOP", "SET_LENGTH_MODE", "SET_F_MODE"])
    def test_a_verb_that_wants_a_value_is_refused_without_one(self, command):
        controls, _spies = _controls()

        assert apply_command(command, controls) is False

    def test_the_registry_declares_exactly_these_verbs_and_no_others(self):
        assert set(VERBS) == {line.split()[0] for line in ACCEPTED_COMMANDS}

    def test_a_funestras_own_verb_is_refused_so_the_funestra_answers_it(self):
        controls, _spies = _controls()

        assert apply_command("NEXT", controls) is False
        assert apply_command("FROBNICATE", controls) is False

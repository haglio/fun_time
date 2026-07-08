"""Unit tests for the voice control module."""
from __future__ import annotations

import json

from pathlib import Path

from fun_time.voice_control import VOICE_COMMANDS, VoiceController, build_grammar, parse_vosk_result


class TestVoiceCommands:
    def test_contains_all_static_phrases(self):
        static_phrases = {
            "quit": "quit",
            "pause": "pause",
            "play": "play",
            "f mode": "fmode_toggle",
            "f mode on": "fmode_on",
            "f mode off": "fmode_off",
            "go now": "genau_activate",
            "now now": "nau_activate",
            "now mode": "nau_activate",
            "hybrid": "hybrid_activate",
            "hybrid mode": "hybrid_activate",
            "start broker": "broker_start",
            "stop broker": "broker_stop",
            "next primary": "primary_next",
            "previous primary": "primary_prev",
            "skip": "primary_nudge_next",
            "back": "primary_nudge_prev",
            "record": "nau_record_down",
            "loop": "nau_record_up",
            "end loop": "nau_loop_cancel",
            "cycle version": "nau_cycle_version",
            "next version": "nau_cycle_version",
            "shorts": "nau_length_shorts",
            "full length": "nau_length_full",
            "browse": "open_file_dialog",
            "clip": "clipper_save",
            "save clip": "clipper_save",
            "slow down": "genau_speed_down",
            "speed down": "genau_speed_down",
            "speed up": "genau_speed_up",
            "amp down": "genau_amplitude_down",
            "amp up": "genau_amplitude_up",
            "center down": "genau_center_down",
            "center up": "genau_center_up",
            "next shape": "genau_cycle_shape",
            "previous shape": "genau_cycle_shape_prev",
            "genau auto": "genau_toggle_auto",
            "cruise control": "genau_toggle_cruise",
            "cruise on": "genau_cruise_on",
            "cruise off": "genau_cruise_off",
            "previous clip": "genau_prev_clip",
            "next clip": "genau_next_clip",
            "offset": "quarter_button",
            "voice off": "voice_off",
        }
        for phrase, cmd in static_phrases.items():
            assert VOICE_COMMANDS[phrase] == cmd

    def test_go_now_activates_genau(self):
        # Recognizer phrase stays "go now"; the reference displays it as "genau".
        assert VOICE_COMMANDS["go now"] == "genau_activate"

    def test_dead_genau_phrases_removed(self):
        for phrase in ("enable genau", "disable genau"):
            assert phrase not in VOICE_COMMANDS

    def test_video_activates_nau(self):
        assert VOICE_COMMANDS["now now"] == "nau_activate"
        assert "v l c" not in VOICE_COMMANDS

    def test_hybrid_activates_hybrid(self):
        assert VOICE_COMMANDS["hybrid"] == "hybrid_activate"

    def test_nau_cycle_version_and_length_phrases(self):
        assert VOICE_COMMANDS["cycle version"] == "nau_cycle_version"
        assert VOICE_COMMANDS["shorts"] == "nau_length_shorts"
        assert VOICE_COMMANDS["full length"] == "nau_length_full"

    def test_satellite_grid_supports_both_orders(self):
        """Each satellite action works BARE (→ active side) and with a side word
        in EITHER order: "portrait lock" and "lock portrait" are equivalent."""
        actions = {
            "lock": "lock_on",
            "unlock": "lock_off",
            "next": "next",
            "previous": "prev",
            "weird": "trash",
            "action": "cycle_action",
            "seed": "cycle_seed",
        }
        for word, act in actions.items():
            assert VOICE_COMMANDS[word] == f"active_{act}"
            for side in ("portrait", "landscape", "both"):
                target = f"{side}_{act}"
                assert VOICE_COMMANDS[f"{side} {word}"] == target  # side first
                assert VOICE_COMMANDS[f"{word} {side}"] == target  # side last

    def test_primary_nav_phrases_both_orders(self):
        """The primary player joins the grid for navigation only, in either
        order — and bare "next"/"previous" reach it via the active side."""
        assert VOICE_COMMANDS["primary next"] == "primary_next"
        assert VOICE_COMMANDS["next primary"] == "primary_next"
        assert VOICE_COMMANDS["primary previous"] == "primary_prev"
        assert VOICE_COMMANDS["previous primary"] == "primary_prev"

    def test_mode_named_navigation_both_orders(self):
        """A mode's name + next/previous (either order) navigates its player:
        Nau/Hybrid drive the primary, Genau its own clip.  vosk can't hear
        "nau"/"genau", so the recognizer uses the "now mode"/"go now" sound-alikes."""
        # Nau (recognizer "now mode") and Hybrid both drive the primary.
        for base in ("now mode", "hybrid"):
            assert VOICE_COMMANDS[f"{base} next"] == "primary_next"
            assert VOICE_COMMANDS[f"next {base}"] == "primary_next"
            assert VOICE_COMMANDS[f"{base} previous"] == "primary_prev"
            assert VOICE_COMMANDS[f"previous {base}"] == "primary_prev"
        # Genau (recognizer "go now") steps its own clip.
        assert VOICE_COMMANDS["go now next"] == "genau_next_clip"
        assert VOICE_COMMANDS["next go now"] == "genau_next_clip"
        assert VOICE_COMMANDS["go now previous"] == "genau_prev_clip"
        assert VOICE_COMMANDS["previous go now"] == "genau_prev_clip"

    def test_contains_numeric_amp_phrases(self):
        assert VOICE_COMMANDS["amp fifty"] == "genau_amp_50"
        assert VOICE_COMMANDS["amp zero"] == "genau_amp_0"
        assert VOICE_COMMANDS["amp one hundred"] == "genau_amp_100"

    def test_contains_numeric_center_phrases(self):
        assert VOICE_COMMANDS["center eighty"] == "genau_center_80"

    def test_contains_numeric_speed_phrases(self):
        assert VOICE_COMMANDS["speed thirty"] == "genau_speed_30"

    def test_min_max_extreme_phrases(self):
        assert VOICE_COMMANDS["min amp"] == "genau_amp_0"
        assert VOICE_COMMANDS["max amp"] == "genau_amp_100"
        assert VOICE_COMMANDS["min center"] == "genau_center_0"
        assert VOICE_COMMANDS["max center"] == "genau_center_100"
        assert VOICE_COMMANDS["min speed"] == "genau_speed_0"
        assert VOICE_COMMANDS["max speed"] == "genau_speed_100"


class TestBuildGrammar:
    def test_returns_json_list_of_phrases_plus_unk(self):
        grammar = build_grammar()
        phrases = json.loads(grammar)
        assert isinstance(phrases, list)
        assert "[unk]" in phrases
        for phrase in VOICE_COMMANDS:
            assert phrase in phrases
        assert len(phrases) == len(VOICE_COMMANDS) + 1

    def test_phrases_are_sorted(self):
        grammar = build_grammar()
        phrases = json.loads(grammar)
        phrase_keys = [p for p in phrases if p != "[unk]"]
        assert phrase_keys == sorted(phrase_keys)


class TestParseVoskResult:
    def test_returns_command_for_known_phrase(self):
        raw = json.dumps({
            "text": "landscape next",
            "result": [
                {"conf": 0.95, "word": "landscape", "start": 0.0, "end": 0.5},
                {"conf": 0.95, "word": "next", "start": 0.5, "end": 0.8},
            ],
        })
        assert parse_vosk_result(raw, threshold=0.7) == "landscape_next"

    def test_returns_none_for_unk(self):
        raw = json.dumps({"text": "[unk]"})
        assert parse_vosk_result(raw, threshold=0.7) is None

    def test_returns_none_for_empty_text(self):
        raw = json.dumps({"text": ""})
        assert parse_vosk_result(raw, threshold=0.7) is None

    def test_returns_none_for_unknown_phrase(self):
        raw = json.dumps({"text": "something random"})
        assert parse_vosk_result(raw, threshold=0.7) is None

    def test_returns_none_when_confidence_below_threshold(self):
        raw = json.dumps({
            "text": "skip",
            "result": [{"conf": 0.3, "word": "skip", "start": 0.0, "end": 0.3}],
        })
        assert parse_vosk_result(raw, threshold=0.7) is None

    def test_accepts_when_no_confidence_data(self):
        raw = json.dumps({"text": "pause"})
        assert parse_vosk_result(raw, threshold=0.7) == "pause"


class TestVoiceController:
    def test_write_command_appends_to_file(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc._write_command("landscape_next")
        assert cmd_file.read_text(encoding="utf-8") == "landscape_next\n"

    def test_write_command_appends_multiple(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc._write_command("landscape_next")
        vc._write_command("pause")
        lines = cmd_file.read_text(encoding="utf-8").strip().splitlines()
        assert lines == ["landscape_next", "pause"]

    def test_stop_sets_event(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        assert not vc._stop.is_set()
        vc.stop()
        assert vc._stop.is_set()

    def test_mute_prevents_write_command(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.mute()
        vc._write_command("landscape_next")
        assert not cmd_file.exists()

    def test_unmute_restores_write_command(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.mute()
        vc.unmute()
        vc._write_command("landscape_next")
        assert cmd_file.read_text(encoding="utf-8") == "landscape_next\n"

    def test_is_muted_property(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        assert not vc.is_muted
        vc.mute()
        assert vc.is_muted
        vc.unmute()
        assert not vc.is_muted


def test_voice_commands_include_generated_filter_phrases():
    from fun_time.filter_vocab import clear_command, set_command

    assert VOICE_COMMANDS["portrait beta gamma"] == set_command("portrait", "beta gamma")
    assert VOICE_COMMANDS["alpha form"] == set_command("both", "alpha")
    assert VOICE_COMMANDS["clear portrait"] == clear_command("portrait")


def test_filter_phrases_reach_the_recognizer_grammar():
    grammar = build_grammar()
    assert "portrait beta gamma" in grammar
    assert "alpha form" in grammar


def test_filter_phrases_do_not_shadow_other_commands():
    # Every filter phrase must resolve to its filter command — i.e. no filter
    # phrase silently overrode (or was overridden by) another voice command.
    from fun_time.filter_vocab import decode_filter_command, filter_voice_commands

    for phrase, command in filter_voice_commands().items():
        assert VOICE_COMMANDS[phrase] == command
        assert decode_filter_command(command) is not None

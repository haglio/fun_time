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
            "lock landscape": "landscape_lock_on",
            "lock portrait": "portrait_lock_on",
            "next landscape": "landscape_next",
            "next portrait": "portrait_next",
            "previous landscape": "landscape_prev",
            "previous portrait": "portrait_prev",
            "weird landscape": "landscape_trash",
            "weird portrait": "portrait_trash",
            "f mode on": "fmode_on",
            "f mode off": "fmode_off",
            "go now": "genau_activate",
            "enable genau": "genau_enable",
            "disable genau": "genau_disable",
            "v l c": "vlc_activate",
            "hybrid": "hybrid_activate",
            "start broker": "broker_start",
            "stop broker": "broker_stop",
            "next primary": "primary_next",
            "previous primary": "primary_prev",
            "skip": "vlc_nudge_next",
            "back": "vlc_nudge_prev",
            "slow down": "genau_speed_down",
            "speed down": "genau_speed_down",
            "speed up": "genau_speed_up",
            "amp down": "genau_amplitude_down",
            "amp up": "genau_amplitude_up",
            "center down": "genau_center_down",
            "center up": "genau_center_up",
            "cycle shape": "genau_cycle_shape",
            "genau auto": "genau_toggle_auto",
            "cruise control": "genau_toggle_cruise",
            "cruise on": "genau_cruise_on",
            "cruise off": "genau_cruise_off",
            "previous clip": "genau_prev_clip",
            "next clip": "genau_next_clip",
            "voice off": "voice_off",
        }
        for phrase, cmd in static_phrases.items():
            assert VOICE_COMMANDS[phrase] == cmd

    def test_go_now_activates_genau(self):
        assert VOICE_COMMANDS["go now"] == "genau_activate"

    def test_v_l_c_activates_vlc(self):
        assert VOICE_COMMANDS["v l c"] == "vlc_activate"

    def test_hybrid_activates_hybrid(self):
        assert VOICE_COMMANDS["hybrid"] == "hybrid_activate"

    def test_contains_numeric_amp_phrases(self):
        assert VOICE_COMMANDS["amp fifty"] == "genau_amp_50"
        assert VOICE_COMMANDS["amp zero"] == "genau_amp_0"
        assert VOICE_COMMANDS["amp one hundred"] == "genau_amp_100"

    def test_contains_numeric_center_phrases(self):
        assert VOICE_COMMANDS["center eighty"] == "genau_center_80"

    def test_contains_numeric_speed_phrases(self):
        assert VOICE_COMMANDS["speed thirty"] == "genau_speed_30"


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
            "text": "next landscape",
            "result": [
                {"conf": 0.95, "word": "next", "start": 0.0, "end": 0.3},
                {"conf": 0.95, "word": "landscape", "start": 0.3, "end": 0.8},
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

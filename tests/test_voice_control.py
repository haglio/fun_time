"""Unit tests for the voice control module."""
from __future__ import annotations

import json

from pathlib import Path
from types import SimpleNamespace

import pytest

from fun_time import voice_control
from fun_time.voice_commands import format_spoken_command, parse_command_line
from fun_time.voice_control import (
    VOICE_COMMANDS,
    Recognition,
    UtteranceOnset,
    VoiceController,
    build_grammar,
    has_partial_text,
    interpret_recognition,
)


def _scored(text: str, conf: float) -> str:
    """A Vosk result JSON for *text* with every word scored *conf*."""
    words = [{"conf": conf, "word": w, "start": 0.0, "end": 0.1} for w in text.split()]
    return json.dumps({"text": text, "result": words})


class TestUtteranceOnset:
    def test_onset_is_the_first_block_that_produced_a_partial(self):
        """Vosk finalizes a phrase only after the speaker stops, so the arrival
        of the phrase says nothing about when it began.  The first audio block
        that turns Vosk's partial hypothesis non-empty does."""
        onset = UtteranceOnset()
        onset.note_block(block_started_at=1.0, has_partial=False)
        onset.note_block(block_started_at=1.5, has_partial=True)
        onset.note_block(block_started_at=2.0, has_partial=True)
        assert onset.take(fallback=2.5) == 1.5

    def test_a_partial_that_evaporates_does_not_back_date_the_next_utterance(self):
        """Vosk withdraws a hypothesis it can no longer support; the run of
        partials restarts, so the false start is not mistaken for the onset."""
        onset = UtteranceOnset()
        onset.note_block(block_started_at=1.0, has_partial=True)   # false start
        onset.note_block(block_started_at=1.5, has_partial=False)  # withdrawn
        onset.note_block(block_started_at=2.0, has_partial=True)   # real speech
        assert onset.take(fallback=2.5) == 2.0

    def test_take_falls_back_and_resets_for_the_next_utterance(self):
        """A phrase recognized from the block that carried it left no partial."""
        onset = UtteranceOnset()
        onset.note_block(block_started_at=1.0, has_partial=True)
        assert onset.take(fallback=2.5) == 1.0
        assert onset.take(fallback=9.0) == 9.0


class TestHasPartialText:
    def test_true_when_vosk_holds_words(self):
        assert has_partial_text(json.dumps({"partial": "lock portrait"}))

    def test_false_for_an_empty_or_absent_partial(self):
        assert not has_partial_text(json.dumps({"partial": "  "}))
        assert not has_partial_text(json.dumps({}))


class TestCommandLineFormat:
    def test_round_trips_the_utterance_start(self):
        line = format_spoken_command("portrait_lock_on", spoken_at=1234.5)
        assert parse_command_line(line) == ("portrait_lock_on", 1234.5)

    def test_an_unstamped_line_is_a_bare_command(self):
        """Hotkeys and dashboard presses are instantaneous — no back-dating."""
        assert parse_command_line("portrait_next") == ("portrait_next", None)

    def test_a_command_ending_in_an_unparseable_stamp_stays_whole(self):
        assert parse_command_line("filter_both_come @ shot") == ("filter_both_come @ shot", None)


class _FakeRecognizer:
    """Records whether the run loop asked vosk for per-word confidences.

    ``grammar`` is None for the free (unrestricted) recognizer the loop builds
    alongside the grammar one.
    """

    def __init__(self, model, sample_rate, grammar=None) -> None:
        self.grammar = grammar
        self.words_enabled = False

    def SetWords(self, enable: bool) -> None:  # noqa: N802 — vosk's API
        self.words_enabled = enable

    def AcceptWaveform(self, data: bytes) -> bool:  # noqa: N802 — vosk's API
        return False


class _NullStream:
    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture
def fake_vosk(monkeypatch):
    """Stand in for vosk + sounddevice; yields the recognizers ``run`` builds."""
    built: list[_FakeRecognizer] = []

    def make_recognizer(model, sample_rate, grammar=None):
        rec = _FakeRecognizer(model, sample_rate, grammar)
        built.append(rec)
        return rec

    monkeypatch.setattr(voice_control, "VOICE_AVAILABLE", True)
    monkeypatch.setattr(
        voice_control,
        "vosk",
        SimpleNamespace(Model=lambda **kwargs: object(), KaldiRecognizer=make_recognizer),
    )
    monkeypatch.setattr(
        voice_control,
        "sd",
        SimpleNamespace(RawInputStream=lambda **kwargs: _NullStream()),
    )
    return built


class TestVoiceCommands:
    def test_exit_is_a_synonym_for_quit(self):
        assert VOICE_COMMANDS["exit"] == "quit"
        assert VOICE_COMMANDS["quit"] == "quit"

    def test_contains_all_static_phrases(self):
        static_phrases = {
            "quit": "quit",
            "exit": "quit",
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
            # "end loop" is side-agnostic — it reaches Nau's own loop through the
            # active-side resolution, not by naming the player here.
            "end loop": "active_no_loop",
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
            "go now auto": "genau_toggle_auto",
            "cruise control": "genau_toggle_cruise",
            "cruise on": "genau_cruise_on",
            "cruise off": "genau_cruise_off",
            "previous clip": "genau_prev_clip",
            "next clip": "genau_next_clip",
            "offset": "quarter_button",
            "voice off": "voice_off",
            "mic off": "voice_off",
        }
        for phrase, cmd in static_phrases.items():
            assert VOICE_COMMANDS[phrase] == cmd

    def test_audio_phrases_mute_and_step_the_volume(self):
        """Both words of each pair mean the same thing, so a speaker never has to
        pick between "quiet" and "quieter"."""
        assert VOICE_COMMANDS["mute"] == "audio_mute"
        assert VOICE_COMMANDS["quiet"] == "audio_volume_down"
        assert VOICE_COMMANDS["quieter"] == "audio_volume_down"
        assert VOICE_COMMANDS["loud"] == "audio_volume_up"
        assert VOICE_COMMANDS["louder"] == "audio_volume_up"

    def test_no_phrase_uses_a_word_vosk_cannot_hear(self):
        """A phrase built from a word outside the model's lexicon can never be
        recognized — the command is unreachable, and silently so.  Every one of
        these has a sound-alike the recognizer listens for instead."""
        oov_words = {"genau", "nau", "hotkeys", "unmute"}
        for phrase in VOICE_COMMANDS:
            offenders = oov_words & set(phrase.split())
            assert not offenders, f"{phrase!r} uses out-of-vocabulary {sorted(offenders)}"

    def test_unmute_is_heard_as_two_words(self):
        """vosk has no "unmute" token but does have "un"; the recognizer listens
        for "un mute" and the reference shows the friendly single word."""
        assert VOICE_COMMANDS["un mute"] == "audio_unmute"
        assert "unmute" not in VOICE_COMMANDS

    def test_reference_popup_phrases_toggle_and_close_help(self):
        # Several spoken names toggle the hotkeys & voice reference popup; the
        # same names prefixed with "close" only dismiss it.  vosk has no
        # "hotkeys" token, so it listens for "hot keys" (two words).
        for phrase in ("help", "reference", "hot keys", "voice commands"):
            assert VOICE_COMMANDS[phrase] == "help_reference"
            assert VOICE_COMMANDS[f"close {phrase}"] == "help_reference_close"
        assert "hotkeys" not in VOICE_COMMANDS  # OOV single token — never a recognizer phrase

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
            "scene": "cycle_action",  # scene == action
            "seed": "cycle_seed",
        }
        for word, act in actions.items():
            assert VOICE_COMMANDS[word] == f"active_{act}"
            for side in ("portrait", "landscape", "both"):
                target = f"{side}_{act}"
                assert VOICE_COMMANDS[f"{side} {word}"] == target  # side first
                assert VOICE_COMMANDS[f"{word} {side}"] == target  # side last

    def test_cycle_verb_phrases_map_to_the_active_cycle(self):
        """"cycle / next / change <axis>" are extra active-side spoken forms for
        the cycle commands, and "scene" reads as "action"."""
        for phrase in ("cycle action", "next action", "change action",
                       "cycle scene", "next scene", "change scene"):
            assert VOICE_COMMANDS[phrase] == "active_cycle_action"
        for phrase in ("cycle seed", "next seed", "change seed"):
            assert VOICE_COMMANDS[phrase] == "active_cycle_seed"

    def test_loop_family_phrases(self):
        """The grid's loop names ("loop actions" / "loop seeds"), the equivalent
        singular/reversed forms, "loop scene(s)" (scene == action), and "no loop"
        / "loop off" to end one — all sided in either order like the rest."""
        for phrase in ("loop actions", "loop action", "action loop", "loop scenes", "loop scene"):
            assert VOICE_COMMANDS[phrase] == "active_action_loop"
        for phrase in ("loop seeds", "loop seed", "seed loop"):
            assert VOICE_COMMANDS[phrase] == "active_seed_loop"
        for phrase in ("no loop", "loop off"):
            assert VOICE_COMMANDS[phrase] == "active_no_loop"
        assert VOICE_COMMANDS["portrait loop actions"] == "portrait_action_loop"
        assert VOICE_COMMANDS["loop actions portrait"] == "portrait_action_loop"
        assert VOICE_COMMANDS["both no loop"] == "both_no_loop"
        assert VOICE_COMMANDS["more seeds"] == "active_more_seeds"
        assert VOICE_COMMANDS["portrait more seeds"] == "portrait_more_seeds"
        # "widen (the) net" are spoken synonyms for "more seeds".
        assert VOICE_COMMANDS["widen net"] == "active_more_seeds"
        assert VOICE_COMMANDS["widen the net"] == "active_more_seeds"
        assert VOICE_COMMANDS["portrait widen the net"] == "portrait_more_seeds"

    def test_grid_lock_scopes_are_aliases_of_existing_commands(self):
        """The grid's lock scopes collapse onto commands that already exist,
        since every satellite playlist runs repeat-all: "lock seed" is the action
        loop, "lock type" the seed loop, "lock all" the repeat-one lock, and
        "loop all" the whole unfiltered browse (reset)."""
        assert VOICE_COMMANDS["lock seed"] == "active_action_loop"
        assert VOICE_COMMANDS["lock type"] == "active_seed_loop"
        assert VOICE_COMMANDS["lock all"] == "active_lock_on"
        assert VOICE_COMMANDS["loop all"] == "active_reset"
        # sided, either order, like the rest of the grid
        assert VOICE_COMMANDS["portrait lock seed"] == "portrait_action_loop"
        assert VOICE_COMMANDS["lock all landscape"] == "landscape_lock_on"
        assert VOICE_COMMANDS["both loop all"] == "both_reset"

    def test_primary_nav_phrases_both_orders(self):
        """The primary player joins the grid for navigation only, in either
        order; "main" is a synonym for "primary". Bare "next"/"previous" reach
        it via the active side."""
        for word in ("primary", "main"):
            assert VOICE_COMMANDS[f"{word} next"] == "primary_next"
            assert VOICE_COMMANDS[f"next {word}"] == "primary_next"
            assert VOICE_COMMANDS[f"{word} previous"] == "primary_prev"
            assert VOICE_COMMANDS[f"previous {word}"] == "primary_prev"

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
        # Speed min/max route to the active engine (Nau video or Genau), not
        # Genau-only like the amp/center extremes.
        assert VOICE_COMMANDS["min speed"] == "speed_min"
        assert VOICE_COMMANDS["max speed"] == "speed_max"

    def test_nau_multiplier_speed_phrases(self):
        assert VOICE_COMMANDS["half speed"] == "nau_speed_50"
        assert VOICE_COMMANDS["normal speed"] == "nau_speed_100"
        assert VOICE_COMMANDS["one and a half speed"] == "nau_speed_150"
        assert VOICE_COMMANDS["double speed"] == "nau_speed_200"

    def test_spoken_speed_ex_phrases_cover_every_stop(self):
        assert VOICE_COMMANDS["speed point two five ex"] == "nau_speed_25"
        assert VOICE_COMMANDS["speed one ex"] == "nau_speed_100"
        assert VOICE_COMMANDS["speed one point two five ex"] == "nau_speed_125"
        assert VOICE_COMMANDS["speed one point seven five ex"] == "nau_speed_175"
        assert VOICE_COMMANDS["speed two ex"] == "nau_speed_200"

    def test_reset_speed_snaps_to_normal(self):
        assert VOICE_COMMANDS["reset speed"] == "nau_speed_100"


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


class TestInterpretRecognition:
    def test_a_confident_grammar_match_is_the_command(self):
        interp = interpret_recognition(
            _scored("landscape next", 0.95), _scored("landscape next", 0.9), threshold=0.7,
        )
        assert interp == Recognition(command="landscape_next", phrase="landscape next")

    def test_an_unscored_grammar_match_is_not_dispatched(self):
        """An unscored recognition cannot clear the threshold — the loop enables
        SetWords, so a result with no word data is one we have no evidence for."""
        interp = interpret_recognition(
            json.dumps({"text": "pause"}), json.dumps({"text": ""}), threshold=0.7,
        )
        assert interp.command is None

    def test_an_out_of_grammar_phrase_is_captioned_from_the_free_recognizer(self):
        """The grammar hears only "[unk]" (it can't leave its vocabulary); the
        free recognizer supplies what was actually said."""
        interp = interpret_recognition(
            json.dumps({"text": "[unk]"}), _scored("full length please", 0.9), threshold=0.7,
        )
        assert interp == Recognition(unrecognized_text="full length please")

    def test_a_grammar_match_below_threshold_falls_back_to_the_caption(self):
        interp = interpret_recognition(
            _scored("skip", 0.3), _scored("skip it", 0.9), threshold=0.7,
        )
        assert interp.command is None
        assert interp.unrecognized_text == "skip it"

    def test_quiet_free_text_is_treated_as_noise_and_dropped(self):
        """"Definitely saying something" is a confidence bar — quiet-room noise
        the free model latches onto must not caption a phantom command."""
        interp = interpret_recognition(
            json.dumps({"text": "[unk]"}), _scored("mumble", 0.3), threshold=0.7,
        )
        assert interp == Recognition()

    def test_nothing_heard_is_nothing(self):
        interp = interpret_recognition(
            json.dumps({"text": ""}), json.dumps({"text": ""}), threshold=0.7,
        )
        assert interp == Recognition()


class TestHandleRecognition:
    def _controller(self, tmp_path: Path) -> VoiceController:
        return VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")

    def test_a_recognized_command_dispatches_and_confirms_over_its_player(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source, level)))

        vc._handle_recognition(Recognition(command="landscape_next", phrase="landscape next"), spoken_at=1.0)

        assert (tmp_path / "cmd.txt").read_text(encoding="utf-8") == "landscape_next @1.000\n"
        assert seen == [("landscape next", "landscape", 25)]

    def test_a_sound_alike_phrase_is_confirmed_under_its_friendly_name(self, tmp_path, monkeypatch):
        """"go now" drives Genau; the confirmation shows "genau", not the raw
        sound-alike the recognizer listens for."""
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(msg))

        vc._handle_recognition(Recognition(command="genau_activate", phrase="go now"), spoken_at=1.0)

        assert seen == ["genau"]

    def test_a_muted_command_neither_dispatches_nor_confirms(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.mute()
        seen = []
        monkeypatch.setattr(voice_control, "notice", lambda *a, **k: seen.append(a))

        vc._handle_recognition(Recognition(command="landscape_next", phrase="landscape next"), spoken_at=1.0)

        assert not (tmp_path / "cmd.txt").exists()
        assert seen == []

    def test_unrecognized_speech_reports_what_it_heard_in_red(self, tmp_path, monkeypatch):
        import logging

        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source, level)))

        vc._handle_recognition(Recognition(unrecognized_text="full length please"), spoken_at=1.0)

        assert seen == [("unrecognized command: full length please", "system", logging.ERROR)]

    def test_unrecognized_speech_stays_silent_while_muted(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.mute()
        seen = []
        monkeypatch.setattr(voice_control, "notice", lambda *a, **k: seen.append(a))

        vc._handle_recognition(Recognition(unrecognized_text="full length please"), spoken_at=1.0)

        assert seen == []


class TestVoiceController:
    def test_write_command_stamps_the_utterance_start(self, tmp_path: Path):
        """Every spoken command carries when the user began saying it."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc._write_command("landscape_next", spoken_at=1234.5)
        assert cmd_file.read_text(encoding="utf-8") == "landscape_next @1234.500\n"

    def test_write_command_appends_multiple(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc._write_command("landscape_next", spoken_at=1.0)
        vc._write_command("pause", spoken_at=2.0)
        lines = cmd_file.read_text(encoding="utf-8").strip().splitlines()
        assert lines == ["landscape_next @1.000", "pause @2.000"]

    def test_stop_sets_event(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        assert not vc._stop.is_set()
        vc.stop()
        assert vc._stop.is_set()

    def test_resolve_device_returns_none_when_unpinned(self, tmp_path, monkeypatch):
        """With no device_name, sounddevice uses the system default (index None)
        and no lookup is attempted."""
        vc = VoiceController(cmd_file=tmp_path / "c.txt", model_path="unused")
        monkeypatch.setattr(
            voice_control, "resolve_input_device",
            lambda name: pytest.fail("must not look up a device when unpinned"),
        )
        assert vc._resolve_device() is None

    def test_resolve_device_looks_up_the_pinned_name(self, tmp_path, monkeypatch):
        """A configured mic name is resolved to its live sounddevice index."""
        vc = VoiceController(cmd_file=tmp_path / "c.txt", model_path="unused", device_name="Brio")
        seen: list = []

        def fake_resolve(name):
            seen.append(name)
            return (2, "Microphone (Brio 101)")

        monkeypatch.setattr(voice_control, "resolve_input_device", fake_resolve)
        assert vc._resolve_device() == 2
        assert seen == ["Brio"]

    def test_resolve_device_falls_back_to_none_when_name_matches_nothing(self, tmp_path, monkeypatch):
        """The pinned mic is absent → None, letting sounddevice use the default."""
        vc = VoiceController(cmd_file=tmp_path / "c.txt", model_path="unused", device_name="Brio")
        monkeypatch.setattr(voice_control, "resolve_input_device", lambda name: (None, None))
        assert vc._resolve_device() is None

    def test_resolve_device_survives_a_lookup_error(self, tmp_path, monkeypatch):
        """A sounddevice failure during lookup must not kill the voice thread."""
        vc = VoiceController(cmd_file=tmp_path / "c.txt", model_path="unused", device_name="Brio")

        def boom(name):
            raise OSError("PortAudio exploded")

        monkeypatch.setattr(voice_control, "resolve_input_device", boom)
        assert vc._resolve_device() is None

    def test_mute_prevents_write_command(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.mute()
        vc._write_command("landscape_next", spoken_at=1.0)
        assert not cmd_file.exists()

    def test_unmute_restores_write_command(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.mute()
        vc.unmute()
        vc._write_command("landscape_next", spoken_at=1.0)
        assert cmd_file.read_text(encoding="utf-8") == "landscape_next @1.000\n"

    def test_is_muted_property(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        assert not vc.is_muted
        vc.mute()
        assert vc.is_muted
        vc.unmute()
        assert not vc.is_muted

    def test_suspend_drops_every_command_but_the_exempt_ones(self, tmp_path: Path):
        """Omnipause suspends voice exactly as it suspends the AHK hotkeys: only
        the two #SuspendExempt triggers survive — resume, and quit."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        for command in ("landscape_next", "help_reference", "pause", "genau_speed_up"):
            vc._write_command(command, spoken_at=1.0)
        assert not cmd_file.exists()

    def test_suspend_still_lets_resume_and_quit_through(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc._write_command("play", spoken_at=1.0)
        vc._write_command("quit", spoken_at=2.0)
        written = cmd_file.read_text(encoding="utf-8").splitlines()
        assert [parse_command_line(line)[0] for line in written] == ["play", "quit"]

    def test_unsuspend_restores_every_command(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc.unsuspend()
        vc._write_command("landscape_next", spoken_at=1.0)
        assert cmd_file.read_text(encoding="utf-8") == "landscape_next @1.000\n"

    def test_mute_beats_the_suspend_exemption(self, tmp_path: Path):
        """"Voice off" means off: an exempt command must not slip past a mute."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc.mute()
        vc._write_command("play", spoken_at=1.0)
        assert not cmd_file.exists()

    def test_run_asks_both_recognizers_for_word_confidences(self, tmp_path: Path, fake_vosk):
        """In grammar mode vosk only reports per-word confidences when SetWords
        is enabled.  Without it every recognition arrives unscored and the
        confidence gate waves it through, so ambient room noise fires real
        commands — the reference popup opening itself during omnipause.  Both the
        grammar recognizer and the free caption recognizer need scores."""
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")
        vc.stop()  # exit the listen loop as soon as the recognizers are built
        vc.run()

        # One grammar recognizer (built with the phrase grammar) and one free
        # recognizer (no grammar) for the unrecognized-speech caption.
        assert len(fake_vosk) == 2
        assert all(r.words_enabled for r in fake_vosk)
        assert [r.grammar is None for r in fake_vosk] == [False, True]


def test_group_commands_join_the_order_agnostic_grid():
    """The group actions are order-agnostic in both words: "action loop" and
    "loop action" are equivalent, each works bare (active side) and with a side
    word in either order, like the other satellite actions."""
    for words, act in {
        ("action loop", "loop action"): "action_loop",
        ("seed loop", "loop seed"): "seed_loop",
        ("lock action", "action lock"): "lock_action",
    }.items():
        for word in words:
            assert VOICE_COMMANDS[word] == f"active_{act}"
            for side in ("portrait", "landscape", "both"):
                assert VOICE_COMMANDS[f"{side} {word}"] == f"{side}_{act}"
                assert VOICE_COMMANDS[f"{word} {side}"] == f"{side}_{act}"


def test_resume_and_unpause_are_synonyms_for_play():
    assert VOICE_COMMANDS["resume"] == "play"
    assert VOICE_COMMANDS["un pause"] == "play"  # recognizer form of "unpause"
    assert "unpause" not in VOICE_COMMANDS  # single OOV token — never a phrase


def test_shuffle_joins_the_order_agnostic_satellite_grid():
    """Shuffle is sided like its counterpart Latest, so a single satellite can be
    reshuffled without disturbing the other."""
    assert VOICE_COMMANDS["shuffle"] == "active_shuffle"
    for side in ("portrait", "landscape", "both"):
        assert VOICE_COMMANDS[f"{side} shuffle"] == f"{side}_shuffle"
        assert VOICE_COMMANDS[f"shuffle {side}"] == f"{side}_shuffle"


def test_reset_joins_the_order_agnostic_satellite_grid():
    assert VOICE_COMMANDS["reset"] == "active_reset"
    for side in ("portrait", "landscape", "both"):
        assert VOICE_COMMANDS[f"{side} reset"] == f"{side}_reset"
        assert VOICE_COMMANDS[f"reset {side}"] == f"{side}_reset"


def test_group_commands_do_not_shadow_the_single_word_actions():
    # "lock action" must not disturb "lock"/"action"; "action loop" not "loop".
    assert VOICE_COMMANDS["lock"] == "active_lock_on"
    assert VOICE_COMMANDS["action"] == "active_cycle_action"
    assert VOICE_COMMANDS["seed"] == "active_cycle_seed"
    assert VOICE_COMMANDS["loop"] == "nau_record_up"


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

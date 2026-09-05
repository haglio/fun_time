"""Unit tests for the voice control module."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fun_time import voice_control
from fun_time.voice_commands import parse_command_line
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

    def test_a_match_scored_exactly_at_the_threshold_fires(self):
        """The bar is inclusive — ``conf >= threshold`` — and the equality
        case is the one the comparison exists to decide: a user who sets
        ``confidence_threshold`` is drawing the line their commands must
        reach, not clear."""
        interp = interpret_recognition(
            _scored("landscape next", 0.7), _scored("landscape next", 0.7), threshold=0.7,
        )
        assert interp == Recognition(command="landscape_next", phrase="landscape next")

    def test_a_caption_scored_exactly_at_the_threshold_surfaces(self):
        # Two words; the three-word case, where the mean used to land a hair
        # under the bar in float, is pinned by the two tests below.
        interp = interpret_recognition(
            json.dumps({"text": "[unk]"}), _scored("skip it", 0.7), threshold=0.7,
        )
        assert interp == Recognition(unrecognized_text="skip it")

    def test_a_three_word_command_at_the_threshold_fires(self):
        """Three words each scoring exactly the threshold: their float mean
        is a hair under it, so a command spoken at the bar was refused
        wherever the word count was not a power of two (bug 86).  The gate
        compares the sum against the bar times the count, which is exact."""
        interp = interpret_recognition(
            _scored("main video mode", 0.7), _scored("main video mode", 0.7), threshold=0.7,
        )
        assert interp == Recognition(command="main_video_activate", phrase="main video mode")

    def test_a_three_word_caption_at_the_threshold_surfaces(self):
        interp = interpret_recognition(
            json.dumps({"text": "[unk]"}), _scored("skip it now", 0.7), threshold=0.7,
        )
        assert interp == Recognition(unrecognized_text="skip it now")

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

        assert seen == [("unrecognized voice command: full length please", "system", logging.ERROR)]

    @pytest.mark.parametrize("heard, source", [
        ("portrait full length please", "portrait"),
        ("landscape full length please", "landscape"),
        ("main full length please", "main"),
        ("full length please landscape", "landscape"),
    ])
    def test_an_unrecognized_phrase_reports_over_the_player_it_named(
        self, tmp_path, monkeypatch, heard, source,
    ):
        """A phrase the grammar rejected can still say who it was for, in either
        order — that satellite is where the user is looking, so that is where the
        red report belongs, rather than on the main player."""
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source)))

        vc._handle_recognition(Recognition(unrecognized_text=heard), spoken_at=1.0)

        assert seen == [(f"unrecognized voice command: {heard}", source)]

    def test_a_player_word_inside_a_longer_word_does_not_claim_the_report(self):
        """The player has to be *named* — matched whole, not as a fragment."""
        assert voice_control._source_for_heard_text("mainly landscaped") == "system"

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
        """Omnipause suspends voice as it suspends the AHK hotkeys: nothing a
        paused room says is acted on — the reference popup included."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        for command in ("landscape_next", "help_reference", "pause", "speed_up"):
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

    def test_suspend_still_lets_relief_through(self, tmp_path: Path):
        """Voice frozen by omnipause must not swallow the one command whose whole
        purpose is to act from inside omnipause: a paused session can still have
        the device on the user, and speaking is the way out when reaching for a
        key is not."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc._write_command("relief_omnipause", spoken_at=1.0)
        written = cmd_file.read_text(encoding="utf-8").splitlines()
        assert [parse_command_line(line)[0] for line in written] == ["relief_omnipause"]

    def test_suspend_freezes_the_reference_popup_too(self, tmp_path: Path):
        """The popup gets no exemption: the freeze is a flat rule about what a
        paused room may be heard to do, and "help" is the phrase room noise
        produced when it opened the popup mid-pause."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc._write_command("help_reference", spoken_at=1.0)
        vc._write_command("help_reference_close", spoken_at=2.0)
        assert not cmd_file.exists()

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


def test_filter_phrases_reach_the_recognizer_grammar():
    from fun_time.filter_vocab import filter_voice_commands

    grammar = build_grammar()
    for phrase in filter_voice_commands():
        assert phrase in grammar



"""What Fun Time does with what the family's listener (voice_core) hears.

Hearing itself -- the grammar, the ranked readings, the repairs, the silence
floor, the microphone, the kept clips -- is voice_core's and is tested there.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import pytest
from voice_core.commands import Recognition
from voice_core.listening import Heard

from fun_time import voice_control
from fun_time.filter_vocab import filter_voice_commands
from fun_time.voice_commands import VOICE_COMMANDS, parse_command_line
from fun_time.voice_control import WHILE_NOT_WEARING_HEADSET, VoiceController, command_rules

SPOKEN = 2000  # a peak that is unmistakably speech


def _heard(recognition: Recognition) -> Heard:
    return Heard(recognition, spoken_at=1.0, peak=SPOKEN, audio=b"", candidates={})


def _formed(recognition: Recognition) -> Heard:
    return replace(_heard(recognition), words_formed=True)


MISSES = [
    Recognition(refused_phrase="skip"),
    Recognition(unconfirmed_phrase="go now"),
    Recognition(unrecognized_text="alpha beta"),
    Recognition(silent_reading="half"),
    Recognition(),
]


class TestCommandRules:
    def test_every_spoken_phrase_is_one_the_listener_is_told_to_hear(self):
        rules = command_rules(confidence_threshold=0.7, confirm_commands=True)

        assert rules.phrases == frozenset(VOICE_COMMANDS)
        assert set(filter_voice_commands()) <= rules.phrases

    def test_a_repair_never_lands_on_a_command_that_holds_or_ends_the_room(self):
        """A repair promotes a reading ranked under another: a fine trade for a
        satellite nudge, a bad one for quitting the room or moving the device."""
        rules = command_rules(confidence_threshold=0.7, confirm_commands=True)

        ruled_out = {phrase for phrase in rules.phrases if rules.never_rescued(phrase)}

        assert {"quit", "park it", "park", "retract", "relief omni pause", "stop",
                "main reset", "portrait reset"} <= ruled_out
        assert not {"landscape lock", "pause", "next", "amp up"} & ruled_out

    def test_the_configured_bar_is_the_one_a_scored_reading_has_to_reach(self):
        assert command_rules(confidence_threshold=0.6, confirm_commands=True
                             ).confidence_threshold == 0.6  # noqa: PLR2004

    def test_with_commands_confirmed_only_relief_acts_on_the_first_listeners_word_alone(self):
        """The sensation emergency must not wait half a second for a second
        opinion; everything else can."""
        rules = command_rules(confidence_threshold=0.7, confirm_commands=True)

        assert {phrase for phrase in rules.phrases if rules.stands_alone(phrase)} == {
            phrase for phrase, command in VOICE_COMMANDS.items() if command == "relief_omnipause"}

    def test_with_commands_unconfirmed_every_phrase_acts_on_the_first_listeners_word(self):
        rules = command_rules(confidence_threshold=0.7, confirm_commands=False)

        assert all(rules.stands_alone(phrase) for phrase in rules.phrases)

    def test_the_second_listener_is_shown_a_sound_alike_phrase_as_it_is_written(self):
        rules = command_rules(confidence_threshold=0.7, confirm_commands=True)

        assert rules.written("go now mode") == "genau mode"
        assert rules.written("o s r two off") == "OSR2 off"
        assert rules.written("landscape next") is None

    def test_the_phrases_that_say_genau_are_said_in_german_and_no_others(self):
        rules = command_rules(confidence_threshold=0.7, confirm_commands=True)

        assert {rules.said_in(phrase) for phrase in
                ("go now", "go now mode", "next go now", "portrait go now")} == {"de"}
        assert {rules.said_in(phrase) for phrase in
                ("kino mode", "clip seconds five", "weird clip", "next")} == {None}

    def test_the_second_listener_is_handed_genau_whichever_spelling_the_first_heard(self):
        rules = command_rules(confidence_threshold=0.7, confirm_commands=True)
        genau_phrases = [phrase for phrase, command in VOICE_COMMANDS.items()
                         if command == "genau_activate"]

        assert {rules.written(phrase) or phrase for phrase in genau_phrases} == {"genau", "genau mode"}


def test_the_listener_is_handed_a_reader_for_the_phrases_said_in_german(tmp_path):
    vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")

    assert set(vc.engines.second_opinion_in) == {"de"}


class TestHandleHeard:
    def _controller(self, tmp_path: Path) -> VoiceController:
        return VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")

    def test_a_recognized_command_is_handed_on_with_what_was_said_and_flashes_nothing_here(
        self, tmp_path, monkeypatch,
    ):
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice", lambda *a, **k: seen.append(a))

        vc.handle_heard(_heard(Recognition(phrase="landscape next")))

        assert (tmp_path / "cmd.txt").read_text(encoding="utf-8") == "landscape_next @1.000\tlandscape next\n"
        assert seen == []

    def test_a_sound_alike_phrase_is_handed_on_under_its_friendly_name(self, tmp_path):
        """"go now" drives Genau; what the room says it heard is "genau", not the
        raw sound-alike the recognizer listens for."""
        vc = self._controller(tmp_path)

        vc.handle_heard(_heard(Recognition(phrase="go now")))

        [line] = (tmp_path / "cmd.txt").read_text(encoding="utf-8").splitlines()
        assert parse_command_line(line).said == "genau"

    def test_a_command_heard_while_omnipaused_says_it_was_ignored(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.suspend()
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source, level)))

        vc.handle_heard(_heard(Recognition(phrase="landscape next")))

        assert not (tmp_path / "cmd.txt").exists()
        assert seen == [("ignored during OmniPause: landscape next", "landscape", logging.WARNING)]

    def test_a_command_heard_with_the_headset_off_says_that_was_why(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.suspend(WHILE_NOT_WEARING_HEADSET)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source, level)))

        vc.handle_heard(_heard(Recognition(phrase="pause")))

        assert not (tmp_path / "cmd.txt").exists()
        assert [msg for msg, _source, _level in seen] == ["ignored while not wearing headset: pause"]

    def test_with_the_headset_off_quit_is_the_one_command_heard(self, tmp_path):
        vc = self._controller(tmp_path)
        vc.suspend(WHILE_NOT_WEARING_HEADSET)

        for phrase in ("play", "quit"):
            vc.handle_heard(_heard(Recognition(phrase=phrase)))

        written = (tmp_path / "cmd.txt").read_text(encoding="utf-8").splitlines()
        assert [parse_command_line(line).command for line in written] == ["quit"]

    def test_a_muted_room_stays_silent_while_omnipaused_too(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.suspend()
        vc.mute()
        seen = []
        monkeypatch.setattr(voice_control, "notice", lambda *a, **k: seen.append(a))

        vc.handle_heard(_heard(Recognition(phrase="landscape next")))

        assert seen == []

    def test_a_muted_command_neither_dispatches_nor_confirms(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.mute()
        seen = []
        monkeypatch.setattr(voice_control, "notice", lambda *a, **k: seen.append(a))

        vc.handle_heard(_heard(Recognition(phrase="landscape next")))

        assert not (tmp_path / "cmd.txt").exists()
        assert seen == []

    @pytest.mark.parametrize("recognition, logged, shown, source", [
        (Recognition(refused_phrase="skip"),
         "not sure enough of a command (1 word)", "not sure enough of: skip", "system"),
        (Recognition(unconfirmed_phrase="go now"),
         "not sure enough of a command (1 word)", "not sure enough of: genau", "system"),
        (Recognition(unrecognized_text="landscape alpha beta gamma"),
         "unrecognized voice command (4 words)",
         "unrecognized voice command: landscape alpha beta gamma", "landscape"),
        (Recognition(silent_reading="landscape half"),
         "too quiet to act on (2 words)", "too quiet to act on: landscape half", "landscape"),
    ])
    def test_what_it_heard_and_did_not_act_on_is_flashed_in_its_words_and_logged_by_count(
        self, tmp_path, monkeypatch, caplog, recognition, logged, shown, source,
    ):
        vc = self._controller(tmp_path)
        flashed = []
        monkeypatch.setattr(voice_control, "flash_unlogged",
                            lambda state_dir, message, *, source, level:
                            flashed.append((state_dir, message, source, level)))

        with caplog.at_level(logging.DEBUG):
            vc.handle_heard(_formed(recognition))

        assert flashed == [(tmp_path, shown, source, logging.WARNING)]
        assert [(record.getMessage(), record.levelno, record.source, record.flashes)
                for record in caplog.records] == [(logged, logging.WARNING, source, False)]

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
        report belongs, rather than on the main player."""
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, _msg, *, source, level, flashes: seen.append(source))
        monkeypatch.setattr(voice_control, "flash_unlogged",
                            lambda _dir, _msg, *, source, level: seen.append(source))

        vc.handle_heard(_formed(Recognition(unrecognized_text=heard)))

        assert seen == [source, source]

    def test_a_player_word_inside_a_longer_word_does_not_claim_the_report(self):
        """The player has to be *named* — matched whole, not as a fragment."""
        assert voice_control._source_for_heard_text("mainly landscaped") == "system"

    def test_unrecognized_speech_stays_silent_while_muted(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.mute()
        seen = []
        monkeypatch.setattr(voice_control, "notice", lambda *a, **k: seen.append(a))
        monkeypatch.setattr(voice_control, "flash_unlogged", lambda *a, **k: seen.append(a))

        vc.handle_heard(_formed(Recognition(unrecognized_text="full length please")))

        assert seen == []

    @pytest.mark.parametrize("recognition", MISSES)
    def test_an_utterance_it_said_it_was_figuring_out_ends_in_exactly_one_line(
        self, tmp_path, monkeypatch, caplog, recognition,
    ):
        vc = self._controller(tmp_path)
        monkeypatch.setattr(voice_control, "flash_unlogged", lambda *a, **k: True)

        with caplog.at_level(logging.DEBUG):
            vc.handle_heard(_formed(recognition))

        assert len(caplog.records) == 1

    @pytest.mark.parametrize("recognition", MISSES)
    def test_an_utterance_it_never_said_it_was_figuring_out_says_nothing(
        self, tmp_path, monkeypatch, caplog, recognition,
    ):
        vc = self._controller(tmp_path)
        flashed = []
        monkeypatch.setattr(voice_control, "flash_unlogged", lambda *a, **k: flashed.append(a))

        with caplog.at_level(logging.DEBUG):
            vc.handle_heard(_heard(recognition))

        assert (caplog.records, flashed) == ([], [])
        assert not (tmp_path / "cmd.txt").exists()

    @pytest.mark.parametrize("recognition", [
        Recognition(), Recognition(unrecognized_text="", heard=""),
    ])
    def test_an_utterance_with_nothing_to_read_says_it_could_not_catch_it(
        self, tmp_path, monkeypatch, recognition,
    ):
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source, level)))

        vc.handle_heard(_formed(recognition))

        assert seen == [("couldn't catch what you said", "system", logging.WARNING)]


class TestTheListenerItRuns:
    def test_misses_are_kept_beside_the_state_only_while_the_room_is_listened_to(self, tmp_path):
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")

        assert vc.listener_settings.miss_dir == tmp_path / "voice_misses"
        assert vc.listener_events.keeps_misses() is True
        vc.mute()
        assert vc.listener_events.keeps_misses() is False
        vc.unmute()
        vc.suspend()
        assert vc.listener_events.keeps_misses() is False

    def test_it_listens_on_the_configured_model_microphone_and_rate(self, tmp_path):
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="a-model",
                             device_name="Desk Cam", sample_rate=8000)

        settings = vc.listener_settings

        assert (settings.model_name, settings.device_name, settings.sample_rate) == (
            "a-model", "Desk Cam", 8000)
        assert settings.caption_misses is True

    def test_what_the_listener_hears_is_handled_here(self, tmp_path):
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")

        assert vc.listener_events.heard == vc.handle_heard

    def test_audio_coming_back_after_a_stall_is_plain_news(self, tmp_path, monkeypatch):
        """Yellow while nothing spoken can be heard (the listener's own warning);
        white when the audio comes back, because a microphone that recovered is
        no warning."""
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source, level)))

        vc.listener_events.recovered()

        assert seen == [("Voice control: audio from the microphone resumed", "system", 25)]

    def test_the_first_words_of_an_utterance_say_the_room_is_working_on_them(
            self, tmp_path, monkeypatch):
        """Understanding a phrase can take seconds -- 6.88 of them, for one
        "enter vr" -- and a room that shows nothing in that time gets told twice.
        So the moment the recognizer has words forming, it says so."""
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(msg))

        vc.listener_events.partial("enter")

        assert seen == [voice_control.WORKING_ON_IT]

    def test_it_says_so_once_per_utterance_however_the_words_grow(self, tmp_path, monkeypatch):
        """The recognizer revises its reading word by word; one line per revision
        would bury the log under a single sentence."""
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(msg))

        for forming in ("enter", "enter v", "enter vr"):
            vc.listener_events.partial(forming)

        assert seen == [voice_control.WORKING_ON_IT]

    def test_the_next_utterance_is_said_to_be_worked_on_again(self, tmp_path, monkeypatch):
        """An utterance settling clears the reading, and that is what re-arms it."""
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(msg))

        for forming in ("enter", "enter vr", "", "exit"):
            vc.listener_events.partial(forming)

        assert seen == [voice_control.WORKING_ON_IT] * 2

    @pytest.mark.parametrize("quieted", ["mute", "suspend"])
    def test_a_room_not_being_listened_to_is_told_nothing(self, tmp_path, monkeypatch, quieted):
        """Muted, or held by omnipause, what is said is thrown away -- so saying
        it is being worked on would be a promise about nothing."""
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")
        getattr(vc, quieted)()
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(msg))

        vc.listener_events.partial("enter")

        assert seen == []

    def test_a_listener_that_dies_is_logged_and_does_not_take_the_thread_down(
            self, tmp_path, monkeypatch, caplog):
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")
        monkeypatch.setattr(vc._listener, "run", lambda: (_ for _ in ()).throw(OSError("no mic")))

        with caplog.at_level(logging.ERROR, logger="fun_time.voice_control"):
            vc.run()

        assert "Voice control thread crashed" in caplog.text

    def test_stopping_stops_the_listener(self, tmp_path, monkeypatch):
        vc = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")
        stopped = []
        monkeypatch.setattr(vc._listener, "stop", lambda: stopped.append(True))

        vc.stop()

        assert stopped == [True]

    def test_the_second_listener_is_handed_to_the_first_whichever_way_commands_are_settled(self, tmp_path):
        reader = object()
        confirmed = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused",
                                    confirm_commands=True, second_listener=reader)
        unconfirmed = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused",
                                      confirm_commands=False, second_listener=reader)

        assert confirmed.engines.second_opinion is reader
        assert unconfirmed.engines.second_opinion is reader


class TestWriteCommand:
    def test_writing_stamps_the_utterance_start(self, tmp_path: Path):
        """Every spoken command carries when the user began saying it."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc._write_spoken("landscape next", spoken_at=1234.5)
        assert cmd_file.read_text(encoding="utf-8") == "landscape_next @1234.500\tlandscape next\n"

    def test_writing_appends_multiple(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc._write_spoken("landscape next", spoken_at=1.0)
        vc._write_spoken("pause", spoken_at=2.0)
        lines = cmd_file.read_text(encoding="utf-8").strip().splitlines()
        assert lines == ["landscape_next @1.000\tlandscape next", "pause @2.000\tpause"]

    def test_mute_prevents_writing(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.mute()
        vc._write_spoken("landscape next", spoken_at=1.0)
        assert not cmd_file.exists()

    def test_unmute_restores_writing(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.mute()
        vc.unmute()
        vc._write_spoken("landscape next", spoken_at=1.0)
        assert cmd_file.read_text(encoding="utf-8") == "landscape_next @1.000\tlandscape next\n"

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
        for phrase in ("landscape next", "help", "pause", "speed up"):
            vc._write_spoken(phrase, spoken_at=1.0)
        assert not cmd_file.exists()

    def test_suspend_still_lets_resume_and_quit_through(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc._write_spoken("play", spoken_at=1.0)
        vc._write_spoken("quit", spoken_at=2.0)
        written = cmd_file.read_text(encoding="utf-8").splitlines()
        assert [parse_command_line(line).command for line in written] == ["play", "quit"]

    def test_suspend_still_lets_relief_through(self, tmp_path: Path):
        """Voice frozen by omnipause must not swallow the one command whose whole
        purpose is to act from inside omnipause: a paused session can still have
        the device on the user, and speaking is the way out when reaching for a
        key is not."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc._write_spoken("relief omni pause", spoken_at=1.0)
        written = cmd_file.read_text(encoding="utf-8").splitlines()
        assert [parse_command_line(line).command for line in written] == ["relief_omnipause"]

    def test_suspend_freezes_the_reference_popup_too(self, tmp_path: Path):
        """The popup gets no exemption: the freeze is a flat rule about what a
        paused room may be heard to do, and "help" is the phrase room noise
        produced when it opened the popup mid-pause."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc._write_spoken("help", spoken_at=1.0)
        vc._write_spoken("close help", spoken_at=2.0)
        assert not cmd_file.exists()

    def test_unsuspend_restores_every_command(self, tmp_path: Path):
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc.unsuspend()
        vc._write_spoken("landscape next", spoken_at=1.0)
        assert cmd_file.read_text(encoding="utf-8") == "landscape_next @1.000\tlandscape next\n"

    def test_mute_beats_the_suspend_exemption(self, tmp_path: Path):
        """"Voice off" means off: an exempt command must not slip past a mute."""
        cmd_file = tmp_path / "cmd.txt"
        vc = VoiceController(cmd_file=cmd_file, model_path="unused")
        vc.suspend()
        vc.mute()
        vc._write_spoken("play", spoken_at=1.0)
        assert not cmd_file.exists()


class TestTheMicBeingOff:
    """The mute lives in this session's controller, so a crossing has nowhere to
    read it from but what the session that ended wrote down."""

    def test_the_next_session_takes_what_the_last_one_said(self, tmp_path: Path):
        voice_control.say_the_mic_is_off(tmp_path, off=True)

        assert voice_control.take_whether_the_mic_was_off(tmp_path) is True

    def test_it_is_spent_in_the_taking(self, tmp_path: Path):
        """Taken at every startup, crossing or not, so a session that ended
        muted cannot quietly mute some later launch of its own."""
        voice_control.say_the_mic_is_off(tmp_path, off=True)
        voice_control.take_whether_the_mic_was_off(tmp_path)

        assert voice_control.take_whether_the_mic_was_off(tmp_path) is False

    def test_a_session_that_said_nothing_left_the_mic_on(self, tmp_path: Path):
        assert voice_control.take_whether_the_mic_was_off(tmp_path) is False

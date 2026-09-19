"""What Fun Time does with what the family's listener (voice_core) hears.

Hearing itself -- the grammar, the ranked readings, the repairs, the silence
floor, the microphone, the kept clips -- is voice_core's and is tested there.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from voice_core.commands import Recognition
from voice_core.listening import Heard

from fun_time import voice_control
from fun_time.voice_commands import VOICE_COMMANDS, parse_command_line
from fun_time.voice_control import VoiceController, command_rules

SPOKEN = 2000  # a peak that is unmistakably speech


def _heard(recognition: Recognition) -> Heard:
    return Heard(recognition, spoken_at=1.0, peak=SPOKEN, audio=b"", candidates={})


class TestCommandRules:
    def test_every_spoken_phrase_is_one_the_listener_is_told_to_hear(self):
        from fun_time.filter_vocab import filter_voice_commands

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


class TestHandleHeard:
    def _controller(self, tmp_path: Path) -> VoiceController:
        return VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused")

    def test_a_recognized_command_dispatches_and_confirms_over_its_player(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source, level)))

        vc.handle_heard(_heard(Recognition(phrase="landscape next")))

        assert (tmp_path / "cmd.txt").read_text(encoding="utf-8") == "landscape_next @1.000\n"
        assert seen == [("landscape next", "landscape", 25)]

    def test_a_sound_alike_phrase_is_confirmed_under_its_friendly_name(self, tmp_path, monkeypatch):
        """"go now" drives Genau; the confirmation shows "genau", not the raw
        sound-alike the recognizer listens for."""
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(msg))

        vc.handle_heard(_heard(Recognition(phrase="go now")))

        assert seen == ["genau"]

    def test_a_command_heard_while_omnipaused_says_it_was_ignored(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.suspend()
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source, level)))

        vc.handle_heard(_heard(Recognition(phrase="landscape next")))

        assert not (tmp_path / "cmd.txt").exists()
        assert seen == [("ignored during OmniPause: landscape next", "landscape", logging.WARNING)]

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

    @pytest.mark.parametrize("recognition, report", [
        (Recognition(unrecognized_text="full length please"),
         "unrecognized voice command: full length please"),
        (Recognition(refused_phrase="skip"), "not sure enough of: skip"),
        (Recognition(unconfirmed_phrase="go now"), "not sure enough of: genau"),
    ])
    def test_speech_it_could_not_act_on_is_reported_as_a_warning(
        self, tmp_path, monkeypatch, recognition, report,
    ):
        """Nothing failed: the room was heard, just not well enough to act on,
        so the report reads yellow and red is kept for errors."""
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append((msg, source, level)))

        vc.handle_heard(_heard(recognition))

        assert seen == [(report, "system", logging.WARNING)]

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
                            lambda _log, msg, *, source, level=25: seen.append((msg, source)))

        vc.handle_heard(_heard(Recognition(unrecognized_text=heard)))

        assert seen == [(f"unrecognized voice command: {heard}", source)]

    def test_a_bare_command_confirms_over_the_player_it_reached(self, tmp_path, monkeypatch):
        """"Portrait next" makes portrait the active player, so the "next" after
        it drives portrait -- and the confirmation belongs over portrait, not
        over the main player the command's own name resolves to."""
        vc = self._controller(tmp_path)
        vc.active_player = lambda: 2  # Player.PORTRAIT
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(source))

        vc.handle_heard(_heard(Recognition(phrase="next")))

        assert seen == ["portrait"]

    def test_a_bare_command_with_the_main_player_active_confirms_over_it(
            self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.active_player = lambda: 1  # Player.MAIN
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(source))

        vc.handle_heard(_heard(Recognition(phrase="next")))

        assert seen == ["main"]

    def test_a_named_command_ignores_which_player_is_active(self, tmp_path, monkeypatch):
        """"Landscape next" says who it is for; the active side has no say."""
        vc = self._controller(tmp_path)
        vc.active_player = lambda: 2
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(source))

        vc.handle_heard(_heard(Recognition(phrase="landscape next")))

        assert seen == ["landscape"]

    def test_with_nothing_to_ask_a_bare_command_falls_back_to_the_main_player(
            self, tmp_path, monkeypatch):
        """A listener nobody wired to a dispatch loop still has to flash somewhere."""
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice",
                            lambda _log, msg, *, source, level=25: seen.append(source))

        vc.handle_heard(_heard(Recognition(phrase="next")))

        assert seen == ["system"]

    def test_a_player_word_inside_a_longer_word_does_not_claim_the_report(self):
        """The player has to be *named* — matched whole, not as a fragment."""
        assert voice_control._source_for_heard_text("mainly landscaped") == "system"

    def test_unrecognized_speech_stays_silent_while_muted(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        vc.mute()
        seen = []
        monkeypatch.setattr(voice_control, "notice", lambda *a, **k: seen.append(a))

        vc.handle_heard(_heard(Recognition(unrecognized_text="full length please")))

        assert seen == []

    def test_a_reading_out_of_silence_or_an_empty_utterance_says_nothing(self, tmp_path, monkeypatch):
        vc = self._controller(tmp_path)
        seen = []
        monkeypatch.setattr(voice_control, "notice", lambda *a, **k: seen.append(a))

        vc.handle_heard(_heard(Recognition(silent_reading="half")))
        vc.handle_heard(_heard(Recognition()))

        assert seen == []
        assert not (tmp_path / "cmd.txt").exists()


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
                                    confirm_commands=True, second_listener=lambda: reader)
        unconfirmed = VoiceController(cmd_file=tmp_path / "cmd.txt", model_path="unused",
                                      confirm_commands=False, second_listener=lambda: reader)

        assert confirmed.engines.second_opinion is reader
        assert unconfirmed.engines.second_opinion is reader


class TestWriteCommand:
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

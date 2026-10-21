"""What Fun Time does with what the family's listener hears: a spoken phrase
becomes a line in the dashboard command file, where the dispatch loop picks it
up as it would an AHK hotkey.  Hearing itself is voice_core's."""
from __future__ import annotations

import logging
import re
import threading
import time
from bisect import bisect_right
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from funestra.file_channel import append_command
from voice_core.commands import CommandRules
from voice_core.listener import CommandListener, ListenerEvents, ListenerSettings, engines_for
from voice_core.listening import Heard

from fun_time.command_dispatch import notice_source, said_to_the_hosted_app
from fun_time.event_log import (
    SOURCE_LANDSCAPE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
    notice,
)
from fun_time.hosted_contract import NO_HOSTED_VOCABULARY, HostedVocabulary
from fun_time.unlogged_notices import flash_unlogged
from fun_time.voice_commands import (
    GENAU_SAID,
    VOICE_COMMANDS,
    build_voice_commands,
    format_spoken_command,
    friendly_voice,
    said_to_a_side,
)

logger = logging.getLogger(__name__)

WORKING_ON_IT = "Figuring out what you said..."
DID_NOT_CATCH_IT = "couldn't catch what you said"
NO_COMMAND = "unrecognized voice command"
NOTHING_TO_ACCEPT = "Nothing to accept"
PRESS_ENTER_TO_ACCEPT = "(press Enter to accept)"


def _words(text: str | None) -> list[str]:
    return re.findall(r"[a-z']+", (text or "").lower())


def _without_its_first_word(text: str) -> str:
    return re.sub(r"^\W*[\w']+\W*", "", text, count=1)


def _how_many(words: str) -> str:
    count = len(words.split())
    return f"{count} word{'' if count == 1 else 's'}"



# The Funestra words a speaker can put in any command, and which window a notice
# about that Funestra flashes over.  "both" is deliberately absent: it addresses
# two Funestras, and a notice flashes over one.
_SPOKEN_FUNESTRA_SOURCES: dict[str, str] = {
    "portrait": SOURCE_PORTRAIT,
    "landscape": SOURCE_LANDSCAPE,
    "main": SOURCE_MAIN,
}


def _source_for_heard_text(text: str) -> str:
    """The Funestra an unrecognized utterance at least named, if it named one.

    A phrase the grammar rejected can still say who it was for — "landscape full
    length please" is landscape's problem — so the report flashes over that
    Funestra rather than where a satellite's mis-hearing reads as the Main
    Funestra's.  Whole words only: "portrait" has to be the word spoken and not a
    fragment of a longer one, and the first Funestra word wins.
    """
    for word in text.lower().split():
        source = _SPOKEN_FUNESTRA_SOURCES.get(word)
        if source is not None:
            return source
    return SOURCE_SYSTEM

# The three keys a pause leaves live (#SuspendExempt in windows_bridge_hotkeys.ahk),
# spoken, and the headset's four.  What a paused room hears is his call: CLAUDE.md.
SUSPEND_EXEMPT_COMMANDS: frozenset[str] = frozenset({
    "play", "quit", "relief_omnipause",
    "enter_vr", "exit_vr", "recenter_view", "vr_reset",
})


@dataclass(frozen=True)
class VoiceHold:
    heard: frozenset[str]
    ignored_while: str


DURING_OMNIPAUSE = VoiceHold(SUSPEND_EXEMPT_COMMANDS, "during OmniPause")
WHILE_NOT_WEARING_HEADSET = VoiceHold(frozenset({"quit"}), "while not wearing headset")


class VoiceHolds:
    def __init__(self) -> None:
        self._changes: list[tuple[float, VoiceHold | None]] = []

    @property
    def now(self) -> VoiceHold | None:
        return self._changes[-1][1] if self._changes else None

    def change(self, hold: VoiceHold | None, *, at: float) -> None:
        if hold != self.now:
            self._changes.append((at, hold))

    def when(self, moment: float) -> VoiceHold | None:
        changes_by_then = bisect_right(self._changes, moment, key=lambda change: change[0])
        return self._changes[changes_by_then - 1][1] if changes_by_then else None


def _holds_or_ends_the_room(command: str) -> bool:
    return (
        command in {"quit", "relief_omnipause", "robot_hand_park"}
        or command.endswith("_reset")
    )


def _written(phrase: str) -> str | None:
    friendly = friendly_voice(phrase)
    return friendly if friendly != phrase else None


def _said_in(phrase: str) -> str | None:
    return "de" if any(said in phrase for said in GENAU_SAID) else None


def command_rules(commands: Mapping[str, str], *, confidence_threshold: float,
                  confirm_commands: bool) -> CommandRules:
    """How the listener is to hear Fun Time's phrases.

    With *confirm_commands* a command acts only once the second listener has read
    it too; without, whatever the first settles acts at once and the second only
    rescues what the first could not settle.  Relief is the sensation emergency:
    it acts on the first listener's word either way."""
    relief = frozenset(phrase for phrase, command in commands.items()
                       if command == "relief_omnipause")
    return CommandRules(
        phrases=frozenset(commands),
        never_rescued=lambda phrase: _holds_or_ends_the_room(commands[phrase]),
        confidence_threshold=confidence_threshold,
        stands_alone=(relief if confirm_commands else frozenset(commands)).__contains__,
        written=_written,
        said_in=_said_in,
    )


@dataclass(frozen=True)
class _NotSureOf:
    phrase: str
    spoken_at: float


class VoiceController:
    """Listens for voice commands and writes them to the dashboard command file."""

    def __init__(
        self,
        *,
        cmd_file: Path | str,
        model_path: str,
        confidence_threshold: float = 0.7,
        device_name: str | None = None,
        sample_rate: int = 16000,
        confirm_commands: bool = True,
        second_listener: Callable[[bytes, str], str] | None = None,
        hosted: HostedVocabulary = NO_HOSTED_VOCABULARY,
        hosted_cmd_file: Path | str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cmd_file = Path(cmd_file)
        self.commands = (build_voice_commands(origenerator_phrases=hosted.phrases)
                         if hosted.phrases else VOICE_COMMANDS)
        self._hosted = hosted
        self._hosted_cmd_file = hosted_cmd_file
        self._request_side: str | None = None
        self.hosted_sides: Callable[[], frozenset[str]] = frozenset
        self._muted = threading.Event()
        self._clock = clock
        self._holds = VoiceHolds()
        self._not_sure_of: _NotSureOf | None = None
        self._not_sure_of_lock = threading.Lock()
        self._words_forming = False
        self.done_starting = threading.Event()
        self.active_funestra: Callable[[], int | None] = lambda: None
        self.listener_settings = ListenerSettings(
            model_name=model_path,
            device_name=device_name,
            sample_rate=sample_rate,
            caption_misses=True,
            miss_dir=self.cmd_file.parent / "voice_misses",
            speech_hint=hosted.speech_hint,
        )
        takes_requests = bool(hosted.openers)
        self.listener_events = ListenerEvents(
            heard=self.handle_heard,
            partial=self._say_it_is_being_worked_on,
            recovered=self._announce_the_microphone_is_back,
            listening=self.done_starting.set,
            keeps_misses=self._is_listening,
            speech=self.handle_speech if takes_requests else None,
            wants_words=self.wants_words if takes_requests else None,
        )
        rules = command_rules(self.commands, confidence_threshold=confidence_threshold,
                              confirm_commands=confirm_commands)
        self.engines = engines_for(rules, self.listener_events, clock=clock,
                                   second_opinion=second_listener)
        self._listener = CommandListener(rules, self.listener_settings, self.listener_events,
                                         self.engines)

    def request_opened(self, side: str) -> None:
        self._request_side = side

    def request_closed(self, side: str) -> None:
        if self._request_side == side:
            self._request_side = None

    def _side_taking_a_request(self) -> str | None:
        side = self._request_side
        return side if side in self.hosted_sides() else None

    def _heard_through_a_request(self, heard: Heard) -> bool:
        phrase = heard.recognition.phrase
        return bool(phrase) and self.commands[phrase] in SUSPEND_EXEMPT_COMMANDS

    def _hosted_side_leading(self, text: str | None) -> str | None:
        said = _words(text)
        return said[0] if said and said[0] in self.hosted_sides() else None

    def _side_a_request_opens_on(self, text: str | None) -> str | None:
        said = _words(text)
        if len(said) < 2 or said[1] not in self._hosted.openers:
            return None
        return self._hosted_side_leading(text)

    def wants_words(self, heard: Heard) -> bool:
        if not self._listened_to_when(heard.spoken_at):
            return False
        if self._side_taking_a_request() is not None:
            return not self._heard_through_a_request(heard)
        recognition = heard.recognition
        if recognition.phrase:
            return False
        if recognition.unrecognized_text is not None:
            return self._hosted_side_leading(recognition.heard) is not None
        return self._side_a_request_opens_on(recognition.heard) is not None

    def handle_speech(self, words: str, heard: Heard) -> None:
        side = self._side_taking_a_request()
        if side is not None:
            append_command(self._hosted_cmd_file, said_to_the_hosted_app(side, words))
            return
        side = self._side_a_request_opens_on(words)
        if side is not None:
            append_command(self._hosted_cmd_file,
                           said_to_the_hosted_app(side, _without_its_first_word(words)))
            return
        phrase = " ".join(_words(words))
        if self._said_to_a_hosted_side(phrase):
            self._hand_on(phrase, spoken_at=heard.spoken_at)
            return
        self._report_a_miss(heard)

    def _said_to_a_hosted_side(self, phrase: str) -> bool:
        said = said_to_a_side(self.commands.get(phrase, ""))
        return said is not None and said[0] in self.hosted_sides()

    @property
    def is_muted(self) -> bool:
        """Return True if voice commands are being suppressed."""
        return self._muted.is_set()

    def _is_listening(self) -> bool:
        """Whether spoken input is currently acted on — not muted, not suspended.

        A muted or omnipaused room's talk is discarded, so it is not captioned
        either.
        """
        return not self._muted.is_set() and self._holds.now is None

    def _listened_to_when(self, spoken_at: float) -> bool:
        return not self._muted.is_set() and self._holds.when(spoken_at) is None

    def mute(self) -> None:
        """Suppress command output (voice still listens but discards)."""
        self._muted.set()
        with self._not_sure_of_lock:
            self._not_sure_of = None

    def unmute(self) -> None:
        """Resume command output."""
        self._muted.clear()

    def suspend(self, hold: VoiceHold = DURING_OMNIPAUSE) -> None:
        self._holds.change(hold, at=self._clock())

    def unsuspend(self) -> None:
        self._holds.change(None, at=self._clock())

    def _write_spoken(self, phrase: str, *, spoken_at: float) -> bool:
        """Append the phrase's command to the dashboard command file; return whether it was.

        No-op (returns False) when muted, and for everything the hold it was said
        under does not hear.  The line carries *spoken_at*, so
        the dispatcher acts on the video that was on screen when the user started
        talking rather than whatever replaced it during recognition.
        """
        command = self.commands[phrase]
        if self._muted.is_set():
            return False
        hold = self._holds.when(spoken_at)
        if hold is not None and command not in hold.heard:
            return False
        return append_command(self.cmd_file, format_spoken_command(
            command, spoken_at=spoken_at, said=friendly_voice(phrase)))

    def handle_heard(self, heard: Heard) -> None:
        if self.wants_words(heard):
            return
        if heard.recognition.phrase:
            self._hand_on(heard.recognition.phrase, spoken_at=heard.spoken_at)
            return
        self._report_a_miss(heard)

    def _report_a_miss(self, heard: Heard) -> None:
        recognition = heard.recognition
        if not heard.words_formed or not self._listened_to_when(heard.spoken_at):
            return
        doubted = recognition.refused_phrase or recognition.unconfirmed_phrase
        if doubted:
            with self._not_sure_of_lock:
                self._not_sure_of = _NotSureOf(doubted, heard.spoken_at)
            self._report_words("not sure enough of a command", "not sure enough of",
                               friendly_voice(doubted), heard_text=doubted,
                               spoken_at=heard.spoken_at, then=PRESS_ENTER_TO_ACCEPT)
        elif recognition.unrecognized_text:
            self._report_words(NO_COMMAND, NO_COMMAND, recognition.unrecognized_text,
                               heard_text=recognition.unrecognized_text,
                               spoken_at=heard.spoken_at)
        elif recognition.silent_reading:
            self._report_words("too quiet to act on", "too quiet to act on",
                               recognition.silent_reading, heard_text=recognition.silent_reading,
                               spoken_at=heard.spoken_at)
        else:
            notice(logger, DID_NOT_CATCH_IT, source=SOURCE_SYSTEM, level=logging.WARNING)

    def accept_what_it_was_not_sure_of(self) -> bool:
        with self._not_sure_of_lock:
            not_sure_of, self._not_sure_of = self._not_sure_of, None
        if not_sure_of is None:
            return False
        self._hand_on(not_sure_of.phrase, spoken_at=not_sure_of.spoken_at)
        return True

    def _hand_on(self, phrase: str, *, spoken_at: float) -> None:
        written = self._write_spoken(phrase, spoken_at=spoken_at)
        hold = self._holds.when(spoken_at)
        if not written and hold is not None and not self._muted.is_set():
            notice(logger, f"ignored {hold.ignored_while}: {friendly_voice(phrase)}",
                   source=notice_source(self.commands[phrase], self.active_funestra()),
                   level=logging.WARNING)

    def _report_words(self, logged: str, shown: str, words: str, *, heard_text: str,
                      spoken_at: float, then: str = "") -> None:
        source = _source_for_heard_text(heard_text)
        after = f" {then}" if then else ""
        notice(logger, f"{logged} ({_how_many(words)}){after}", source=source,
               level=logging.WARNING, flashes=False, spoken_at=spoken_at)
        flash_unlogged(self.cmd_file.parent, f"{shown}: {words}{after}", source=source,
                       level=logging.WARNING, spoken_at=spoken_at)

    def _say_it_is_being_worked_on(self, forming: str) -> None:
        if forming and not self._words_forming and self._is_listening():
            notice(logger, WORKING_ON_IT, source=SOURCE_SYSTEM)
        self._words_forming = bool(forming)

    def _announce_the_microphone_is_back(self) -> None:
        notice(logger, "Voice control: audio from the microphone resumed", source=SOURCE_SYSTEM)

    def stop(self) -> None:
        """Signal the run loop to stop."""
        self._listener.stop()

    def run(self) -> None:
        """Blocking listen loop — call from a daemon thread."""
        try:
            self._listener.run()
            logger.info("Voice control stopped")
        except Exception:
            logger.exception("Voice control thread crashed")
        finally:
            self.done_starting.set()

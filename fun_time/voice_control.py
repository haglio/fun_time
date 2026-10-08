"""What Fun Time does with what the family's listener hears: a spoken phrase
becomes a line in the dashboard command file, where the dispatch loop picks it
up as it would an AHK hotkey.  Hearing itself is voice_core's."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app_support.file_channel import read_flag, write_flag
from player_core.file_channel import append_command
from voice_core.commands import CommandRules
from voice_core.listener import CommandListener, Engines, ListenerEvents, ListenerSettings
from voice_core.listening import Heard
from voice_core.whisper_reader import WhisperReader

from fun_time.command_dispatch import notice_source
from fun_time.event_log import (
    SOURCE_LANDSCAPE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
    notice,
)
from fun_time.unlogged_notices import flash_unlogged
from fun_time.voice_commands import (
    GENAU_SAID,
    VOICE_COMMANDS,
    format_spoken_command,
    friendly_voice,
)

logger = logging.getLogger(__name__)

WORKING_ON_IT = "Figuring out what you said..."
DID_NOT_CATCH_IT = "couldn't catch what you said"
NO_COMMAND = "unrecognized voice command"


def _how_many(words: str) -> str:
    count = len(words.split())
    return f"{count} word{'' if count == 1 else 's'}"

MIC_OFF_FILENAME = "mic_off.flag"


def say_the_mic_is_off(state_dir: Path, *, off: bool) -> None:
    write_flag(Path(state_dir) / MIC_OFF_FILENAME, off)


def take_whether_the_mic_was_off(state_dir: Path) -> bool:
    path = Path(state_dir) / MIC_OFF_FILENAME
    was_off = read_flag(path, default=False)
    path.unlink(missing_ok=True)
    return was_off


# The player words a speaker can put in any command, and which window a notice
# about that player flashes over.  "both" is deliberately absent: it addresses
# two players, and a notice flashes over one.
_SPOKEN_PLAYER_SOURCES: dict[str, str] = {
    "portrait": SOURCE_PORTRAIT,
    "landscape": SOURCE_LANDSCAPE,
    "main": SOURCE_MAIN,
}


def _source_for_heard_text(text: str) -> str:
    """The player an unrecognized utterance at least named, if it named one.

    A phrase the grammar rejected can still say who it was for — "landscape full
    length please" is landscape's problem — so the report flashes over that
    player rather than where a satellite's mis-hearing reads as the main
    player's.  Whole words only: "portrait" has to be the word spoken and not a
    fragment of a longer one, and the first player word wins.
    """
    for word in text.lower().split():
        source = _SPOKEN_PLAYER_SOURCES.get(word)
        if source is not None:
            return source
    return SOURCE_SYSTEM

# Omnipause suspends the AHK hotkeys wholesale and exempts exactly three: Esc,
# which resumes, Ctrl+Alt+Q, which quits, and Shift+Esc, which retracts the OSR2
# (``#SuspendExempt`` in windows_bridge_hotkeys.ahk).  Voice mirrors those three
# and adds nothing — "play" resumes, "quit" quits, "relief omnipause"
# retracts, and that last one has to reach a room that is ALREADY paused, because
# a paused session can still have the device on the user.  Nothing else a paused
# room says reaches the dispatch loop.  Widening this set is the owner's call --
# see CLAUDE.md, "Standing rules", and the test that pins the whole frozenset.
SUSPEND_EXEMPT_COMMANDS: frozenset[str] = frozenset({"play", "quit", "relief_omnipause"})


@dataclass(frozen=True)
class VoiceHold:
    heard: frozenset[str]
    ignored_while: str


DURING_OMNIPAUSE = VoiceHold(SUSPEND_EXEMPT_COMMANDS, "during OmniPause")
WHILE_NOT_WEARING_HEADSET = VoiceHold(frozenset({"quit"}), "while not wearing headset")


def _holds_or_ends_the_room(command: str) -> bool:
    return (
        command in {"quit", "relief_omnipause", "robot_hand_park", "robot_hand_retract"}
        or command.endswith("_reset")
    )


def _never_rescued(phrase: str) -> bool:
    return _holds_or_ends_the_room(VOICE_COMMANDS[phrase])


def _written(phrase: str) -> str | None:
    friendly = friendly_voice(phrase)
    return friendly if friendly != phrase else None


def _said_in(phrase: str) -> str | None:
    return "de" if any(said in phrase for said in GENAU_SAID) else None


# Relief is the sensation emergency: it acts on the first listener's word, where
# every other command can wait the half second the second one takes to agree.
_RELIEF_PHRASES = frozenset(
    phrase for phrase, command in VOICE_COMMANDS.items() if command == "relief_omnipause")


def command_rules(*, confidence_threshold: float, confirm_commands: bool) -> CommandRules:
    """How the listener is to hear Fun Time's phrases.

    With *confirm_commands* a command acts only once the second listener has read
    it too; without, whatever the first settles acts at once and the second only
    rescues what the first could not settle."""
    unconfirmed = _RELIEF_PHRASES if confirm_commands else frozenset(VOICE_COMMANDS)
    return CommandRules(
        phrases=frozenset(VOICE_COMMANDS),
        never_rescued=_never_rescued,
        confidence_threshold=confidence_threshold,
        stands_alone=unconfirmed.__contains__,
        written=_written,
        said_in=_said_in,
    )


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
    ) -> None:
        self.cmd_file = Path(cmd_file)
        self._muted = threading.Event()
        self._hold: VoiceHold | None = None
        self._words_forming = False
        self.active_player: Callable[[], int | None] = lambda: None
        self.listener_settings = ListenerSettings(
            model_name=model_path,
            device_name=device_name,
            sample_rate=sample_rate,
            caption_misses=True,
            miss_dir=self.cmd_file.parent / "voice_misses",
        )
        self.listener_events = ListenerEvents(
            heard=self.handle_heard,
            partial=self._say_it_is_being_worked_on,
            recovered=self._announce_the_microphone_is_back,
            keeps_misses=self._is_listening,
        )
        self.engines = Engines(second_opinion=second_listener or WhisperReader(),
                               second_opinion_in={"de": WhisperReader(language="de")})
        self._listener = CommandListener(
            command_rules(confidence_threshold=confidence_threshold,
                          confirm_commands=confirm_commands),
            self.listener_settings, self.listener_events, self.engines)

    @property
    def is_muted(self) -> bool:
        """Return True if voice commands are being suppressed."""
        return self._muted.is_set()

    def _is_listening(self) -> bool:
        """Whether spoken input is currently acted on — not muted, not suspended.

        A muted or omnipaused room's talk is discarded, so it is not captioned
        either.
        """
        return not self._muted.is_set() and self._hold is None

    def mute(self) -> None:
        """Suppress command output (voice still listens but discards)."""
        self._muted.set()

    def unmute(self) -> None:
        """Resume command output."""
        self._muted.clear()

    def suspend(self, hold: VoiceHold = DURING_OMNIPAUSE) -> None:
        self._hold = hold

    def unsuspend(self) -> None:
        self._hold = None

    def _write_spoken(self, phrase: str, *, spoken_at: float) -> bool:
        """Append the phrase's command to the dashboard command file; return whether it was.

        No-op (returns False) when muted, and — while held — for everything the
        hold does not hear.  The line carries *spoken_at*, so
        the dispatcher acts on the video that was on screen when the user started
        talking rather than whatever replaced it during recognition.
        """
        command = VOICE_COMMANDS[phrase]
        if self._muted.is_set():
            return False
        hold = self._hold
        if hold is not None and command not in hold.heard:
            return False
        return append_command(self.cmd_file, format_spoken_command(
            command, spoken_at=spoken_at, said=friendly_voice(phrase)))

    def handle_heard(self, heard: Heard) -> None:
        recognition = heard.recognition
        if recognition.phrase:
            self._hand_on(recognition.phrase, spoken_at=heard.spoken_at)
            return
        if not heard.words_formed:
            return
        doubted = recognition.refused_phrase or recognition.unconfirmed_phrase
        if doubted:
            self._report_words("not sure enough of a command", "not sure enough of",
                               friendly_voice(doubted), heard_text=doubted)
        elif recognition.unrecognized_text:
            self._report_words(NO_COMMAND, NO_COMMAND, recognition.unrecognized_text,
                               heard_text=recognition.unrecognized_text)
        elif recognition.silent_reading:
            self._report_words("too quiet to act on", "too quiet to act on",
                               recognition.silent_reading, heard_text=recognition.silent_reading)
        elif self._is_listening():
            notice(logger, DID_NOT_CATCH_IT, source=SOURCE_SYSTEM, level=logging.WARNING)

    def _hand_on(self, phrase: str, *, spoken_at: float) -> None:
        written = self._write_spoken(phrase, spoken_at=spoken_at)
        hold = self._hold
        if not written and hold is not None and not self._muted.is_set():
            notice(logger, f"ignored {hold.ignored_while}: {friendly_voice(phrase)}",
                   source=notice_source(VOICE_COMMANDS[phrase], self.active_player()),
                   level=logging.WARNING)

    def _report_words(self, logged: str, shown: str, words: str, *, heard_text: str) -> None:
        if not self._is_listening():
            return
        source = _source_for_heard_text(heard_text)
        notice(logger, f"{logged} ({_how_many(words)})", source=source,
               level=logging.WARNING, flashes=False)
        flash_unlogged(self.cmd_file.parent, f"{shown}: {words}", source=source,
                       level=logging.WARNING)

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

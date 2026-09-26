"""What Fun Time does with what the family's listener hears: a spoken phrase
becomes a line in the dashboard command file, where the dispatch loop picks it
up as it would an AHK hotkey.  Hearing itself is voice_core's."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from player_core.file_channel import append_command
from voice_core.commands import CommandRules
from voice_core.listener import CommandListener, Engines, ListenerEvents, ListenerSettings
from voice_core.listening import Heard
from voice_core.whisper_reader import WhisperReader

from fun_time.command_dispatch import command_player
from fun_time.event_log import (
    SOURCE_LANDSCAPE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
    notice,
)
from fun_time.voice_commands import (
    SELF_REPORTING_COMMANDS,
    VOICE_COMMANDS,
    format_spoken_command,
    friendly_voice,
)

logger = logging.getLogger(__name__)

WORKING_ON_IT = "Figuring out what you said..."


def _source_for_command(command: str, active_player: int | None = None) -> str:
    """The event-log source a recognized command's confirmation flashes on.

    A command naming a player flashes over it; a bare one ("next" after
    "portrait next") names none but REACHES one -- whichever the session last
    addressed -- and belongs over that player rather than the main one it would
    otherwise default to.  Everything else flashes on the main player.
    """
    player = active_player if command.startswith("active_") else command_player(command)
    return {
        1: SOURCE_MAIN,
        2: SOURCE_PORTRAIT,
        3: SOURCE_LANDSCAPE,
    }.get(player, SOURCE_SYSTEM)


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
        self._suspended = threading.Event()
        self._words_forming = False
        # Which player a bare command reaches, and whether one goes to the hosted
        # app's show, asked of the dispatch loop as it is spoken: one process.
        self.active_player: Callable[[], int | None] = lambda: None
        self.hands_to_the_hosted_app: Callable[[str], bool] = lambda _command: False
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
        self.engines = Engines(second_opinion=second_listener or WhisperReader())
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
        return not self._muted.is_set() and not self._suspended.is_set()

    def mute(self) -> None:
        """Suppress command output (voice still listens but discards)."""
        self._muted.set()

    def unmute(self) -> None:
        """Resume command output."""
        self._muted.clear()

    def suspend(self) -> None:
        """Freeze voice for the duration of omnipause, save the exempt commands."""
        self._suspended.set()

    def unsuspend(self) -> None:
        """Thaw voice when omnipause lifts."""
        self._suspended.clear()

    def _write_command(self, command: str, *, spoken_at: float) -> bool:
        """Append a command to the dashboard command file; return whether it was.

        No-op (returns False) when muted, and — while suspended by omnipause —
        for everything but the exempt commands.  The line carries *spoken_at*, so
        the dispatcher acts on the video that was on screen when the user started
        talking rather than whatever replaced it during recognition.
        """
        if self._muted.is_set():
            return False
        if self._suspended.is_set() and command not in SUSPEND_EXEMPT_COMMANDS:
            return False
        return append_command(self.cmd_file, format_spoken_command(command, spoken_at=spoken_at))

    def handle_heard(self, heard: Heard) -> None:
        """Act on one utterance the listener settled.

        On screen it stays quiet: a white confirmation over the player a
        dispatched command addresses, a yellow report over the player a refused,
        doubted or unmatched phrase named -- the confirmation only when the
        command really dispatched, the reports only while the room is being
        listened to.  The log line for every outcome is the listener's.
        """
        recognition = heard.recognition
        if recognition.phrase:
            self._dispatch(recognition.phrase, spoken_at=heard.spoken_at)
            return
        doubted = recognition.refused_phrase or recognition.unconfirmed_phrase
        if doubted:
            self._report(f"not sure enough of: {friendly_voice(doubted)}", heard_text=doubted)
        elif recognition.unrecognized_text:
            self._report(f"unrecognized voice command: {recognition.unrecognized_text}",
                         heard_text=recognition.unrecognized_text)

    def _dispatch(self, phrase: str, *, spoken_at: float) -> None:
        command = VOICE_COMMANDS[phrase]
        dispatched = self._write_command(command, spoken_at=spoken_at)
        source = _source_for_command(command, self.active_player())
        reported_by_the_dispatch = (command in SELF_REPORTING_COMMANDS
                                    and not self.hands_to_the_hosted_app(command))
        if dispatched and not reported_by_the_dispatch:
            notice(logger, friendly_voice(phrase), source=source)
        elif not dispatched and not self._muted.is_set() and self._suspended.is_set():
            notice(logger, f"ignored during OmniPause: {friendly_voice(phrase)}", source=source,
                   level=logging.WARNING)

    def _report(self, message: str, *, heard_text: str) -> None:
        if self._is_listening():
            notice(logger, message, source=_source_for_heard_text(heard_text),
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

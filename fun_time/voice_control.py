"""Voice control module for Fun Time.

Uses Vosk (offline speech recognition) with a restricted grammar to
recognize voice commands and write them to the dashboard command file,
where the dispatch loop picks them up identically to AHK hotkey commands.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from player_core.file_channel import append_command

from fun_time.command_dispatch import command_side
from fun_time.event_log import (
    SOURCE_LANDSCAPE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    SOURCE_SYSTEM,
    notice,
)
from fun_time.mic_selection import resolve_input_device
from fun_time.voice_commands import (
    SELF_REPORTING_COMMANDS,
    VOICE_COMMANDS,
    format_spoken_command,
    friendly_voice,
)

logger = logging.getLogger(__name__)


def _source_for_command(command: str) -> str:
    """The event-log source a recognized command's confirmation flashes on.

    A command addressed to a satellite or the main player flashes over that player;
    everything else (mode switches, Genau params, the audio level) has no single
    player, so it flashes on the main player via ``system``.
    """
    return {
        1: SOURCE_MAIN,
        2: SOURCE_PORTRAIT,
        3: SOURCE_LANDSCAPE,
    }.get(command_side(command), SOURCE_SYSTEM)


# The player words a speaker can put in any command, and which window a notice
# about that player flashes over.  "main" is the main player's synonym throughout the
# spoken vocabulary, so it names the same player here.  "both" is deliberately
# absent: it addresses two players, and a notice flashes over one.
_SPOKEN_PLAYER_SOURCES: dict[str, str] = {
    "portrait": SOURCE_PORTRAIT,
    "landscape": SOURCE_LANDSCAPE,
    "main": SOURCE_MAIN,
}


def _source_for_heard_text(text: str) -> str:
    """The player an unrecognized utterance at least named, if it named one.

    A phrase the grammar rejected can still say who it was for — "landscape full
    length please" is landscape's problem — so the report flashes over that
    player instead of defaulting to the main player, where a satellite's mis-hearing
    would be read as the main player's.  Matched on whole words, since "portrait" has
    to be the word spoken and not a fragment of a longer one; the first player
    word wins when a mis-hearing produces two.  A phrase naming no player is
    SOURCE_SYSTEM, which flashes over the main player as everything session-wide does.
    """
    for word in text.lower().split():
        source = _SPOKEN_PLAYER_SOURCES.get(word)
        if source is not None:
            return source
    return SOURCE_SYSTEM

# Omnipause suspends the AHK hotkeys wholesale and exempts exactly three: Esc,
# which resumes, Ctrl+Alt+Q, which quits, and Shift+Esc, which retracts the OSR2
# (``#SuspendExempt`` in windows_bridge_hotkeys.ahk).  Voice mirrors those three
# and adds nothing — "play" resumes, "quit"/"exit" quits, "relief omnipause"
# retracts, and that last one has to reach a room that is ALREADY paused, because
# a paused session can still have the device on the user.  Nothing else a paused
# room says reaches the dispatch loop.  Widening this set is the owner's call --
# see CLAUDE.md, "Standing rules", and the test that pins the whole frozenset.
SUSPEND_EXEMPT_COMMANDS: frozenset[str] = frozenset({"play", "quit", "relief_omnipause"})


def build_grammar() -> str:
    """Build a Vosk grammar JSON string from VOICE_COMMANDS."""
    phrases = sorted(VOICE_COMMANDS.keys())
    phrases.append("[unk]")
    return json.dumps(phrases)


def has_partial_text(raw_json: str) -> bool:
    """Whether Vosk's partial hypothesis currently holds any words."""
    return bool(json.loads(raw_json).get("partial", "").strip())


class UtteranceOnset:
    """When the speech Vosk is currently decoding began.

    Each audio block is offered here with the wall time its capture *started*
    and whether Vosk holds a partial hypothesis after consuming it.  Speech
    began at the first block of the current unbroken run of partials; a block
    that leaves the partial empty ends the run, so a false start cannot
    back-date the utterance that follows it.
    """

    def __init__(self) -> None:
        self._started_at: float | None = None

    def note_block(self, *, block_started_at: float, has_partial: bool) -> None:
        if not has_partial:
            self._started_at = None
        elif self._started_at is None:
            self._started_at = block_started_at

    def take(self, *, fallback: float) -> float:
        """Consume the onset for the utterance just recognized.

        *fallback* covers a phrase short enough to be recognized from the very
        block that carried it, with no partial ever observed.
        """
        started_at = self._started_at
        self._started_at = None
        return fallback if started_at is None else started_at


def _text_and_confidence(raw_json: str) -> tuple[str, float | None]:
    """The recognized text and its mean per-word confidence (None if unscored)."""
    data = json.loads(raw_json)
    text = data.get("text", "").strip()
    words = data.get("result")
    if not words:
        return text, None
    return text, sum(w.get("conf", 0) for w in words) / len(words)


@dataclass(frozen=True)
class Recognition:
    """What the listener made of one utterance.

    Exactly one of these holds, or none (noise): a *command* was recognized from
    the grammar (with the *phrase* that matched, for the confirmation flash), or
    speech was clearly heard but matched no command (*unrecognized_text*, the free
    recognizer's transcription — what lets the user see that "full length" came
    through as something else).
    """

    command: str | None = None
    phrase: str | None = None
    unrecognized_text: str | None = None


def interpret_recognition(grammar_json: str, free_json: str, *, threshold: float) -> Recognition:
    """Combine the grammar and free recognizers' takes on one utterance.

    The grammar recognizer is the authority on commands — its restricted
    vocabulary is what keeps recognition accurate.  The free recognizer runs
    only to caption what was said when the grammar matched nothing confident, so
    an out-of-grammar phrase surfaces as text instead of vanishing into "[unk]".
    """
    text, conf = _text_and_confidence(grammar_json)
    if text and text != "[unk]":
        command = VOICE_COMMANDS.get(text)
        if command is not None and conf is not None and conf >= threshold:
            return Recognition(command=command, phrase=text)
    heard, heard_conf = _text_and_confidence(free_json)
    if heard and heard != "[unk]" and heard_conf is not None and heard_conf >= threshold:
        return Recognition(unrecognized_text=heard)
    return Recognition()


_VOICE_IMPORT_ERROR: str = ""
try:
    import sounddevice as sd
    import vosk
# Absent (ImportError), present but without PortAudio (OSError), or broken --
# sounddevice dies in ctypes.util where ctypes has no Win32 half, which
# tests/test_win32_loader.py stages: unavailable either way, message kept.
except Exception as _exc:
    vosk = None  # type: ignore[assignment]
    sd = None  # type: ignore[assignment]
    _VOICE_IMPORT_ERROR = str(_exc)

VOICE_AVAILABLE = vosk is not None and sd is not None


def voice_import_error() -> str:
    """Why voice control is unavailable, or "" when it is available.

    An accessor rather than the module global it reads, so the two orchestrators
    that report this cannot bind a name this module calls its own.
    """
    return _VOICE_IMPORT_ERROR


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
    ) -> None:
        self.cmd_file = Path(cmd_file)
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.device_name = device_name
        self.sample_rate = sample_rate
        self._stop = threading.Event()
        self._muted = threading.Event()
        self._suspended = threading.Event()

    @property
    def is_muted(self) -> bool:
        """Return True if voice commands are being suppressed."""
        return self._muted.is_set()

    def _is_listening(self) -> bool:
        """Whether spoken input is currently acted on — not muted, not suspended.

        Gates the recognition feedback (the "unrecognized voice command" flash): a
        muted or omnipaused room's talk is discarded, so it must not be captioned
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

        No-op (returns False) when muted (the user turned voice off), and — while
        suspended by omnipause — for everything but the exempt commands.  The
        caller flashes a confirmation only when the command actually went through.

        The line carries *spoken_at* — when the utterance began — so the
        dispatcher can act on the video that was on screen then, not on
        whatever replaced it while the phrase was still being recognized.
        """
        if self._muted.is_set():
            return False
        if self._suspended.is_set() and command not in SUSPEND_EXEMPT_COMMANDS:
            logger.debug("Voice suspended by omnipause: ignored %s", command)
            return False
        return append_command(self.cmd_file, format_spoken_command(command, spoken_at=spoken_at))

    def _handle_recognition(self, interp: Recognition, *, spoken_at: float) -> None:
        """Act on one interpreted utterance: dispatch, confirm, or report.

        A recognized command that actually dispatches flashes a plain white
        confirmation over the player it addresses; speech that matched nothing
        flashes a red "unrecognized voice command: …" so a mis-heard phrase is
        visible rather than silent — over the player it named, if it named one,
        which is where the user was already looking when they said it.
        Confirmations follow whether the command dispatched, so a muted/omnipaused
        no-op stays quiet; the unrecognized report is gated on the room actually
        being listened to.
        """
        if interp.command:
            logger.info("Voice command: %s (spoken %.2fs before recognition)",
                        interp.command, time.monotonic() - spoken_at)
            dispatched = self._write_command(interp.command, spoken_at=spoken_at)
            if dispatched and interp.command not in SELF_REPORTING_COMMANDS:
                notice(
                    logger,
                    friendly_voice(interp.phrase or interp.command),
                    source=_source_for_command(interp.command),
                )
        elif interp.unrecognized_text and self._is_listening():
            logger.info("Unrecognized speech: %s", interp.unrecognized_text)
            notice(
                logger,
                f"unrecognized voice command: {interp.unrecognized_text}",
                source=_source_for_heard_text(interp.unrecognized_text),
                level=logging.ERROR,
            )

    def _resolve_device(self) -> int | None:
        """The sounddevice input index to open the listen stream on.

        Resolved from ``device_name`` (a mic-name substring).  Pinning by name
        rather than a fragile index is deliberate: Windows renumbers devices when
        one is added or removed, and its default input is often a dead virtual
        mic (a VR headset, "Sound Mapper") that returns pure silence and so
        silently kills every voice command.  Falls back to None (the system
        default) when no name is configured or none matches.
        """
        if not self.device_name:
            return None
        try:
            index, name = resolve_input_device(self.device_name)
        except Exception:
            logger.exception("Voice control device lookup failed; using system default")
            return None
        if index is None:
            logger.warning(
                "Voice control: no input device matching %r; using system default",
                self.device_name,
            )
        else:
            logger.info("Voice control listening on input device %s (%s)", index, name)
        return index

    def stop(self) -> None:
        """Signal the run loop to stop."""
        self._stop.set()

    def run(self) -> None:
        """Blocking listen loop — call from a daemon thread.

        Reads audio from the default microphone, feeds it to Vosk with
        a restricted grammar, and writes recognized commands to the
        dashboard command file.
        """
        if not VOICE_AVAILABLE:
            raise ImportError("vosk and sounddevice are required for voice control")

        import queue as _queue

        # Each block is queued with the monotonic time its capture ENDED — the
        # moment the callback fires.  The block's start is that minus its
        # duration, which is what dates an utterance's first block.
        audio_q: _queue.Queue[tuple[bytes, float]] = _queue.Queue()

        def _callback(indata, _frames, _time_info, status):
            if status:
                logger.debug("audio status: %s", status)
            audio_q.put((bytes(indata), time.monotonic()))

        onset = UtteranceOnset()
        device = self._resolve_device()

        try:
            model = vosk.Model(model_name=self.model_path)
            grammar = build_grammar()
            rec = vosk.KaldiRecognizer(model, self.sample_rate, grammar)
            # A second, unrestricted recognizer runs alongside the grammar one,
            # fed the same audio, purely to transcribe what was said when the
            # grammar matches nothing — so an out-of-grammar phrase can be shown
            # back as "unrecognized voice command: <what it heard>" instead of silently
            # becoming "[unk]".  It never drives a dispatch.
            free_rec = vosk.KaldiRecognizer(model, self.sample_rate)
            # Grammar mode reports per-word confidences only when words are
            # enabled; without them every recognition arrives unscored and the
            # confidence threshold below can never reject anything.
            rec.SetWords(True)
            free_rec.SetWords(True)
            logger.info("Voice control listening (model=%s, rate=%d, device=%s)",
                        self.model_path, self.sample_rate, device)

            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=8000,
                dtype="int16",
                channels=1,
                device=device,
                callback=_callback,
            ):
                while not self._stop.is_set():
                    try:
                        data, captured_at = audio_q.get(timeout=0.5)
                    except _queue.Empty:
                        continue
                    block_started_at = captured_at - (len(data) / 2) / self.sample_rate
                    grammar_final = rec.AcceptWaveform(data)
                    # Feed the free recognizer the same block so its own
                    # end-of-utterance lands with the grammar's.
                    free_final = free_rec.AcceptWaveform(data)
                    if grammar_final:
                        free_json = free_rec.Result() if free_final else free_rec.FinalResult()
                        interp = interpret_recognition(
                            rec.Result(), free_json, threshold=self.confidence_threshold,
                        )
                        spoken_at = onset.take(fallback=block_started_at)
                        self._handle_recognition(interp, spoken_at=spoken_at)
                    else:
                        onset.note_block(
                            block_started_at=block_started_at,
                            has_partial=has_partial_text(rec.PartialResult()),
                        )

            logger.info("Voice control stopped")
        except Exception:
            logger.exception("Voice control thread crashed")

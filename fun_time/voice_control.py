"""Vosk listens on a restricted grammar and writes what it hears to the dashboard
command file, where the dispatch loop picks it up as it would an AHK hotkey."""
from __future__ import annotations

import array
import json
import logging
import math
import threading
import time
from collections.abc import Sequence
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

    A command addressed to one player flashes over it; everything else has no
    single player, so it flashes on the main player via ``system``.
    """
    return {
        1: SOURCE_MAIN,
        2: SOURCE_PORTRAIT,
        3: SOURCE_LANDSCAPE,
    }.get(command_side(command), SOURCE_SYSTEM)


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

    Speech began at the first block of the current unbroken run of partials; a
    block that leaves the partial empty ends the run, so a false start cannot
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
        """Consume the onset for the utterance just recognized; *fallback* covers
        a phrase recognized from the very block that carried it."""
        started_at = self._started_at
        self._started_at = None
        return fallback if started_at is None else started_at


# How many of the recognizer's ranked readings the phrase list gets to filter.
# Vosk's "grammar" restricts the VOCABULARY, not the phrases: it decodes any
# sequence of the 181 words the phrases are built from, and most such sequences
# are no command -- "portrait next" comes back as "portrait net", "net" being a
# word only because "widen net" is a phrase.  The right phrase sits in the
# rankings under it, where the exact-match lookup never looked.
GRAMMAR_ALTERNATIVES = 5

# A command that ENDS the session may only be the recognizer's first choice: a
# repair promotes a reading vosk ranked below another, which is a fine trade for
# a satellite nudge and a bad one for quitting the room.
NO_REPAIR_INTO: frozenset[str] = frozenset({"quit"})


@dataclass(frozen=True)
class Hypothesis:
    """One reading of an utterance, and vosk's per-word scores for it -- empty in
    alternatives mode, where it scores whole readings: unscored, never zero."""

    text: str
    confidences: tuple[float, ...] = ()


def _hypotheses(raw_json: str) -> list[Hypothesis]:
    """The recognizer's readings of one utterance, best first -- from either shape
    vosk emits, ``{"text", "result"}`` or the ``SetMaxAlternatives`` list."""
    data = json.loads(raw_json) if raw_json else {}
    readings = data.get("alternatives") or [data]
    out = []
    for reading in readings:
        text = reading.get("text", "").strip()
        if not text:
            continue
        words = reading.get("result") or []
        out.append(Hypothesis(text, tuple(w["conf"] for w in words if "conf" in w)))
    return out


def _text_and_confidences(raw_json: str) -> tuple[str, list[float]]:
    """The best reading's text and its per-word confidences (empty if unscored)."""
    best = _hypotheses(raw_json)
    return (best[0].text, list(best[0].confidences)) if best else ("", [])


def _clears(confidences: Sequence[float], threshold: float) -> bool:
    """Whether the words' mean confidence reaches *threshold* -- inclusive, and
    decided as a sum against the bar times the count rather than as a quotient,
    which rounds a phrase spoken exactly at the bar under it (bug 86).  Unscored
    words are no evidence at all.
    """
    return bool(confidences) and math.fsum(confidences) >= threshold * len(confidences)


@dataclass(frozen=True)
class Recognition:
    """What the listener made of one utterance -- always something, never nothing.

    A *command* (the *phrase* that matched, and the *rank* vosk gave it: 0 its
    first choice, higher a repair the phrase list rescued), a phrase the
    confidence gate *refused*, speech matching no command (*unrecognized_text*),
    or none of those -- which "it did nothing" used to cover indistinguishably.
    *heard* is vosk's best reading, which a repair is then logged against."""

    command: str | None = None
    phrase: str | None = None
    rank: int = 0
    refused_phrase: str | None = None
    unrecognized_text: str | None = None
    heard: str | None = None


def interpret_recognition(grammar_json: str, free_json: str, *, threshold: float) -> Recognition:
    """Combine the grammar and free recognizers' takes on one utterance.

    The grammar recognizer is the authority, and its first reading that is a
    phrase wins: the phrase list filters, vosk's ranking is the evidence, and
    nothing here invents a similarity of its own.  The free recognizer only
    captions an utterance the grammar made nothing of; ``threshold`` gates that
    caption -- room noise it latches onto must not caption a phantom command --
    and any reading vosk scored."""
    hypotheses = _hypotheses(grammar_json)
    spoken = next((h.text for h in hypotheses if h.text != "[unk]"), None)
    for rank, hypothesis in enumerate(hypotheses):
        if hypothesis.text == "[unk]":
            continue
        command = VOICE_COMMANDS.get(hypothesis.text)
        if command is None:
            continue
        if hypothesis.confidences and not _clears(hypothesis.confidences, threshold):
            # Scored, and under the bar.  A lower-ranked reading is less likely
            # still, so this ends the search rather than falling through to one.
            return Recognition(refused_phrase=hypothesis.text, heard=spoken)
        if rank and command in NO_REPAIR_INTO:
            continue
        return Recognition(command=command, phrase=hypothesis.text, rank=rank, heard=spoken)
    if spoken:
        return Recognition(unrecognized_text=spoken, heard=spoken)
    heard, heard_confidences = _text_and_confidences(free_json)
    if heard and heard != "[unk]" and _clears(heard_confidences, threshold):
        return Recognition(unrecognized_text=heard)
    return Recognition()


# Blocks arrive twice a second, so ten seconds of nothing is the stream gone.
AUDIO_STALL_S = 10.0

# How often the loop says what it hears even when nothing is recognized: without
# it, a session where every phrase missed and one where the microphone was dead
# leave identical logs.
LISTEN_HEARTBEAT_S = 60.0


class CaptureLevel:
    """The loudest sample delivered, per utterance and per heartbeat.

    Its job is to be in the log when a command misses: a peak of 30 says the
    microphone is muted, gated or aimed elsewhere, and one of 8000 says the audio
    was fine and the recognizer is the stage to look at.
    """

    def __init__(self) -> None:
        self._utterance = 0
        self._recent = 0

    def note_block(self, pcm: bytes) -> None:
        block = array.array("h")
        block.frombytes(pcm[: len(pcm) // 2 * 2])
        if not block:
            return
        peak = max(max(block), -min(block))
        self._utterance = max(self._utterance, peak)
        self._recent = max(self._recent, peak)

    def take_utterance(self) -> int:
        peak, self._utterance = self._utterance, 0
        return peak

    def take_recent(self) -> int:
        peak, self._recent = self._recent, 0
        return peak


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

    An accessor, so the two orchestrators that report it cannot bind this
    module's global as a name of their own.
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
            logger.debug("Voice suspended by omnipause: ignored %s", command)
            return False
        return append_command(self.cmd_file, format_spoken_command(command, spoken_at=spoken_at))

    def _handle_recognition(self, interp: Recognition, *, spoken_at: float, peak: int = 0) -> None:
        """Act on one interpreted utterance -- and say which of its ends it reached.

        Every finalized utterance leaves one log line naming its outcome and the
        level the microphone delivered, so a command that misses says where it
        died instead of leaving the same silence as an unplugged microphone.  On
        screen it stays quieter: a white confirmation over the player a
        dispatched command addresses, a red report over the player a refused or
        unmatched phrase named -- the confirmation only when the command really
        dispatched, the reports only while the room is being listened to.
        """
        heard_at = time.monotonic() - spoken_at
        if interp.command:
            if interp.rank:
                logger.info(
                    "Voice command: %s -- %r was the recognizer's choice %d, "
                    "under %r, which is no command (spoken %.2fs before recognition, peak %d)",
                    interp.command, interp.phrase, interp.rank + 1, interp.heard, heard_at, peak,
                )
            else:
                logger.info("Voice command: %s (spoken %.2fs before recognition, peak %d)",
                            interp.command, heard_at, peak)
            dispatched = self._write_command(interp.command, spoken_at=spoken_at)
            if dispatched and interp.command not in SELF_REPORTING_COMMANDS:
                notice(
                    logger,
                    friendly_voice(interp.phrase or interp.command),
                    source=_source_for_command(interp.command),
                )
        elif interp.refused_phrase:
            logger.info("Voice: heard %r but its confidence was under %.2f (peak %d)",
                        interp.refused_phrase, self.confidence_threshold, peak)
            if self._is_listening():
                notice(
                    logger,
                    f"not sure enough of: {friendly_voice(interp.refused_phrase)}",
                    source=_source_for_heard_text(interp.refused_phrase),
                    level=logging.ERROR,
                )
        elif interp.unrecognized_text:
            logger.info("Unrecognized speech: %s (peak %d)", interp.unrecognized_text, peak)
            if self._is_listening():
                notice(
                    logger,
                    f"unrecognized voice command: {interp.unrecognized_text}",
                    source=_source_for_heard_text(interp.unrecognized_text),
                    level=logging.ERROR,
                )
        else:
            logger.debug("Voice: an utterance ended with nothing in it (peak %d)", peak)

    def _resolve_device(self) -> int | None:
        """The sounddevice input index to open the listen stream on.

        Resolved from ``device_name``, a mic-name substring — see
        :mod:`fun_time.mic_selection` for why it is a name and not an index.
        Falls back to None (the system default) when nothing matches.
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
        """Blocking listen loop — call from a daemon thread."""
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
        level = CaptureLevel()
        device = self._resolve_device()

        try:
            model = vosk.Model(model_name=self.model_path)
            grammar = build_grammar()
            rec = vosk.KaldiRecognizer(model, self.sample_rate, grammar)
            # A second, unrestricted recognizer runs alongside the grammar one,
            # fed the same audio, purely to transcribe an utterance the grammar
            # made nothing of.  It never drives a dispatch.
            free_rec = vosk.KaldiRecognizer(model, self.sample_rate)
            rec.SetWords(True)
            # ...and the ranked readings the phrase list filters; vosk drops the
            # per-word scores in this mode, which interpret_recognition expects.
            rec.SetMaxAlternatives(GRAMMAR_ALTERNATIVES)
            free_rec.SetWords(True)
            logger.info("Voice control listening (model=%s, rate=%d, device=%s, alternatives=%d)",
                        self.model_path, self.sample_rate, device, GRAMMAR_ALTERNATIVES)

            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=8000,
                dtype="int16",
                channels=1,
                device=device,
                callback=_callback,
            ):
                # The free recognizer ends its utterances on its own schedule, so
                # its latest is banked here: reading it only when the two
                # happened to finish on one block threw the transcription away.
                free_json = ""
                last_block_at = time.monotonic()
                last_heartbeat = last_block_at
                stalled = False
                while not self._stop.is_set():
                    try:
                        data, captured_at = audio_q.get(timeout=0.5)
                    except _queue.Empty:
                        idle = time.monotonic() - last_block_at
                        if not stalled and idle >= AUDIO_STALL_S:
                            stalled = True
                            logger.warning(
                                "Voice control: no audio from device %s for %.0fs -- "
                                "nothing spoken can be heard until it comes back",
                                device, idle,
                            )
                        continue
                    if stalled:
                        stalled = False
                        logger.warning("Voice control: audio from device %s resumed", device)
                    last_block_at = time.monotonic()
                    level.note_block(data)
                    if last_block_at - last_heartbeat >= LISTEN_HEARTBEAT_S:
                        last_heartbeat = last_block_at
                        logger.debug("Voice control listening; loudest sample since the last "
                                     "report: %d", level.take_recent())
                    block_started_at = captured_at - (len(data) / 2) / self.sample_rate
                    grammar_final = rec.AcceptWaveform(data)
                    # Feed the free recognizer the same block, and bank whatever
                    # it finishes.
                    if free_rec.AcceptWaveform(data):
                        free_json = free_rec.Result()
                    if grammar_final:
                        interp = interpret_recognition(
                            rec.Result(),
                            free_json or free_rec.FinalResult(),
                            threshold=self.confidence_threshold,
                        )
                        free_json = ""
                        spoken_at = onset.take(fallback=block_started_at)
                        self._handle_recognition(
                            interp, spoken_at=spoken_at, peak=level.take_utterance(),
                        )
                    else:
                        onset.note_block(
                            block_started_at=block_started_at,
                            has_partial=has_partial_text(rec.PartialResult()),
                        )

            logger.info("Voice control stopped")
        except Exception:
            logger.exception("Voice control thread crashed")

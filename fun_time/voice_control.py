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
from pathlib import Path

from fun_time.voice_commands import VOICE_COMMANDS, format_spoken_command

logger = logging.getLogger(__name__)


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


def parse_vosk_result(raw_json: str, *, threshold: float) -> str | None:
    """Parse a Vosk recognizer result and return the dispatch command, or None.

    Returns None if the text is empty, unknown, "[unk]", carries no per-word
    confidences, or scores below *threshold* on average.  Quiet-room noise
    still lands on a grammar phrase — vosk's grammar restricts the vocabulary
    rather than requiring speech — but it scores far below a spoken command, so
    the threshold is the only thing standing between ambient noise and a real
    dispatch.  An unscored result cannot clear it and is rejected; the listen
    loop enables ``SetWords`` so every real recognition carries scores.
    """
    data = json.loads(raw_json)
    text = data.get("text", "").strip()
    if not text or text == "[unk]":
        return None
    command = VOICE_COMMANDS.get(text)
    if command is None:
        return None
    words = data.get("result")
    if not words:
        return None
    avg_conf = sum(w.get("conf", 0) for w in words) / len(words)
    if avg_conf < threshold:
        logger.debug("Ignored %r (confidence %.2f < %.2f)", text, avg_conf, threshold)
        return None
    return command


_VOICE_IMPORT_ERROR: str = ""
try:
    import vosk
    import sounddevice as sd
except Exception as _exc:  # optional — voice control silently unavailable
    vosk = None  # type: ignore[assignment]
    sd = None  # type: ignore[assignment]
    _VOICE_IMPORT_ERROR = str(_exc)

VOICE_AVAILABLE = vosk is not None and sd is not None


class VoiceController:
    """Listens for voice commands and writes them to the dashboard command file."""

    def __init__(
        self,
        *,
        cmd_file: Path | str,
        model_path: str,
        confidence_threshold: float = 0.7,
        device_index: int | None = None,
        sample_rate: int = 16000,
    ) -> None:
        self.cmd_file = Path(cmd_file)
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.device_index = device_index
        self.sample_rate = sample_rate
        self._stop = threading.Event()
        self._muted = threading.Event()

    @property
    def is_muted(self) -> bool:
        """Return True if voice commands are being suppressed."""
        return self._muted.is_set()

    def mute(self) -> None:
        """Suppress command output (voice still listens but discards)."""
        self._muted.set()

    def unmute(self) -> None:
        """Resume command output."""
        self._muted.clear()

    def _write_command(self, command: str, *, spoken_at: float) -> None:
        """Append a command to the dashboard command file (no-op when muted).

        The line carries *spoken_at* — when the utterance began — so the
        dispatcher can act on the video that was on screen then, not on
        whatever replaced it while the phrase was still being recognized.
        """
        if self._muted.is_set():
            return
        with self.cmd_file.open("a", encoding="utf-8") as f:
            f.write(format_spoken_command(command, spoken_at=spoken_at) + "\n")

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

        def _callback(indata, frames, time_info, status):
            if status:
                logger.debug("audio status: %s", status)
            audio_q.put((bytes(indata), time.monotonic()))

        onset = UtteranceOnset()

        try:
            model = vosk.Model(model_name=self.model_path)
            grammar = build_grammar()
            rec = vosk.KaldiRecognizer(model, self.sample_rate, grammar)
            # Grammar mode reports per-word confidences only when words are
            # enabled; without them every recognition arrives unscored and the
            # confidence threshold below can never reject anything.
            rec.SetWords(True)
            logger.info("Voice control listening (model=%s, rate=%d, device=%s)",
                        self.model_path, self.sample_rate, self.device_index)

            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=8000,
                dtype="int16",
                channels=1,
                device=self.device_index,
                callback=_callback,
            ):
                while not self._stop.is_set():
                    try:
                        data, captured_at = audio_q.get(timeout=0.5)
                    except _queue.Empty:
                        continue
                    block_started_at = captured_at - (len(data) / 2) / self.sample_rate
                    if rec.AcceptWaveform(data):
                        result = rec.Result()
                        command = parse_vosk_result(result, threshold=self.confidence_threshold)
                        spoken_at = onset.take(fallback=block_started_at)
                        if command:
                            logger.info("Voice command: %s (spoken %.2fs before recognition)",
                                        command, time.monotonic() - spoken_at)
                            self._write_command(command, spoken_at=spoken_at)
                    else:
                        onset.note_block(
                            block_started_at=block_started_at,
                            has_partial=has_partial_text(rec.PartialResult()),
                        )

            logger.info("Voice control stopped")
        except Exception:
            logger.exception("Voice control thread crashed")

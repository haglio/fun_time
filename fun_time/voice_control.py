"""Voice control module for Fun Time.

Uses Vosk (offline speech recognition) with a restricted grammar to
recognize voice commands and write them to the dashboard command file,
where the dispatch loop picks them up identically to AHK hotkey commands.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from fun_time.voice_commands import VOICE_COMMANDS

logger = logging.getLogger(__name__)


def build_grammar() -> str:
    """Build a Vosk grammar JSON string from VOICE_COMMANDS."""
    phrases = sorted(VOICE_COMMANDS.keys())
    phrases.append("[unk]")
    return json.dumps(phrases)


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

    def _write_command(self, command: str) -> None:
        """Append a command to the dashboard command file (no-op when muted)."""
        if self._muted.is_set():
            return
        with self.cmd_file.open("a", encoding="utf-8") as f:
            f.write(command + "\n")

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

        audio_q: _queue.Queue[bytes] = _queue.Queue()

        def _callback(indata, frames, time_info, status):
            if status:
                logger.debug("audio status: %s", status)
            audio_q.put(bytes(indata))

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
                        data = audio_q.get(timeout=0.5)
                    except _queue.Empty:
                        continue
                    if rec.AcceptWaveform(data):
                        result = rec.Result()
                        command = parse_vosk_result(result, threshold=self.confidence_threshold)
                        if command:
                            logger.info("Voice command: %s", command)
                            self._write_command(command)

            logger.info("Voice control stopped")
        except Exception:
            logger.exception("Voice control thread crashed")

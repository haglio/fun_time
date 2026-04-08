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

VOICE_COMMANDS: dict[str, str] = {
    "quit": "quit",
    "pause": "pause",
    "play": "play",
    "lock landscape": "landscape_lock_on",
    "lock portrait": "portrait_lock_on",
    "next landscape": "landscape_next",
    "next portrait": "portrait_next",
    "previous landscape": "landscape_prev",
    "previous portrait": "portrait_prev",
    "weird landscape": "landscape_trash",
    "weird portrait": "portrait_trash",
    "f mode on": "fmode_on",
    "f mode off": "fmode_off",
    "go now": "genau_activate",
    "enable genau": "genau_enable",
    "disable genau": "genau_disable",
    "v l c": "genau_deactivate",
    "start broker": "broker_start",
    "stop broker": "broker_stop",
    "next primary": "primary_next",
    "previous primary": "primary_prev",
    "skip": "vlc_nudge_next",
    "back": "vlc_nudge_prev",
    "slow down": "genau_speed_down",
    "speed down": "genau_speed_down",
    "speed up": "genau_speed_up",
    "amp down": "genau_amplitude_down",
    "amp up": "genau_amplitude_up",
    "center down": "genau_center_down",
    "center up": "genau_center_up",
    "cycle shape": "genau_cycle_shape",
    "genau auto": "genau_toggle_auto",
    "cruise control": "genau_toggle_cruise",
    "cruise on": "genau_cruise_on",
    "cruise off": "genau_cruise_off",
    "previous clip": "genau_prev_clip",
    "next clip": "genau_next_clip",
    "voice off": "voice_off",
}

_NUMBER_WORDS: dict[str, int] = {
    "zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "one hundred": 100,
}

_NUMERIC_PREFIXES: dict[str, str] = {
    "amp": "genau_amp",
    "center": "genau_center",
    "speed": "genau_speed",
}

for _word, _value in _NUMBER_WORDS.items():
    for _prefix, _cmd_prefix in _NUMERIC_PREFIXES.items():
        VOICE_COMMANDS[f"{_prefix} {_word}"] = f"{_cmd_prefix}_{_value}"


def build_grammar() -> str:
    """Build a Vosk grammar JSON string from VOICE_COMMANDS."""
    phrases = sorted(VOICE_COMMANDS.keys())
    phrases.append("[unk]")
    return json.dumps(phrases)


def parse_vosk_result(raw_json: str, *, threshold: float) -> str | None:
    """Parse a Vosk recognizer result and return the dispatch command, or None.

    Returns None if the text is empty, unknown, "[unk]", or if the average
    per-word confidence is below *threshold*.  When Vosk omits confidence
    data (common in grammar mode), the phrase is accepted.
    """
    data = json.loads(raw_json)
    text = data.get("text", "").strip()
    if not text or text == "[unk]":
        return None
    command = VOICE_COMMANDS.get(text)
    if command is None:
        return None
    words = data.get("result")
    if words:
        avg_conf = sum(w.get("conf", 0) for w in words) / len(words)
        if avg_conf < threshold:
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

logger = logging.getLogger(__name__)


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

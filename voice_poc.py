"""Tiny proof-of-concept: listen to the mic and print recognized phrases.

Run:  .venv/Scripts/python.exe voice_poc.py

Say any of the Fun Time commands (quit, pause, play, skip, back,
next landscape, lock portrait, etc.) and see if Vosk picks them up.
Press Ctrl+C to stop.
"""
import json
import queue
import sys

import sounddevice as sd
from vosk import KaldiRecognizer, Model

PHRASES = [
    "quit", "pause", "play",
    "lock landscape", "lock portrait",
    "next landscape", "next portrait",
    "previous landscape", "previous portrait",
    "weird landscape", "weird portrait",
    "f mode on", "f mode off",
    "enable genau", "disable genau",
    "start broker", "stop broker",
    "next primary", "previous primary",
    "skip", "back",
]

SAMPLE_RATE = 16000
BLOCK_SIZE = 8000  # 0.5s chunks

audio_q: queue.Queue[bytes] = queue.Queue()


def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"  [audio status: {status}]", file=sys.stderr)
    audio_q.put(bytes(indata))


def main():
    print("Loading Vosk model (first run downloads ~40 MB)...")
    model = Model(model_name="vosk-model-small-en-us-0.15")

    grammar = json.dumps(PHRASES + ["[unk]"])
    rec = KaldiRecognizer(model, SAMPLE_RATE, grammar)

    print(f"\nListening on default mic at {SAMPLE_RATE} Hz.")
    print(f"Grammar: {len(PHRASES)} phrases + [unk]")
    print("Speak a command — recognized text prints below. Ctrl+C to quit.\n")

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        dtype="int16",
        channels=1,
        callback=audio_callback,
    ):
        while True:
            data = audio_q.get()
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                text = result.get("text", "").strip()
                if text and text != "[unk]":
                    conf_info = ""
                    words = result.get("result", [])
                    if words:
                        avg = sum(w.get("conf", 0) for w in words) / len(words)
                        conf_info = f"  (confidence: {avg:.2f})"
                    print(f"  >> {text}{conf_info}")
            else:
                partial = json.loads(rec.PartialResult())
                p = partial.get("partial", "").strip()
                if p and p != "[unk]":
                    print(f"     ... {p}", end="\r")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDone.")

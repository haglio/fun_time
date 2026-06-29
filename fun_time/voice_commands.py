"""Voice command vocabulary for Fun Time.

The spoken-phrase → dispatch-command mapping, deliberately free of any speech
recognition runtime (vosk / sounddevice).  Lightweight consumers — the
dashboard's hotkey/voice reference and tests — import it without loading native
audio libraries.  :mod:`fun_time.voice_control` re-exports ``VOICE_COMMANDS``
and layers the Vosk grammar and recognizer on top.
"""
from __future__ import annotations

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
    # "enable genau" is a spoken synonym for "go now"; "disable genau" maps to
    # genau_deactivate (handled by the dispatch loop), which switches back to VLC.
    "enable genau": "genau_activate",
    "disable genau": "genau_deactivate",
    "v l c": "vlc_activate",
    "hybrid": "hybrid_activate",
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

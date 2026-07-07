"""Voice command vocabulary for Fun Time.

The spoken-phrase → dispatch-command mapping, deliberately free of any speech
recognition runtime (vosk / sounddevice).  Lightweight consumers — the
dashboard's hotkey/voice reference and tests — import it without loading native
audio libraries.  :mod:`fun_time.voice_control` re-exports ``VOICE_COMMANDS``
and layers the Vosk grammar and recognizer on top.
"""
from __future__ import annotations

from fun_time.filter_vocab import filter_voice_commands

VOICE_COMMANDS: dict[str, str] = {
    "quit": "quit",
    "pause": "pause",
    "play": "play",
    "lock landscape": "landscape_lock_on",
    "lock portrait": "portrait_lock_on",
    "unlock landscape": "landscape_lock_off",
    "unlock portrait": "portrait_lock_off",
    "next landscape": "landscape_next",
    "next portrait": "portrait_next",
    "previous landscape": "landscape_prev",
    "previous portrait": "portrait_prev",
    "weird landscape": "landscape_trash",
    "weird portrait": "portrait_trash",
    # Cycle a video's siblings: same subject(s)+scene doing another action, or
    # the same generation config under another seed (a different subject).
    "portrait action": "portrait_cycle_action",
    "cycle portrait action": "portrait_cycle_action",
    "portrait seed": "portrait_cycle_seed",
    "cycle portrait seed": "portrait_cycle_seed",
    "landscape action": "landscape_cycle_action",
    "cycle landscape action": "landscape_cycle_action",
    "landscape seed": "landscape_cycle_seed",
    "cycle landscape seed": "landscape_cycle_seed",
    # "…both" drives Portrait + Landscape together; the dispatch loop expands
    # each ``both_*`` into its portrait_*/landscape_* pair.  Lock = on,
    # unlock = off, mirroring the per-satellite phrases above.
    "next both": "both_next",
    "previous both": "both_prev",
    "weird both": "both_trash",
    "lock both": "both_lock_on",
    "unlock both": "both_lock_off",
    "both action": "both_cycle_action",
    "cycle both action": "both_cycle_action",
    "both seed": "both_cycle_seed",
    "cycle both seed": "both_cycle_seed",
    # Bare (no side word) satellite commands: act on whichever side — portrait
    # or landscape — was most recently addressed, by naming it above or by
    # navigating it from the keyboard. The active side is remembered until the
    # other side is touched.
    "lock": "active_lock_on",
    "unlock": "active_lock_off",
    "next": "active_next",
    "previous": "active_prev",
    "weird": "active_trash",
    "action": "active_cycle_action",
    "seed": "active_cycle_seed",
    "f mode": "fmode_toggle",
    "f mode on": "fmode_on",
    "f mode off": "fmode_off",
    # "Premiere": (re)load the Portrait/Landscape VLC playlists newest-first,
    # picking up any new files and restarting each from the top.
    "premiere": "recency_order_refresh",
    # Recognizer listens for "go now" (reliably recognized); the reference
    # displays this as "genau" via the row's voice_display override.
    "go now": "genau_activate",
    # "nau" is not in the vosk vocabulary, so the recognizer listens for the
    # sound-alikes "now now"/"now mode"; the reference displays "nau mode".
    "now now": "nau_activate",
    "now mode": "nau_activate",
    "hybrid": "hybrid_activate",
    "hybrid mode": "hybrid_activate",
    "start broker": "broker_start",
    "stop broker": "broker_stop",
    "next primary": "primary_next",
    "previous primary": "primary_prev",
    "skip": "primary_nudge_next",
    "back": "primary_nudge_prev",
    "browse": "open_file_dialog",
    "clip": "clipper_save",
    "save clip": "clipper_save",
    "record": "nau_record_down",
    "loop": "nau_record_up",
    "end loop": "nau_loop_cancel",
    "cycle version": "nau_cycle_version",
    "next version": "nau_cycle_version",
    "shorts": "nau_length_shorts",
    "full length": "nau_length_full",
    "slow down": "genau_speed_down",
    "speed down": "genau_speed_down",
    "speed up": "genau_speed_up",
    "amp down": "genau_amplitude_down",
    "amp up": "genau_amplitude_up",
    "center down": "genau_center_down",
    "center up": "genau_center_up",
    "next shape": "genau_cycle_shape",
    "previous shape": "genau_cycle_shape_prev",
    "genau auto": "genau_toggle_auto",
    "cruise control": "genau_toggle_cruise",
    "cruise on": "genau_cruise_on",
    "cruise off": "genau_cruise_off",
    "previous clip": "genau_prev_clip",
    "next clip": "genau_next_clip",
    "offset": "quarter_button",
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

# "amp fifty" -> genau_amp_50, etc.
for _word, _value in _NUMBER_WORDS.items():
    for _prefix, _cmd_prefix in _NUMERIC_PREFIXES.items():
        VOICE_COMMANDS[f"{_prefix} {_word}"] = f"{_cmd_prefix}_{_value}"

# "min amp" -> genau_amp_0, "max speed" -> genau_speed_100, etc.
_EXTREMES: dict[str, int] = {"min": 0, "max": 100}
for _label, _value in _EXTREMES.items():
    for _prefix, _cmd_prefix in _NUMERIC_PREFIXES.items():
        VOICE_COMMANDS[f"{_label} {_prefix}"] = f"{_cmd_prefix}_{_value}"

# Spoken metadata filters — "portrait beta gamma", "alpha form", "clear portrait" —
# generated from the library's action vocabulary (see fun_time.filter_vocab).  The
# guard keeps a future act from silently shadowing an existing phrase.
_filter_commands = filter_voice_commands()
_shadowed = set(_filter_commands) & set(VOICE_COMMANDS)
if _shadowed:
    raise RuntimeError(f"filter phrases collide with existing voice commands: {sorted(_shadowed)}")
VOICE_COMMANDS.update(_filter_commands)

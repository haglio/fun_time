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
    # Satellite commands (portrait/landscape/both nav, lock, weird, cycle) are
    # generated as an order-agnostic grid below the literal.
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
    # "primary next" / "next primary" are generated with the satellite grid
    # below (the primary joins the active-side feature for navigation).
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

# Satellite commands form a uniform, order-agnostic grid.  Each action works
# BARE — driving the "active side", whichever satellite was most recently
# addressed (by naming a side, or by keyboard nav) — or with a side word
# (portrait / landscape / both) in EITHER order: "portrait lock" and "lock
# portrait" are equivalent, so a speaker never has to remember which order.
# "both …" drives Portrait + Landscape together (the dispatch loop expands each
# both_* into its portrait_/landscape_ pair).  Cycle a video's siblings with
# "action" (same subject(s)+scene, another act) or "seed" (same config, another
# seed — a different subject).
_SATELLITE_ACTIONS: dict[str, str] = {
    "lock": "lock_on",
    "unlock": "lock_off",
    "next": "next",
    "previous": "prev",
    "weird": "trash",
    "action": "cycle_action",
    "seed": "cycle_seed",
}
for _act_word, _act in _SATELLITE_ACTIONS.items():
    VOICE_COMMANDS[_act_word] = f"active_{_act}"
    for _side in ("portrait", "landscape", "both"):
        _sided = f"{_side}_{_act}"
        VOICE_COMMANDS[f"{_side} {_act_word}"] = _sided
        VOICE_COMMANDS[f"{_act_word} {_side}"] = _sided

# The primary (Nau) player joins the grid for navigation ONLY — "primary next"
# / "next primary" (either order) — since it has no lock/weird/cycle.  Bare
# "next"/"previous" also reach it whenever it was the last player navigated
# (the active side resolves to the primary then).
for _nav_word, _nav in {"next": "next", "previous": "prev"}.items():
    VOICE_COMMANDS[f"primary {_nav_word}"] = f"primary_{_nav}"
    VOICE_COMMANDS[f"{_nav_word} primary"] = f"primary_{_nav}"

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

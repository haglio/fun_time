"""Voice command vocabulary and command-file line format for Fun Time.

The spoken-phrase → dispatch-command mapping, deliberately free of any speech
recognition runtime (vosk / sounddevice).  Lightweight consumers — the
dashboard's hotkey/voice reference and tests — import it without loading native
audio libraries.  :mod:`fun_time.voice_control` re-exports ``VOICE_COMMANDS``
and layers the Vosk grammar and recognizer on top.

Also here is the one-line wire format every writer of the dashboard command file
shares, since the voice controller writes it and the dispatch loop reads it and
neither may import the other.
"""
from __future__ import annotations

import time
from pathlib import Path

from fun_time.filter_vocab import filter_voice_commands

# A spoken command carries when the *utterance began*, appended after " @".  A
# phrase is only recognized once the speaker stops, by which time an
# auto-advancing player may have moved on; the dispatcher back-dates the command
# to the video that was on screen when the user started talking.  Keyboard and
# dashboard commands are instantaneous and write the bare command with no stamp.
#
# The stamp is a ``time.monotonic()`` reading, meaningful only within the
# process that produced it — the voice controller and the dispatch loop are
# threads of that same process.
_SPOKEN_AT_SEP = " @"


def format_spoken_command(command: str, *, spoken_at: float) -> str:
    """The command-file line for *command*, stamped with its utterance start."""
    return f"{command}{_SPOKEN_AT_SEP}{spoken_at:.3f}"


def append_command_line(path: Path, line: str, *, attempts: int = 5, delay_s: float = 0.005) -> bool:
    """Append *line* as its own command to the dashboard command file.

    Every mouse- and voice-driven writer shares one file that the dispatch loop
    drains ~20x/s by renaming it (see ``poll_dashboard_commands``).  A write that
    overlaps a drain hits a transient Windows sharing violation (WinError 32) —
    the same race AHK's ``QueueCommand`` retries past.  Retry briefly so a
    collision delays the command by milliseconds instead of raising: unhandled,
    the error propagates out of a Qt click slot and PyQt6 aborts the whole
    window (the "power button closed the Dash instead of quitting Fun Time" bug).
    Append, newline-terminated, so a second click within one drain window queues
    behind the first rather than clobbering it.  Returns whether it was written;
    a persistently locked file drops the line (the next click lands) over raising.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(attempts):
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return True
        except OSError:
            if attempt < attempts - 1:
                time.sleep(delay_s)
    return False


def parse_command_line(line: str) -> tuple[str, float | None]:
    """Split a command-file line into ``(command, spoken_at)``.

    ``spoken_at`` is None for an unstamped line — a hotkey or dashboard press,
    which needs no back-dating.
    """
    command, separator, stamp = line.rpartition(_SPOKEN_AT_SEP)
    if not separator:
        return line, None
    try:
        return command, float(stamp)
    except ValueError:
        return line, None


VOICE_COMMANDS: dict[str, str] = {
    "quit": "quit",
    "exit": "quit",
    "pause": "pause",
    "play": "play",
    # Synonyms for "play"/resume.  vosk has no "unpause" token but does have
    # "un", so the recognizer listens for the two-word "un pause"; the reference
    # shows it as "unpause" via the row's friendly_voice override.
    "resume": "play",
    "un pause": "play",
    # "Shuffle" reshuffles Portrait/Landscape, cancelling Premiere's newest-first
    # order (the counterpart to "premiere"); each satellite keeps its filter.
    "shuffle": "shuffle",
    # Satellite commands (portrait/landscape/both nav, lock, weird, cycle) are
    # generated as an order-agnostic grid below the literal.
    "f mode": "fmode_toggle",
    "f mode on": "fmode_on",
    "f mode off": "fmode_off",
    # "Premiere": (re)load the Portrait/Landscape satellite playlists newest-first,
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
    # Clip navigation (larkin-style clips carved from compilations). "vid" is
    # not in the vosk vocabulary, so "full video" is the reliable phrase; "full
    # vid" stays as a fallback the model uses only if it knows the word.
    "compilation": "nau_compilation",
    "full video": "nau_full_vid",
    "full vid": "nau_full_vid",
    "money shot": "nau_money_shot",
    "redacted": "nau_money_shot",
    "slow down": "genau_speed_down",
    "speed down": "genau_speed_down",
    "speed up": "genau_speed_up",
    "amp down": "genau_amplitude_down",
    "amp up": "genau_amplitude_up",
    "center down": "genau_center_down",
    "center up": "genau_center_up",
    "next shape": "genau_cycle_shape",
    "previous shape": "genau_cycle_shape_prev",
    # vosk cannot hear "genau", so this reuses the "go now" sound-alike the mode
    # phrases already rely on; the reference shows it as "genau auto".
    "go now auto": "genau_toggle_auto",
    "cruise control": "genau_toggle_cruise",
    "cruise on": "genau_cruise_on",
    "cruise off": "genau_cruise_off",
    "previous clip": "genau_prev_clip",
    "next clip": "genau_next_clip",
    "offset": "quarter_button",
    # "voice off" / "mic off" both mute voice control (there is no spoken way
    # back — a muted recognizer hears nothing; the dashboard mic button or a
    # restart re-enables it).
    "voice off": "voice_off",
    "mic off": "voice_off",
    # The primary display's sound, whichever mode owns it.  Each pair's two
    # words mean the same thing, so a speaker never has to pick between them.
    # vosk has no "unmute" token but does have "un", so the recognizer listens
    # for the two-word "un mute"; the reference shows it as "unmute" via the
    # row's voice_display override.
    "mute": "audio_mute",
    "un mute": "audio_unmute",
    "quiet": "audio_volume_down",
    "quieter": "audio_volume_down",
    "loud": "audio_volume_up",
    "louder": "audio_volume_up",
}

# The hotkeys & voice reference popup toggles from several spoken names, and
# closes from any of them prefixed with "close".  vosk has no "hotkeys" token,
# so it listens for "hot keys" (two words); the reference shows the friendly
# "hotkeys" via command_reference.friendly_voice.
for _ref_phrase in ("help", "reference", "hot keys", "voice commands"):
    VOICE_COMMANDS[_ref_phrase] = "help_reference"
    VOICE_COMMANDS[f"close {_ref_phrase}"] = "help_reference_close"

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
    # "scene" is a synonym for "action" — a scene IS an act — so it cycles the
    # subject's other acts exactly like "action", bare or sided.
    "scene": "cycle_action",
    "seed": "cycle_seed",
    # Drop any filter/premiere/loop and reshuffle back to the default browse
    # order (all clips, one per subject).
    "reset": "reset",
}
for _act_word, _act in _SATELLITE_ACTIONS.items():
    VOICE_COMMANDS[_act_word] = f"active_{_act}"
    for _side in ("portrait", "landscape", "both"):
        _sided = f"{_side}_{_act}"
        VOICE_COMMANDS[f"{_side} {_act_word}"] = _sided
        VOICE_COMMANDS[f"{_act_word} {_side}"] = _sided

# Group commands act on the current clip's GROUP rather than on the playlist,
# and join the same order-agnostic grid.  "action loop" cycles the subject's
# other acts; "seed loop" the same act under its other seeds; both are repeat-all
# over that group (a lock, by contrast, is repeat-one over a single clip).
# "lock action" filters the satellite to the current clip's action — it is
# "portrait <act>" with the act read off the clip instead of spoken.  Each
# command's own two words are order-agnostic too ("loop action" == "action
# loop"), so a speaker never has to remember which word leads.
_SATELLITE_GROUP_ACTIONS: dict[str, tuple[str, ...]] = {
    # "loop actions"/"loop seeds" are the grid's names; the singular/reversed
    # forms and "loop scene(s)" (scene == action) are kept as equivalents.
    #
    # The grid's lock scopes are aliases here: because every satellite playlist
    # runs repeat-all, "lock seed" (hold the seed, its acts vary) IS the action
    # loop and "lock type" (the seed family) IS the seed loop; "lock all" is the
    # repeat-one lock and "loop all" is the whole unfiltered browse (reset).
    "action_loop": ("action loop", "loop action", "loop actions", "loop scene", "loop scenes", "lock seed"),
    "seed_loop": ("seed loop", "loop seed", "loop seeds", "lock type"),
    # "more seeds" / "widen (the) net" widens cycle-seed's reach on demand until
    # it finds another subject doing the same act.
    "more_seeds": ("more seeds", "widen net", "widen the net"),
    # "no loop" / "loop off" ends any group loop, back to the browse.
    "no_loop": ("no loop", "loop off"),
    "lock_action": ("lock action", "action lock"),
    "lock_on": ("lock all",),
    "reset": ("loop all",),
}
for _group_act, _group_words in _SATELLITE_GROUP_ACTIONS.items():
    for _group_word in _group_words:
        VOICE_COMMANDS[_group_word] = f"active_{_group_act}"
        for _side in ("portrait", "landscape", "both"):
            _sided = f"{_side}_{_group_act}"
            VOICE_COMMANDS[f"{_side} {_group_word}"] = _sided
            VOICE_COMMANDS[f"{_group_word} {_side}"] = _sided

# The cycle axes also take an explicit verb up front — "cycle / next / change
# <axis>" — and "scene" reads as "action".  These are extra spoken forms for the
# active-side cycle command (a side word already reaches a specific satellite via
# the bare "portrait action" / "portrait seed" forms above).
_SATELLITE_CYCLE_PHRASES: dict[str, tuple[str, ...]] = {
    "active_cycle_action": (
        "cycle action", "next action", "change action",
        "cycle scene", "next scene", "change scene",
    ),
    "active_cycle_seed": ("cycle seed", "next seed", "change seed"),
}
for _cycle_cmd, _cycle_phrases in _SATELLITE_CYCLE_PHRASES.items():
    for _cycle_phrase in _cycle_phrases:
        VOICE_COMMANDS[_cycle_phrase] = _cycle_cmd

# The primary (Nau) player joins the grid for navigation ONLY — "primary next"
# / "next primary" (either order) — since it has no lock/weird/cycle.  "main" is
# a synonym for "primary".  Bare "next"/"previous" also reach it whenever it was
# the last player navigated (the active side resolves to the primary then).
for _player_word in ("primary", "main"):
    for _nav_word, _nav in {"next": "next", "previous": "prev"}.items():
        VOICE_COMMANDS[f"{_player_word} {_nav_word}"] = f"primary_{_nav}"
        VOICE_COMMANDS[f"{_nav_word} {_player_word}"] = f"primary_{_nav}"

# Mode-named navigation: a mode's name + next/previous (either order) navigates
# that mode's player.  Nau and Hybrid drive the primary (Nau owns the primary
# display in both modes); Genau steps its own clip.  vosk can't hear "nau" or
# "genau", so the recognizer reuses the mode-activation sound-alikes ("now mode",
# "go now") — the reference translates them back to the friendly mode names.
_MODE_NAV: dict[str, tuple[str, str]] = {
    # recognizer base -> (next command, previous command)
    "now mode": ("primary_next", "primary_prev"),  # displayed "nau mode"
    "hybrid": ("primary_next", "primary_prev"),
    "go now": ("genau_next_clip", "genau_prev_clip"),  # displayed "genau"
}
for _base, (_next_cmd, _prev_cmd) in _MODE_NAV.items():
    VOICE_COMMANDS[f"{_base} next"] = _next_cmd
    VOICE_COMMANDS[f"next {_base}"] = _next_cmd
    VOICE_COMMANDS[f"{_base} previous"] = _prev_cmd
    VOICE_COMMANDS[f"previous {_base}"] = _prev_cmd

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

# Nau's video speed by spoken multiplier, routed to Nau (the video the user
# sees) when Nau drives the OSR2.  Encoded as percent-of-normal so the command
# name stays integer: "half speed" -> nau_speed_50 -> 0.5x.
_NAU_SPEED_MULTIPLIERS: dict[str, int] = {
    "quarter speed": 25,
    "half speed": 50,
    "three quarter speed": 75,
    "normal speed": 100,
    "one and a half speed": 150,
    "double speed": 200,
}
for _phrase, _pct in _NAU_SPEED_MULTIPLIERS.items():
    VOICE_COMMANDS[_phrase] = f"nau_speed_{_pct}"

# The literal "speed <n> ex" form: "speed one ex" -> 1x, "speed one point five
# ex" -> 1.5x, "speed point two five ex" -> 0.25x — every 0.25 stop.
_NAU_SPEED_SPOKEN: dict[str, int] = {
    "point two five": 25, "point five": 50, "point seven five": 75,
    "one": 100, "one point two five": 125, "one point five": 150,
    "one point seven five": 175, "two": 200,
}
for _spoken, _pct in _NAU_SPEED_SPOKEN.items():
    VOICE_COMMANDS[f"speed {_spoken} ex"] = f"nau_speed_{_pct}"

# "reset speed" snaps the video back to 1x.
VOICE_COMMANDS["reset speed"] = "nau_speed_100"

# "min speed"/"max speed" drive whichever engine currently owns the OSR2 (Nau's
# video or Genau's strokes); the amp/center extremes above stay Genau-only.
VOICE_COMMANDS["min speed"] = "speed_min"
VOICE_COMMANDS["max speed"] = "speed_max"

# Spoken metadata filters — "portrait beta gamma", "alpha form", "clear portrait" —
# generated from the library's action vocabulary (see fun_time.filter_vocab).  The
# guard keeps a future act from silently shadowing an existing phrase.
_filter_commands = filter_voice_commands()
_shadowed = set(_filter_commands) & set(VOICE_COMMANDS)
if _shadowed:
    raise RuntimeError(f"filter phrases collide with existing voice commands: {sorted(_shadowed)}")
VOICE_COMMANDS.update(_filter_commands)

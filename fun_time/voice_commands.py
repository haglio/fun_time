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

from collections.abc import Mapping
from types import MappingProxyType

from fun_time.filter_vocab import filter_voice_commands

from .content import load_content

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


# A hosted Origenerator's own vocabulary, said to one of its regions.
#
# The session owns the microphone for the whole room — one mic, one
# transcription — so these are heard HERE and posted on the hosted app's
# channel as the words themselves (see command_dispatch's origenerator speech).
# It matches them with its own matcher, which is the only place that knows
# which shelves its tree has and which detail parts have detectors installed.
# So this list is the recognizer's half of the deal: vosk can only hear a
# phrase that is in its grammar, and these are the phrases.
ORIGENERATOR_PHRASES: tuple[str, ...] = (
    # The shelves, by the labels its tree wears.  Not "latest" and not "trash":
    # this session already says both to a satellite ("portrait latest" reorders
    # that player's browse, "portrait trash" discards its clip), and in
    # origenerator mode those same two commands are routed to the hosted app
    # instead — one phrase, one meaning per mode, rather than two spellings.
    "favorites", "experiments", "requests",
    "enhanced only", "filter enhanced",
    # The show's own controls.
    "play slideshow", "start slideshow", "pause slideshow", "stop slideshow",
    # The commands about the picture on screen: the built-in detail parts, and
    # the sound Fun Time settled on for Genau (no recognizer here hears it).
    "fix face", "fix hands", "fix teeth", "fix eyes", "go now",
)

def build_voice_commands(
    *,
    filter_commands: Mapping[str, str] | None = None,
    clip_jump_phrases: tuple[str, ...] | None = None,
    origenerator_phrases: tuple[str, ...] | None = None,
) -> Mapping[str, str]:
    """The spoken vocabulary, built as a read-only value.

    Every outside-the-code input (the overlay's act-filter and clip-jump
    phrases, the hosted app's own) can be handed in explicitly, so a test can
    build a second vocabulary; None loads each the way the module global does.
    The collision guards raise from in here — they are what stops a hosted-app
    or filter phrase silently shadowing a session command."""
    if origenerator_phrases is None:
        origenerator_phrases = ORIGENERATOR_PHRASES
    commands: dict[str, str] = {
        "quit": "quit",
        "exit": "quit",
        "pause": "pause",
        "play": "play",
        # Synonyms for "play"/resume.  vosk has no "unpause" token but does have
        # "un", so the recognizer listens for the two-word "un pause"; the reference
        # shows it as "unpause" via the row's friendly_voice override.
        "resume": "play",
        "un pause": "play",
        # The sensation emergency: omnipause AND send the OSR2 away.  Three
        # words is a lot to get out in the moment this is for, so the one
        # obvious single word answers too ("stop broker" is a whole phrase, so
        # it stays).  "retract" was a second such word and is now the hold
        # below: relief is the one that stops the room with it.
        "relief omni pause": "relief_omnipause",
        "stop": "relief_omnipause",
        # Stopping the device alone, at either end of its axis, and back off
        # that hold onto the stroke it took away (see fun_time.genau_hold).
        "park": "genau_park",
        "park it": "genau_park",
        "retract": "genau_retract",
        "un park": "genau_release",
        "un retract": "genau_release",
        "o s r two resume": "genau_release",
        "oh es are two resume": "genau_release",
        # Satellite commands (portrait/landscape/both nav, lock, weird, cycle) are
        # generated as an order-agnostic grid below the literal — F-mode among them,
        # bare and sided both.
        #
        # Recognizer listens for "go now" (reliably recognized); the reference
        # displays this as "genau" via the row's voice_display override.
        "go now": "genau_activate",
        # "nau" is not in the vosk vocabulary, so the recognizer listens for the
        # sound-alikes "now now"/"now mode"; the reference displays "nau mode".
        "now now": "nau_activate",
        "now mode": "nau_activate",
        "hybrid": "hybrid_activate",
        "hybrid mode": "hybrid_activate",
        # The satellite side's mode pair.  "origenerator" is not a vosk token, so
        # the recognizer listens for "generator mode"; the reference displays
        # "origenerator mode".  Spoken as the explicit pair rather than a toggle,
        # so a phrase misheard twice cannot land on the opposite of what was asked.
        "generator mode": "origenerator_activate",
        "player mode": "players_activate",
        "start broker": "broker_start",
        "stop broker": "broker_stop",
        # "main next" / "next main" are generated with the satellite grid
        # below (the main player joins the active-side feature for navigation).
        "skip": "main_nudge_next",
        "back": "main_nudge_prev",
        # FunTimeVR: walk the main player's video's projection (flat / 180 / fisheye /
        # MKX200 / 360); the pick is remembered per video in its sidecar.
        "projection": "projection_cycle",
        # FunTimeVR: re-zero the scene onto wherever the headset is facing now —
        # the in-app recenter, since the runtime's own menu doesn't reach the app.
        "recenter": "recenter_view",
    "tilt up": "tilt_up",
    "tilt down": "tilt_down",
    "level": "tilt_reset",
        "browse": "browse_library",
        "clip": "clipper_save",
        "save clip": "clipper_save",
        "record": "nau_record_down",
        "loop": "nau_record_up",
        "end loop": "nau_loop_cancel",
        # Nau's other encodes of the same video.  The bare axis word cycles it, the
        # way "action"/"seed" do on a satellite; the "cycle / next / change version"
        # verb forms come from the cycle-axis grid below.
        "version": "nau_cycle_version",
        "shorts": "nau_length_shorts",
        "full length": "nau_length_full",
        # The unfiltered library Nau opens in, and so the way back out of either
        # half.  "main reset" contains this and goes further, dropping F-mode too (see
        # the main-player grid below); this is the narrow gesture of the pair.
        "mixed": "nau_length_mixed",
        # Clip navigation (Larkin-style clips carved from compilations). "vid" is
        # not in the vosk vocabulary, so "full video" is the reliable phrase; "full
        # vid" stays as a fallback the model uses only if it knows the word.
        "compilation": "nau_compilation",
        # …and back out of one, without having to name a length: Nau returns to
        # whichever mode was feeding the playlist when it went in.
        "end compilation": "nau_end_compilation",
        "full video": "nau_full_vid",
        "full vid": "nau_full_vid",
        # Funscript navigation.  A scripted video is mostly not scripted — the action
        # comes in runs with quiet stretches between them — so one phrase skips the
        # stretch you are in and the other gives up on the video entirely for the next
        # one that has a script, landing on its action rather than at its top.  vosk
        # has no "funscript" token but has both halves, so the recognizer listens for
        # the split "fun script"/"fun scripted"; the reference joins them back up via
        # friendly_voice below.
        "jump to fun script": "nau_funscript_jump",
        "next fun scripted": "nau_next_funscripted",
        # The phrases for the clip jump are library vocabulary, so they come from
        # the content overlay and are merged in below rather than written here.
        # Nothing in these words names an engine, so they follow whichever
        # holds the OSR2: the video's rate while a funscript is driving it
        # (the script scales with the clock), else Genau's stroke.
        "slow down": "speed_down",
        "speed down": "speed_down",
        "speed up": "speed_up",
        # Naming the playback pins the same nudge to the video no matter who has
        # the OSR2 — the one thing the bare pair above cannot say; the same pair
        # the console's playback arrows send.
        "playback slow down": "nau_speed_down",
        "playback speed down": "nau_speed_down",
        "playback speed up": "nau_speed_up",
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
        # Bare "weird" already addresses the active satellite, so Genau's own clip
        # action names the clip.  There is no spoken hold to go with it: holding a
        # clip is the main player's lock, said as "main lock" or bare while the
        # main player has the floor.
        "weird clip": "genau_weird_clip",
        "offset": "quarter_button",
        # "voice off" / "mic off" both mute voice control (there is no spoken way
        # back — a muted recognizer hears nothing; the dashboard mic button or a
        # restart re-enables it).
        "voice off": "voice_off",
        "mic off": "voice_off",
        # The main player's sound, whichever mode owns it.  Each pair's two
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

    # The spoken phrases for the clip jump describe the library, not the app, so
    # they live in the content overlay (content.example.json documents the shape).
    if clip_jump_phrases is None:
        clip_jump_phrases = tuple(load_content()["clip_jump_phrases"])
    commands.update(dict.fromkeys(clip_jump_phrases, "nau_clip_jump"))

    # The hotkeys & voice reference popup toggles from several spoken names, and
    # closes from any of them prefixed with "close".  vosk has no "hotkeys" token,
    # so it listens for "hot keys" (two words); the reference shows the friendly
    # "hotkeys" via friendly_voice below.
    for _ref_phrase in ("help", "reference", "hot keys", "voice commands"):
        commands[_ref_phrase] = "help_reference"
        commands[f"close {_ref_phrase}"] = "help_reference_close"

    # Satellite commands form a uniform, order-agnostic grid.  Each action
    # works BARE — driving the "active side", whichever satellite was most
    # recently addressed — or with a side word (portrait / landscape / both)
    # in EITHER order, and "both …" drives the pair (the dispatch loop expands
    # it).  Cycle siblings with "action" (same subject(s)+scene, another act)
    # or "seed" (same config, another subject).
    _SATELLITE_ACTIONS: dict[str, str] = {
        "lock": "lock_on",
        "unlock": "lock_off",
        "next": "next",
        "previous": "prev",
        "weird": "trash",
        # The clip is fine; what its metadata says it shows is not.  Strikes the act
        # out of the sidecar, which puts the clip back in front of Evolver's backfill
        # tool to be named again — the metadata counterpart of "weird".
        "wrong action": "wrong_action",
        "action": "cycle_action",
        # "scene" is a synonym for "action" — a scene IS an act — so it cycles the
        # subject's other acts exactly like "action", bare or sided.
        "scene": "cycle_action",
        "seed": "cycle_seed",
        # Drop any filter/ordering/loop and reshuffle back to the default browse
        # order (all clips, one per subject).
        "reset": "reset",
        # The two browse orderings, each rescanning the sources so new files are picked
        # up: "latest" reloads newest-first, "shuffle" reshuffles.  Both are sided like
        # every other satellite action — a side put in latest order has to be
        # shuffleable on its own — and "both latest" is what the P key sends.
        "latest": "latest",
        "shuffle": "shuffle",
    }
    for _act_word, _act in _SATELLITE_ACTIONS.items():
        commands[_act_word] = f"active_{_act}"
        for _side in ("portrait", "landscape", "both"):
            _sided = f"{_side}_{_act}"
            commands[f"{_side} {_act_word}"] = _sided
            commands[f"{_act_word} {_side}"] = _sided

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
        # Two of the grid's lock scopes are aliases here: because every satellite
        # playlist runs repeat-all, "lock seed" (hold the seed, its acts vary) IS the
        # action loop and "lock type" (the seed family) IS the seed loop.
        #
        # The scope named "all" is NOT among them.  "lock all" and "loop all" once
        # meant the repeat-one lock and the whole unfiltered browse, reading "all" as
        # everything the clip is pinned by — but the room's other "all" means all
        # *players* ("all f mode"), and no listener can tell the two senses apart.
        # They were second spellings of what "lock" and "reset" already say, so they
        # went rather than being disambiguated.
        "action_loop": ("action loop", "loop action", "loop actions", "loop scene", "loop scenes", "lock seed"),
        "seed_loop": ("seed loop", "loop seed", "loop seeds", "lock type"),
        # "more seeds" / "widen (the) net" widens cycle-seed's reach on demand until
        # it finds another subject doing the same act.
        "more_seeds": ("more seeds", "widen net", "widen the net"),
        # "no loop" / "loop off" ends any group loop, back to the browse.  ("end loop"
        # joins them, but only sided — bare it belongs to Nau; see below.)
        "no_loop": ("no loop", "loop off"),
        # "no filter" drops just the filter, where "reset" puts the whole side back
        # to its defaults (lock, order, loop and all); "clear filter" and "show
        # everything" are the same gesture said another way, scoped like the grid.
        "no_filter": ("no filter", "filter off", "clear filter", "show everything"),
        # "filter" is the same gesture named after what it leaves behind — the side's
        # filter, the one the HUD lights and "no filter" drops — so "portrait filter"
        # and "filter portrait" say "portrait lock action", and bare it filters the
        # active side.  It does not collide with the no_filter phrases above: the
        # grammar matches whole phrases, so "filter off" stays its own command.
        "lock_action": ("lock action", "action lock", "filter"),
    }
    for _group_act, _group_words in _SATELLITE_GROUP_ACTIONS.items():
        for _group_word in _group_words:
            commands[_group_word] = f"active_{_group_act}"
            for _side in ("portrait", "landscape", "both"):
                _sided = f"{_side}_{_group_act}"
                commands[f"{_side} {_group_word}"] = _sided
                commands[f"{_group_word} {_side}"] = _sided

    # "end loop" is side-agnostic like the rest of the grid: bare, it reaches the
    # player last addressed and means that player's own kind of loop — the dispatch
    # loop resolves ``active_no_loop`` to Nau's A-B loop cancel on the main player, and to
    # a satellite's group loop on portrait/landscape.
    commands["end loop"] = "active_no_loop"
    for _side in ("portrait", "landscape", "both"):
        commands[f"{_side} end loop"] = f"{_side}_no_loop"
        commands[f"end loop {_side}"] = f"{_side}_no_loop"

    # Every cycle axis is sayable by its bare word — the satellite ones from the grid
    # above, Nau's "version" from the literal map — and each also takes an explicit
    # verb up front: "cycle / next / change <axis>".  "scene" reads as "action".  The
    # satellite axes cycle the active side here; a side word already reaches a
    # specific satellite via the bare "portrait action" / "portrait seed" forms.
    _CYCLE_AXES: dict[str, str] = {
        "action": "active_cycle_action",
        "scene": "active_cycle_action",
        "seed": "active_cycle_seed",
        "version": "nau_cycle_version",
    }
    for _axis_word, _axis_cmd in _CYCLE_AXES.items():
        for _cycle_verb in ("cycle", "next", "change"):
            commands[f"{_cycle_verb} {_axis_word}"] = _axis_cmd

    # The main (Nau) player joins the grid for navigation, its lock and reset —
    # "main next" / "next main" (either order) — since it has no weird, and its one
    # cycle axis is "version" above rather than the satellites' action/seed.  It is
    # only ever "main": "primary" was a synonym here, and is not one any more, because
    # in this room "primary" names a monitor — the primary and the secondary — and one
    # word cannot be both a screen and a player.  Bare "next"/"previous"/"lock"/
    # "unlock" also reach it whenever it was the last player addressed (the active side
    # resolves to it then).  A lock means here what it means on a satellite: hold the
    # video on screen, where unlocked its end walks the playlist.  "reset" means what
    # it means for a satellite — drop whatever is narrowing the playlist, back to the
    # default browse — which for Nau is leaving any compilation and any length filter
    # for the mixed library, and dropping F-mode with them.
    _MAIN_ACTIONS = {"next": "next", "previous": "prev",
                     "lock": "lock_on", "unlock": "lock_off",
                     # Its own command rather than a bare "length mixed" forward:
                     # F-mode is half of what narrows the main player, and that flag
                     # is the orchestrator's, not Nau's.
                     "reset": "reset",
                     # The two browse orderings, the satellites' own: "latest" reloads
                     # newest-first and "shuffle" reshuffles, each rescanning the
                     # library so a video that arrived since is picked up.  The main
                     # player had only the shuffle, so a fresh clip sat somewhere in a
                     # thousand with no way to ask for it.
                     "latest": "latest", "shuffle": "shuffle"}
    for _action_word, _action in _MAIN_ACTIONS.items():
        commands[f"main {_action_word}"] = f"main_{_action}"
        commands[f"{_action_word} main"] = f"main_{_action}"

    # F-mode, per player.  Every player has its own — it narrows a satellite to the
    # favorites and the main player to the videos that have a funscript — so each is
    # sayable by naming it, in either order like the rest of the grid: "portrait f
    # mode" and "f mode portrait" are the same command.  "both" drives the two
    # satellites (expanded into its pair by the dispatch loop), "main" the main player,
    # and "all" every player at once — the gesture the F key is.
    #
    # Bare, it reaches the player last addressed, exactly as bare "lock" and "next"
    # do.  Reading the bare phrase as the whole room instead is what made a spoken
    # "f mode" answer a room that already looked narrowed with "enabled": one player
    # had been turned off by name hours earlier, and the all-players toggle turns ON
    # unless every one of them is already on.
    #
    # ``_FMODE_PHRASES`` pairs each phrase with the per-player suffix and the
    # all-players command it means, which are spelled differently for the toggle
    # alone ("<player>_fmode" against a bare "fmode_toggle").
    _FMODE_PHRASES: dict[str, tuple[str, str]] = {
        "f mode": ("fmode", "fmode_toggle"),
        "f mode on": ("fmode_on", "fmode_on"),
        "f mode off": ("fmode_off", "fmode_off"),
    }
    for _fmode_word, (_fmode_act, _fmode_all) in _FMODE_PHRASES.items():
        commands[_fmode_word] = f"active_{_fmode_act}"
        for _side in ("portrait", "landscape", "both", "main", "all"):
            _sided = _fmode_all if _side == "all" else f"{_side}_{_fmode_act}"
            commands[f"{_side} {_fmode_word}"] = _sided
            commands[f"{_fmode_word} {_side}"] = _sided

    # Mode-named navigation: a mode's name + next/previous (either order) navigates
    # that mode's player.  Nau and Hybrid drive the main slot (Nau owns the main
    # display in both modes); Genau steps its own clip.  vosk can't hear "nau" or
    # "genau", so the recognizer reuses the mode-activation sound-alikes ("now mode",
    # "go now") — the reference translates them back to the friendly mode names.
    _MODE_NAV: dict[str, tuple[str, str]] = {
        # recognizer base -> (next command, previous command)
        "now mode": ("main_next", "main_prev"),  # displayed "nau mode"
        "hybrid": ("main_next", "main_prev"),
        "go now": ("genau_next_clip", "genau_prev_clip"),  # displayed "genau"
    }
    for _base, (_next_cmd, _prev_cmd) in _MODE_NAV.items():
        commands[f"{_base} next"] = _next_cmd
        commands[f"next {_base}"] = _next_cmd
        commands[f"{_base} previous"] = _prev_cmd
        commands[f"previous {_base}"] = _prev_cmd

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
            commands[f"{_prefix} {_word}"] = f"{_cmd_prefix}_{_value}"

    # "clip seconds five" -> genau_clip_seconds_5.  These are seconds, not a 0-100 axis,
    # so they need finer granularity than the tens-only _NUMBER_WORDS above: a spoken
    # integer 1-60, single digits and compounds ("twenty five" -> 25) alike.  Zero is
    # omitted — a nought-second interval would step the clip every frame.  Naming a
    # small number was the whole point of the interval, and its absence from the
    # grammar was why the recognizer fell back to free capture ("otto advance five").
    # The phrase says what the number means — how many seconds a clip holds the
    # screen — rather than naming the machinery that moves it on.
    _SPOKEN_ONES: dict[str, int] = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9,
    }
    _SPOKEN_TEENS: dict[str, int] = {
        "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    }
    _SPOKEN_TENS: dict[str, int] = {
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    }


    def _spoken_seconds() -> dict[str, int]:
        """Spoken integers 1-60, e.g. {"five": 5, "twenty five": 25, "sixty": 60}."""
        words = {**_SPOKEN_ONES, **_SPOKEN_TEENS}
        for _tens_word, _tens in _SPOKEN_TENS.items():
            words[_tens_word] = _tens
            if _tens < 60:
                for _ones_word, _ones in _SPOKEN_ONES.items():
                    words[f"{_tens_word} {_ones_word}"] = _tens + _ones
        return words


    for _word, _value in _spoken_seconds().items():
        commands[f"clip seconds {_word}"] = f"genau_clip_seconds_{_value}"

    # "min amp" -> genau_amp_0, "max speed" -> genau_speed_100, etc.
    _EXTREMES: dict[str, int] = {"min": 0, "max": 100}
    for _label, _value in _EXTREMES.items():
        for _prefix, _cmd_prefix in _NUMERIC_PREFIXES.items():
            commands[f"{_label} {_prefix}"] = f"{_cmd_prefix}_{_value}"

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
        commands[_phrase] = f"nau_speed_{_pct}"

    # The literal "speed <n> ex" form: "speed one ex" -> 1x, "speed one point five
    # ex" -> 1.5x, "speed point two five ex" -> 0.25x — every 0.25 stop.
    _NAU_SPEED_SPOKEN: dict[str, int] = {
        "point two five": 25, "point five": 50, "point seven five": 75,
        "one": 100, "one point two five": 125, "one point five": 150,
        "one point seven five": 175, "two": 200,
    }
    for _spoken, _pct in _NAU_SPEED_SPOKEN.items():
        commands[f"speed {_spoken} ex"] = f"nau_speed_{_pct}"

    # "reset speed" snaps the video back to 1x.
    commands["reset speed"] = "nau_speed_100"

    # "min speed"/"max speed" drive whichever engine currently owns the OSR2 (Nau's
    # video or Genau's strokes); the amp/center extremes above stay Genau-only.
    commands["min speed"] = "speed_min"
    commands["max speed"] = "speed_max"


    for _side in ("portrait", "landscape"):
        for _phrase in origenerator_phrases:
            _spoken = f"{_side} {_phrase}"
            if _spoken in commands:  # the session already says this to a player
                raise RuntimeError(f"hosted phrase collides with a session command: {_spoken}")
            commands[_spoken] = f"{_side}_say_{_phrase.replace(' ', '_')}"


    # Spoken metadata filters — "portrait beta gamma", "alpha form", "clear portrait" —
    # generated from the library's action vocabulary (see fun_time.filter_vocab).  The
    # guard keeps a future act from silently shadowing an existing phrase.
    if filter_commands is None:
        filter_commands = filter_voice_commands()
    _shadowed = set(filter_commands) & set(commands)
    if _shadowed:
        raise RuntimeError(f"filter phrases collide with existing voice commands: {sorted(_shadowed)}")
    commands.update(filter_commands)
    return MappingProxyType(commands)


VOICE_COMMANDS: Mapping[str, str] = build_voice_commands()


# Commands that flash their own outcome, so the generic "I heard you" echo must
# not stack a second toast on top.  The clip and funscript jumps report from
# Nau (where they landed, or "full video not available" / "no funscripting
# ahead"); F-mode reports from the dispatch, which alone knows whether the toggle
# turned it on (green) or off (red) — and by owning the toast there, the F key and
# the dashboard flash it too, not just voice.  The two judgements of the clip on
# screen are that same shape: only the dispatch knows whether "weird" demoted a
# favorite ("Unfavorited") or condemned an ordinary clip ("Marked weird"), and
# only it knows which act "wrong action" struck ("Action removed: Alpha") or that
# there was none to strike.  Echoing either phrase back would say neither, so
# every spelling of both is listed — any of them can be what voice hands over.
SELF_REPORTING_COMMANDS = frozenset({
    "nau_compilation",
    "nau_full_vid",
    "nau_clip_jump",
    "nau_funscript_jump",
    "nau_next_funscripted",
    *(
        f"{side}_{judgement}"
        for judgement in ("trash", "wrong_action")
        for side in ("portrait", "landscape", "active", "both")
    ),
    # Every spelling of F-mode: the dispatch flashes which way each one went, so a
    # spoken one must not stack the generic echo on top of that.
    "fmode_toggle",
    "fmode_on",
    "fmode_off",
    *(
        f"{player}_fmode{suffix}"
        for player in ("main", "portrait", "landscape", "both", "active")
        for suffix in ("", "_on", "_off")
    ),
    # Every spelling of the two browse orders, for the same reason: the dispatch
    # flashes "Latest" / "Shuffle" on the player it reordered.  Echoed as well,
    # "main latest" came back as two toasts at once — the phrase, and the outcome
    # of it — which is one more than either says.  "both latest" made three.
    *(
        f"{player}_{order}"
        for player in ("main", "portrait", "landscape", "both", "active")
        for order in ("latest", "shuffle")
    ),
})


# recognizer phrase -> what the reference and the toasts show, one pair per word
# vosk cannot hear: a mode name, a joined-up word it only has the halves of, or
# a device name it only has the letters of.  Applied in order as plain replaces,
# so "un pause" precedes "omni pause" and cannot be re-split by it, and each
# rewrite reaches the derived phrases the word sits inside ("next fun scripted").
_VOICE_DISPLAY_ALIASES: tuple[tuple[str, str], ...] = (
    ("go now", "genau"),
    ("now mode", "nau mode"),
    ("hot keys", "hotkeys"),
    ("un mute", "unmute"),
    ("un pause", "unpause"),
    ("fun script", "funscript"),
    ("omni pause", "omnipause"),
    ("un park", "unpark"),
    ("un retract", "unretract"),
    ("o s r two", "OSR2"),
    ("oh es are two", "OSR2"),
)


def friendly_voice(phrase: str) -> str:
    """Rewrite a recognizer phrase's vosk sound-alikes to the friendly names."""
    for raw, nice in _VOICE_DISPLAY_ALIASES:
        phrase = phrase.replace(raw, nice)
    return phrase

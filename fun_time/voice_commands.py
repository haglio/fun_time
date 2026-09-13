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
        # Into the headset and back out (docs/entering-vr.md).  No key and no
        # pin, so the phrase IS the way in; two spellings apiece, the model
        # having both.  A bare "exit" quit until it made "exit VR" unsayable.
        "enter vr": "enter_vr",
        "enter v r": "enter_vr",
        "exit vr": "exit_vr",
        "exit v r": "exit_vr",
        "pause": "pause",
        "play": "play",
        # Synonyms for "play"/resume.
        "resume": "play",
        "un pause": "play",
        # Three syllables the recognizer can tell apart, where a bare "pause"
        "omni pause": "pause",  # competes with every one-word phrase and loses
        "omni play": "play",
        # The sensation emergency: omnipause AND send the OSR2 away.  Three
        # words is a lot to get out in the moment this is for, so the one
        # obvious single word answers too ("stop broker" is a whole phrase, so
        # it stays).  "retract" was a second such word and is now the hold
        # below: relief is the one that stops the room with it.
        "relief omni pause": "relief_omnipause",
        "stop": "relief_omnipause",
        # Stopping the device alone, at either end of its axis, and back off
        # that hold onto the motion it took away (see fun_time.robot_hand_hold).
        "park": "robot_hand_park",
        "park it": "robot_hand_park",
        "retract": "robot_hand_retract",
        "un park": "robot_hand_release",
        "un retract": "robot_hand_release",
        "o s r two resume": "robot_hand_release",
        "oh es are two resume": "robot_hand_release",
        # Satellite commands (portrait/landscape/both nav, lock, weird, cycle) are
        # generated as an order-agnostic grid below the literal — F-mode among them,
        # bare and sided both.

        "go now": "genau_activate",
        "go now mode": "genau_activate",
        # Video mode, said of a side or of neither: the bare phrase puts the
        # main slot AND the satellites on their players, each side's own phrase
        # just that side.
        "video mode": "video_activate",
        "main video mode": "main_video_activate",
        "satellite video mode": "satellites_video_activate",
        "satellites video mode": "satellites_video_activate",
        # The satellite side's other mode, spoken as explicit modes rather than a
        # toggle, so a phrase misheard twice cannot land on the opposite.
        "aura generator mode": "origenerator_activate",
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
        "record": "main_player_record_down",
        "loop": "main_player_record_up",
        "end loop": "main_player_loop_cancel",
        # The main player's other encodes of the same video.  The bare axis word cycles it, the
        # way "action"/"seed" do on a satellite; the "cycle / next / change version"
        # verb forms come from the cycle-axis grid below.
        "version": "main_player_cycle_version",
        "shorts": "main_player_length_shorts",
        "full length": "main_player_length_full",
        # Which shape of video the main player may reach, in the headset, where
        # the rotation holds both.  "mixed" is taken by the length above, so the
        # both-shapes phrase names the shapes instead; neither shape gets no
        # phrase at all, being a browse with nothing in it.
        "vr only": "main_projection_vr",
        "flat only": "main_projection_flat",
        "two d only": "main_projection_flat",
        "flat and vr": "main_projection_both",
        "vr and flat": "main_projection_both",
        # The unfiltered library the main player opens in, and so the way back out of either
        # half.  "main reset" contains this and goes further, dropping F-mode too (see
        # the main-player grid below); this is the narrow gesture of the pair.
        "mixed": "main_player_length_mixed",
        # Clip navigation (Larkin-style clips carved from compilations); "full
        # video" is the reliable phrase, "full vid" a fallback.
        "compilation": "main_player_compilation",
        # …and back out of one, without having to name a length: the main player returns to
        # whichever mode was feeding the playlist when it went in.
        "end compilation": "main_player_end_compilation",
        "full video": "main_player_full_vid",
        "full vid": "main_player_full_vid",
        # Funscript navigation.  A scripted video is mostly not scripted — the action
        # comes in runs with quiet stretches between them — so one phrase skips the
        # stretch you are in and the other gives up on the video entirely for the next
        # one that has a script, landing on its action rather than at its top.
        "jump to fun script": "main_player_funscript_jump",
        "next fun scripted": "main_player_next_funscripted",
        # The phrases for the clip jump are library vocabulary, so they come from
        # the content overlay and are merged in below rather than written here.
        # Nothing in these words names an engine, so they follow whichever holds
        # the OSR2: the video's rate under a funscript, else Genau's motion.
        "slow down": "speed_down",
        "speed down": "speed_down",
        "speed up": "speed_up",
        "amp down": "robot_hand_amplitude_down",
        "amp up": "robot_hand_amplitude_up",
        "center down": "robot_hand_center_down",
        "center up": "robot_hand_center_up",
        "next shape": "robot_hand_cycle_shape",
        "previous shape": "robot_hand_cycle_shape_prev",
        "go now auto": "genau_toggle_auto",
        "cruise control": "robot_hand_toggle_cruise",
        "cruise on": "robot_hand_cruise_on",
        "cruise off": "robot_hand_cruise_off",
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
    commands.update(dict.fromkeys(clip_jump_phrases, "main_player_clip_jump"))

    # The hotkeys & voice reference popup toggles from several spoken names, and
    # closes from any of them prefixed with "close".
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
    # "lock action" filters the satellite to the current clip's action.  Each
    # command's own two words are order-agnostic too ("loop action" == "action
    # loop").
    _SATELLITE_GROUP_ACTIONS: dict[str, tuple[str, ...]] = {
        # "loop actions"/"loop seeds" are the grid's names; the singular and
        # reversed forms and "loop scene(s)" are kept as equivalents.  Two lock
        # scopes are aliases here, every satellite playlist running repeat-all:
        # "lock seed" IS the action loop and "lock type" IS the seed loop.
        #
        # The scope named "all" is NOT among them: the room's other "all" means
        # all *players* ("all f mode"), no listener can tell the senses apart,
        # and they were second spellings of what "lock" and "reset" already say.
        "action_loop": ("action loop", "loop action", "loop actions", "loop scene", "loop scenes", "lock seed"),
        "seed_loop": ("seed loop", "loop seed", "loop seeds", "lock type"),
        # "more seeds" / "widen (the) net" widens cycle-seed's reach on demand until
        # it finds another subject doing the same act.
        "more_seeds": ("more seeds", "widen net", "widen the net"),
        # "no loop" / "loop off" ends any group loop, back to the browse.  ("end loop"
        # joins them, but only sided — bare it belongs to the main player; see below.)
        "no_loop": ("no loop", "loop off"),
        # "no filter" drops just the filter, where "reset" puts the whole side back
        # to its defaults (lock, order, loop and all); "clear filter" and "show
        # everything" are the same gesture said another way, scoped like the grid.
        "no_filter": ("no filter", "filter off", "clear filter", "show everything"),
        # "filter" is the same gesture named after what it leaves — the side's
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
    # loop resolves ``active_no_loop`` to the main player's A-B loop cancel on the main player, and to
    # a satellite's group loop on portrait/landscape.
    commands["end loop"] = "active_no_loop"
    for _side in ("portrait", "landscape", "both"):
        commands[f"{_side} end loop"] = f"{_side}_no_loop"
        commands[f"end loop {_side}"] = f"{_side}_no_loop"

    # Every cycle axis is sayable by its bare word — the satellite ones from the grid
    # above, the main player's "version" from the literal map — and each also takes an explicit
    # verb up front: "cycle / next / change <axis>".  "scene" reads as "action".  The
    # satellite axes cycle the active side here; a side word already reaches a
    # specific satellite via the bare "portrait action" / "portrait seed" forms.
    _CYCLE_AXES: dict[str, str] = {
        "action": "active_cycle_action",
        "scene": "active_cycle_action",
        "seed": "active_cycle_seed",
        "version": "main_player_cycle_version",
    }
    for _axis_word, _axis_cmd in _CYCLE_AXES.items():
        for _cycle_verb in ("cycle", "next", "change"):
            commands[f"{_cycle_verb} {_axis_word}"] = _axis_cmd

    # The main (the main player) player joins the grid for navigation, its lock and reset —
    # "main next" / "next main" (either order) — since it has no weird, and its one
    # cycle axis is "version" above rather than the satellites' action/seed.  It is
    # only ever "main": in this room "primary" names a monitor, and one word cannot
    # be both a screen and a player.  Bare "next"/"previous"/"lock"/"unlock" also
    # reach it whenever it was the last player addressed.  Lock and reset mean what
    # they mean on a satellite — hold what is on screen, and drop whatever is
    # narrowing the browse.
    _MAIN_ACTIONS = {"next": "next", "previous": "prev",
                     "lock": "lock_on", "unlock": "lock_off",
                     # Its own command rather than a bare "length mixed" forward:
                     # F-mode is half of what narrows the main player, and that flag
                     # is the orchestrator's, not the main player's.
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

    # F-mode, per player, sayable in either order like the rest of the grid:
    # "both" drives the satellites, "main" the main player, "all" every one --
    # the F key.  Bare it reaches the player last addressed, as bare "lock" and
    # "next" do; reading it as the whole room made a spoken "f mode" answer
    # "enabled" on a room that already looked narrowed.  ``_FMODE_PHRASES``
    # pairs each phrase with its per-player suffix and all-players command.
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
    # that mode's player.  Video drives the main slot's video; Genau steps its own
    # clip.
    _MODE_NAV: dict[str, tuple[str, str]] = {
        # recognizer base -> (next command, previous command)
        "video": ("main_next", "main_prev"),
        "go now": ("genau_next_clip", "genau_prev_clip"),
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
        "amp": "robot_hand_amp",
        "center": "robot_hand_center",
        "speed": "robot_hand_speed",
    }

    # "amp fifty" -> robot_hand_amp_50, etc.
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

    # "min amp" -> robot_hand_amp_0, "max center" -> robot_hand_center_100.
    _EXTREMES: dict[str, int] = {"min": 0, "max": 100}
    for _label, _value in _EXTREMES.items():
        for _prefix in ("amp", "center"):
            commands[f"{_label} {_prefix}"] = f"{_NUMERIC_PREFIXES[_prefix]}_{_value}"

    # A video's playback rate, said bare of the player last addressed or of the
    # one named before or after it, the way every action the main player shares
    # with a satellite is said.  A set rate is spelled in percent of normal.
    _PLAYBACK_SPEEDS: dict[str, str] = {
        "playback speed up": "speed_up",
        "playback speed down": "speed_down",
        "playback slow down": "speed_down",
        "min speed": "speed_min",
        "max speed": "speed_max",
        "quarter speed": "speed_25",
        "half speed": "speed_50",
        "three quarter speed": "speed_75",
        "normal speed": "speed_100",
        "one and a half speed": "speed_150",
        "double speed": "speed_200",
        "reset speed": "speed_100",
        **{f"speed {_spoken} ex": f"speed_{_pct}" for _spoken, _pct in (
            ("point two five", 25), ("point five", 50), ("point seven five", 75),
            ("one", 100), ("one point two five", 125), ("one point five", 150),
            ("one point seven five", 175), ("two", 200))},
    }
    for _phrase, _act in _PLAYBACK_SPEEDS.items():
        commands[_phrase] = f"active_{_act}"
        for _side, _player in (("portrait", "portrait"), ("landscape", "landscape"),
                              ("both", "both"), ("main", "main_player")):
            commands[f"{_side} {_phrase}"] = f"{_player}_{_act}"
            commands[f"{_phrase} {_side}"] = f"{_player}_{_act}"


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
# not stack a second toast on top.  The clip and funscript jumps report from the main player,
# where they landed or could not; the rest report from the dispatch, which alone
# knows which way a toggle went or which act a judgement struck — and by owning
# the toast there, the keys and the buttons flash it too, not just voice.  Every
# spelling of each is listed, any of them being what voice hands over.
SELF_REPORTING_COMMANDS = frozenset({
    "main_player_compilation",
    "main_player_full_vid",
    "main_player_clip_jump",
    "main_player_funscript_jump",
    "main_player_next_funscripted",
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
# a device name it only has the letters of.  EVERY sound-alike spelling above is
# here and nowhere else, which is why no row up there explains its own.
# Applied in order as plain replaces,
# so "un pause" precedes "omni pause" and cannot be re-split by it, and each
# rewrite reaches the derived phrases the word sits inside ("next fun scripted").
_VOICE_DISPLAY_ALIASES: tuple[tuple[str, str], ...] = (
    ("go now", "genau"),
    ("aura generator", "origenerator"),
    ("hot keys", "hotkeys"),
    ("un mute", "unmute"),
    ("un pause", "unpause"),
    ("fun script", "funscript"),
    ("omni pause", "omnipause"),
    ("omni play", "omniplay"),
    ("un park", "unpark"),
    ("un retract", "unretract"),
    ("o s r two", "OSR2"),
    ("oh es are two", "OSR2"),
    # Spaced form first: after the joined rewrite it is no longer there to match.
    ("v r", "VR"),
    ("vr", "VR"),
    ("two d", "2D"),
)


def friendly_voice(phrase: str) -> str:
    """Rewrite a recognizer phrase's vosk sound-alikes to the friendly names."""
    for raw, nice in _VOICE_DISPLAY_ALIASES:
        phrase = phrase.replace(raw, nice)
    return phrase

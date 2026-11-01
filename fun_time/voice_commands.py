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

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from fun_time.filter_vocab import filter_voice_commands

from .aiming import named_first_or_last, on_the_main_funestra
from .content import Noun, genau_flick_noun, load_content
from .crown import Crown
from .origenerator_switches import KINO_MODE, ORIGENERATOR_MODE, mode_command
from .room_funestras import RoomFunestra

# A spoken command carries when the *utterance began*, appended after " @".  A
# phrase is only recognized once the speaker stops, by which time an
# auto-advancing Funestra may have moved on; the dispatcher back-dates the command
# to the video that was on screen when the user started talking.  Keyboard and
# dashboard commands are instantaneous and write the bare command with no stamp.
#
# The stamp is a ``time.monotonic()`` reading, meaningful only within the
# process that produced it — the voice controller and the dispatch loop are
# threads of that same process.
_SPOKEN_AT_SEP = " @"
_SAID_SEP = "\t"


@dataclass(frozen=True)
class CommandLine:
    command: str
    spoken_at: float | None = None
    said: str = ""


def format_spoken_command(command: str, *, spoken_at: float, said: str) -> str:
    return f"{command}{_SPOKEN_AT_SEP}{spoken_at:.3f}{_SAID_SEP}{said}"


def parse_command_line(line: str) -> CommandLine:
    stamped, _, said = line.partition(_SAID_SEP)
    command, separator, stamp = stamped.rpartition(_SPOKEN_AT_SEP)
    if not separator:
        return CommandLine(line)
    try:
        return CommandLine(command, float(stamp), said)
    except ValueError:
        return CommandLine(line)


GENAU_SAID: tuple[str, ...] = ("genau", "go now")
_SATELLITE_SIDES = tuple(funestra.label for funestra in RoomFunestra.SATELLITES)
# The words that aim a phrase at a satellite side, "satellites" naming the pair.
_SIDE_WORDS = (*_SATELLITE_SIDES, "satellites")
FAVORITES_PHRASE = "favorites"


_SAY = "_say_"


def say_command(side: str, phrase: str) -> str:
    return f"{side}{_SAY}{phrase.replace(' ', '_')}"


def said_to_a_side(command: str) -> tuple[str, str] | None:
    side, said, words = command.partition(_SAY)
    if not said or side not in _SATELLITE_SIDES or not words:
        return None
    return side, words.replace("_", " ")


def build_voice_commands(
    *,
    filter_commands: Mapping[str, str] | None = None,
    clip_jump_phrases: tuple[str, ...] | None = None,
    flick_flip_phrases: tuple[str, ...] | None = None,
    origenerator_phrases: Collection[str] = (),
    genau_flick_words: Noun | None = None,
) -> Mapping[str, str]:
    """The spoken vocabulary, built as a read-only value.

    Every outside-the-code input (the overlay's act-filter and clip-jump
    phrases, the hosted app's own) can be handed in explicitly, so a test can
    build a second vocabulary; None loads each the way the module global does.
    The filter guard raises from in here; a phrase of the hosted app's that the
    session already says to a satellite stays the session's."""
    flick = (genau_flick_words or genau_flick_noun()).one
    commands: dict[str, str] = {
        "quit": "quit",
        # Into the headset and back out (docs/entering-vr.md).  No key and no
        # pin, so the phrase IS the way in; two spellings apiece, the model
        # having both.  A bare "exit" quit until it made "exit VR" unsayable.
        "enter vr": "enter_vr",
        "enter v r": "enter_vr",
        "exit vr": "exit_vr",
        "exit v r": "exit_vr",
        "vr reset": "vr_reset",
        "v r reset": "vr_reset",
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
        # it stays).
        "relief omni pause": "relief_omnipause",
        "stop": "relief_omnipause",
        # Stopping the device alone, at either end of its axis, and back off
        # that hold.
        "park": "robot_hand_park",
        "park it": "robot_hand_park",
        "o s r two control off": "robot_hand_park",
        "oh es are two control off": "robot_hand_park",
        "o s r two control on": "robot_hand_release",
        "oh es are two control on": "robot_hand_release",
        "un park": "robot_hand_release",
        # Satellite commands (portrait/landscape/satellites nav, lock, weird, cycle)
        # are generated as an order-agnostic grid below the literal — F-mode among
        # them, bare and sided both.

        **{phrase: "genau_activate" for said in GENAU_SAID for phrase in (said, f"{said} mode")},
        # Kino mode, said of a side or of neither: the bare phrase puts the
        # main slot AND the satellites on their Funestras, each side's own phrase
        # just that side.
        "kino": "kino_activate",
        "kino mode": "kino_activate",
        # Both satellites' other mode, spoken as explicit modes rather than a
        # toggle, so a phrase misheard twice cannot land on the opposite.
        "aura generator": "satellites_origenerator_activate",
        "aura generator mode": "satellites_origenerator_activate",
        "start broker": "broker_start",
        "stop broker": "broker_stop",
        # "main next" / "next main" are generated with the satellite grid
        # below (the Main Funestra joins the active-side feature for navigation).
        "projection": "projection_cycle",
        "cycle projection": "projection_cycle",
        # FunTimeVR: re-zero the scene onto wherever the headset is facing now —
        # the in-app recenter, since the runtime's own menu doesn't reach the app.
        "recenter": "recenter_view",
    "tilt up": "tilt_up",
    "tilt down": "tilt_down",
    "level": "tilt_reset",
        **dict.fromkeys(("browse", "open browser", "open file browser", "file browser",
                         "file browse"), "browse_library"),
        "save loop": "genaumacher_save",
        f"save {flick}": "genaumacher_save",
        "record": "main_funestra_record_down",
        "loop": "main_funestra_record_up",
        # The Main Funestra's other encodes of the same video.  The bare axis word cycles it, the
        # way "action"/"seed" do on a satellite; the "cycle / next / change version"
        # verb forms come from the cycle-axis grid below.
        "version": "main_funestra_cycle_version",
        # Which lengths of video the Main Funestra's list includes, which
        # shapes, in the headset, where its library holds both, and which
        # orientations: each pair said as what it includes.  Neither length,
        # neither shape and neither orientation get no phrase, being a browse
        # with nothing in it.
        "clips only": "main_funestra_length_clips",
        "full only": "main_funestra_length_full",
        "clips and full": "main_funestra_length_mixed",
        "full and clips": "main_funestra_length_mixed",
        "vr only": "main_projection_vr",
        "flat only": "main_projection_flat",
        "two d only": "main_projection_flat",
        "flat and vr": "main_projection_both",
        "vr and flat": "main_projection_both",
        "two d and vr": "main_projection_both",
        "vr and two d": "main_projection_both",
        "portrait only": "main_orientation_portrait",
        "only portrait": "main_orientation_portrait",
        "landscape only": "main_orientation_landscape",
        "only landscape": "main_orientation_landscape",
        "portrait and landscape": "main_orientation_both",
        "landscape and portrait": "main_orientation_both",
        # Clip navigation (clips carved from compilations); "full
        # video" is the reliable phrase, "full vid" a fallback.
        "compilation": "main_funestra_compilation",
        # …and back out of one, without having to name a length: the Main Funestra returns to
        # whichever mode was feeding the playlist when it went in.
        "end compilation": "main_funestra_end_compilation",
        "full video": "main_funestra_full_vid",
        "full vid": "main_funestra_full_vid",
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
        "human inspired": "robot_hand_toggle_learned",
        "human inspired on": "robot_hand_learned_on",
        "human inspired off": "robot_hand_learned_off",
        "hi on": "robot_hand_learned_on",
        "hi off": "robot_hand_learned_off",
        f"previous {flick}": "genau_prev_flick",
        f"next {flick}": "genau_next_flick",
        # Bare "weird" already addresses the active satellite, so Genau's own
        # action names the flick.  Holding one is the Main Funestra's lock, so
        # the flick named after "lock" says that lock.
        f"weird {flick}": "genau_weird_flick",
        f"lock {flick}": "main_lock_on",
        f"unlock {flick}": "main_lock_off",
        # "voice off" / "mic off" both mute voice control (there is no spoken way
        # back — a muted recognizer hears nothing; the dashboard mic button or a
        # restart re-enables it).
        "voice off": "voice_off",
        "mic off": "voice_off",
    }

    # The spoken phrases for the clip jump describe the library, not the app, so
    # they live in the content overlay (content.example.json documents the shape).
    if clip_jump_phrases is None:
        clip_jump_phrases = tuple(load_content()["clip_jump_phrases"])
    commands.update(dict.fromkeys(clip_jump_phrases, "main_funestra_clip_jump"))
    if flick_flip_phrases is None:
        flick_flip_phrases = tuple(load_content()["flick_flip_phrases"])
    commands.update(dict.fromkeys(flick_flip_phrases, "genau_flip_ends"))
    for crown in Crown:
        commands.update(named_first_or_last(crown, "crown", crown.command))
    for _funestra_word, _command in (("main", "main_kino_activate"),
                                     ("satellite", "satellites_kino_activate"),
                                     ("satellites", "satellites_kino_activate")):
        commands.update(named_first_or_last(_funestra_word, "kino mode", _command))
    for _side in _SATELLITE_SIDES:
        for _mode_words, _mode in (("kino mode", KINO_MODE), ("aura generator mode", ORIGENERATOR_MODE)):
            commands.update(named_first_or_last(_side, _mode_words, mode_command(_side, _mode)))

    # The hotkeys & voice reference popup toggles from several spoken names, and
    # closes from any of them prefixed with "close".
    for _ref_phrase in ("help", "reference", "hot keys", "voice commands"):
        commands[_ref_phrase] = "help_reference"
        commands[f"close {_ref_phrase}"] = "help_reference_close"

    # Satellite commands form a uniform, order-agnostic grid.  Each action
    # works BARE — driving the "active side", whichever satellite was most
    # recently addressed — or with a side word in EITHER order; "satellites"
    # names the pair, whose id the dispatch loop expands; "main" names the Main
    # Funestra, under the ids its own console posts.
    def _bare_or_named(words: str, action: str) -> None:
        commands[words] = f"active_{action}"
        for side in _SIDE_WORDS:
            commands.update(named_first_or_last(side, words, f"{side}_{action}"))
        if (on_the_main := on_the_main_funestra(action)) is not None:
            commands.update(named_first_or_last("main", words, on_the_main))

    _SATELLITE_ACTIONS: dict[str, str] = {
        "lock": "lock_on",
        "unlock": "lock_off",
        "next": "next",
        "previous": "prev",
        "skip": "nudge_next",
        "back": "nudge_prev",
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
        # shuffleable on its own.
        "latest": "latest",
        "shuffle": "shuffle",
    }
    for _act_word, _act in _SATELLITE_ACTIONS.items():
        _bare_or_named(_act_word, _act)
    # "next video" is bare "next" said longer, so it reaches whatever was last
    # navigated, as "next" does; so do the others said of a video.
    for _of_a_video in ("next", "previous", "weird", "lock", "unlock"):
        commands[f"{_of_a_video} video"] = commands[_of_a_video]

    # The bare word is the Main Funestra's own version axis above.
    for _funestra_word, _command in (*((side, f"{side}_cycle_version") for side in _SIDE_WORDS),
                                     ("main", "main_funestra_cycle_version")):
        commands.update(named_first_or_last(_funestra_word, "version", _command))

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
        # all *Funestras* ("all f mode"), no listener can tell the senses apart,
        # and they were second spellings of what "lock" and "reset" already say.
        "action_loop": ("action loop", "loop action", "loop actions", "loop scene", "loop scenes", "lock seed"),
        "seed_loop": ("seed loop", "loop seed", "loop seeds", "lock type"),
        # "more seeds" / "widen (the) net" widens cycle-seed's reach on demand until
        # it finds another subject doing the same act.
        "more_seeds": ("more seeds", "widen net", "widen the net"),
        # "end loop" / "no loop" / "loop off" ends the loop, whichever kind the
        # Funestra has.
        "no_loop": ("end loop", "no loop", "loop off"),
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
            _bare_or_named(_group_word, _group_act)

    # Every cycle axis is sayable by its bare word — the satellite ones from the grid
    # above, the Main Funestra's "version" from the literal map — and each also takes an explicit
    # verb up front: "cycle / next / change <axis>".  "scene" reads as "action".  The
    # satellite axes cycle the active side here; a side word already reaches a
    # specific satellite via the bare "portrait action" / "portrait seed" forms.
    _CYCLE_AXES: dict[str, str] = {
        "action": "active_cycle_action",
        "scene": "active_cycle_action",
        "seed": "active_cycle_seed",
        "version": "main_funestra_cycle_version",
    }
    for _axis_word, _axis_cmd in _CYCLE_AXES.items():
        for _cycle_verb in ("cycle", "next", "change"):
            commands[f"{_cycle_verb} {_axis_word}"] = _axis_cmd

    # A Funestra's sound, said like the rest of the grid.  Each pair's two words
    # mean the same thing, so a speaker never has to pick between them.
    _SOUND_WORDS: dict[str, str] = {
        "mute": "mute", "un mute": "unmute",
        "quiet": "volume_down", "quieter": "volume_down",
        "loud": "volume_up", "louder": "volume_up",
    }
    for _sound_word, _change in _SOUND_WORDS.items():
        _bare_or_named(_sound_word, _change)

    # F-mode, per Funestra, sayable in either order like the rest of the grid:
    # "satellites" drives the pair, "main" the Main Funestra, "all" every one --
    # the F key.  Bare it reaches the Funestra last addressed, as bare "lock" and
    # "next" do; reading it as the whole room made a spoken "f mode" answer
    # "enabled" on a room that already looked narrowed.
    _FMODE_PHRASES: dict[str, tuple[str, str]] = {
        "f mode": ("fmode", "fmode_toggle"),
        "f mode on": ("fmode_on", "fmode_on"),
        "f mode off": ("fmode_off", "fmode_off"),
    }
    for _fmode_word, (_fmode_act, _fmode_all) in _FMODE_PHRASES.items():
        _bare_or_named(_fmode_word, _fmode_act)
        commands.update(named_first_or_last("all", _fmode_word, _fmode_all))
    # The Main Funestra's F-mode named for what it keeps, as "portrait favorites"
    # names a satellite's.
    commands["fun scripts"] = "main_fmode_on"
    commands.update(named_first_or_last("main", "fun scripts", "main_fmode_on"))
    commands.update(named_first_or_last("all", "reset", "all_reset"))

    # Which of what a Funestra plays gets through, by whether an enhanced
    # version exists -- said as the shapes are, as what it includes, of the
    # Funestra last addressed or the one named.
    _ENHANCEMENT_PHRASES: dict[str, str] = {
        "enhanced only": "enhancement_enhanced",
        "original only": "enhancement_original",
        "enhanced and original": "enhancement_both",
        "original and enhanced": "enhancement_both",
    }
    for _phrase, _act in _ENHANCEMENT_PHRASES.items():
        _bare_or_named(_phrase, _act)

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

    # "<flick> seconds five" -> genau_flick_seconds_5.  These are seconds, not a 0-100 axis,
    # so they need finer granularity than the tens-only _NUMBER_WORDS above: a spoken
    # integer 1-60, single digits and compounds ("twenty five" -> 25) alike.  Zero is
    # omitted — a nought-second interval would step the flick every frame.  Naming a
    # small number was the whole point of the interval, and its absence from the
    # grammar was why the recognizer fell back to free capture ("otto advance five").
    # The phrase says what the number means — how many seconds a flick holds the
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
        commands[f"{flick} seconds {_word}"] = f"genau_flick_seconds_{_value}"

    # "min amp" -> robot_hand_amp_0, "max center" -> robot_hand_center_100.
    _EXTREMES: dict[str, int] = {"min": 0, "max": 100}
    for _label, _value in _EXTREMES.items():
        for _prefix in ("amp", "center"):
            commands[f"{_label} {_prefix}"] = f"{_NUMERIC_PREFIXES[_prefix]}_{_value}"

    # A video's playback rate, said bare of the Funestra last addressed or of the
    # one named before or after it, the way every action the Main Funestra shares
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
        _bare_or_named(_phrase, _act)
    # Funscript navigation, said the same way.  A scripted video is mostly not
    # scripted — the action comes in runs with quiet stretches between them — so
    # one phrase skips the stretch you are in and the other gives up on the video
    # entirely for the next one that has a script, landing on its action rather
    # than at its top.
    _FUNSCRIPT_MOVES: dict[str, str] = {
        "jump to fun script": "funscript_jump",
        "next fun scripted": "next_funscripted",
    }
    for _phrase, _act in _FUNSCRIPT_MOVES.items():
        _bare_or_named(_phrase, _act)


    for _verb, _act in (("minimize", "hud_minimize"), ("collapse", "hud_minimize"),
                        ("restore", "hud_restore"), ("maximize", "hud_restore"),
                        ("expand", "hud_restore")):
        for _words in (f"{_verb} hud", f"hud {_verb}"):
            _bare_or_named(_words, _act)
        for _side in (*_SIDE_WORDS, "main"):
            commands[f"{_verb} {_side} hud"] = commands[f"{_side} {_verb} hud"]

    # A HUD sent to a corner of its picture by name -- "main hud top right", with
    # or without "move" in front -- where the Ctrl keys step it round.  "upper"
    # says the same as "top", and "bottom" the same as "lower".
    _CORNER_WORDS: dict[str, str] = {
        f"{_height} {_edge}": f"{_corner}_{_edge}"
        for _height, _corner in (("top", "upper"), ("upper", "upper"),
                                 ("lower", "lower"), ("bottom", "lower"))
        for _edge in ("left", "right")
    }
    for _corner_word, _corner in _CORNER_WORDS.items():
        for _lead in ("", "move "):
            _bare_or_named(f"{_lead}hud {_corner_word}", f"hud_{_corner}")
        for _funestra in (*_SIDE_WORDS, "main"):
            commands[f"move {_funestra} hud {_corner_word}"] = commands[f"{_funestra} hud {_corner_word}"]

    for _side in _SIDE_WORDS:
        commands.update(named_first_or_last(_side, FAVORITES_PHRASE,
                                            say_command(_side, FAVORITES_PHRASE)))
    commands[FAVORITES_PHRASE] = say_command("active", FAVORITES_PHRASE)

    # Spoken metadata filters — "portrait beta gamma", "alpha form portrait" —
    # generated from the library's action vocabulary (see fun_time.filter_vocab).  The
    # guard keeps a future act from silently shadowing an existing phrase.
    if filter_commands is None:
        filter_commands = filter_voice_commands()
    _shadowed = set(filter_commands) & set(commands)
    if _shadowed:
        raise RuntimeError(f"filter phrases collide with existing voice commands: {sorted(_shadowed)}")
    commands.update(filter_commands)
    # The hosted app's own phrases, aimed like the grid: bare, the satellite last
    # navigated; a side word before or after, that side; "satellites", the pair.
    # Said alone, "genau" is the Genau mode, so it animates a picture only with
    # a side named.
    for _phrase in origenerator_phrases:
        _aimed = {} if _phrase in GENAU_SAID else {_phrase: say_command("active", _phrase)}
        for _side in _SIDE_WORDS:
            _aimed.update(named_first_or_last(_side, _phrase, say_command(_side, _phrase)))
        for _spoken, _said in _aimed.items():
            commands.setdefault(_spoken, _said)
    return MappingProxyType(commands)


VOICE_COMMANDS: Mapping[str, str] = build_voice_commands()


# recognizer phrase -> what the reference and the notices show, one pair per word
# spelled as it is heard: a name in no dictionary as the words it sounds like, a
# joined-up word as its halves, a device name as its letters, Genau as "go now".
# EVERY such spelling above is here and nowhere else, so no row explains its own.
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
    ("hi on", "HI on"),
    ("hi off", "HI off"),
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

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from fun_time.filter_vocab import named_first_or_last

from .command_value import main_funestras_own, say_command
from .content import Noun, genau_flick_noun, load_content
from .crown import Crown
from .fun_time_funestras import FunTimeFunestra
from .hosted_contract import SAID_FOR_AN_ENHANCEMENT_LEVEL
from .origenerator_switches import KINO_MODE, ORIGENERATOR_MODE, mode_command

logger = logging.getLogger(__name__)

_SPOKEN_AT_SEP = " @"
_SAID_SEP = "\t"
_MOST_SECONDS = 60


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
RELIEF_SAID_IN_A_HURRY = "stop"
_SATELLITE_NAMES = tuple(funestra.label for funestra in FunTimeFunestra.SATELLITES)
_SATELLITE_WORDS = (*_SATELLITE_NAMES, "satellites")
FAVORITES_PHRASE = "favorites"


def _bare_or_named(commands: dict[str, str], words: str, action: str) -> None:
    commands[words] = f"active_{action}"
    for satellite in _SATELLITE_WORDS:
        commands.update(named_first_or_last(satellite, words, f"{satellite}_{action}"))
    if (on_the_main := main_funestras_own(action)) is not None:
        commands.update(named_first_or_last("main", words, on_the_main))


def _mode_and_reference_phrases(commands: dict[str, str]) -> None:
    for crown in Crown:
        commands.update(named_first_or_last(crown, "crown", crown.command))
    for _funestra_word, _command in (("main", "main_kino_activate"),
                                     ("satellite", "satellites_kino_activate"),
                                     ("satellites", "satellites_kino_activate")):
        commands.update(named_first_or_last(_funestra_word, "kino mode", _command))
    for _satellite in _SATELLITE_NAMES:
        for _mode_words, _mode in (("kino mode", KINO_MODE), ("aura generator mode", ORIGENERATOR_MODE)):
            commands.update(named_first_or_last(_satellite, _mode_words, mode_command(_satellite, _mode)))

    for _ref_phrase in ("help", "reference", "hot keys", "voice commands"):
        commands[_ref_phrase] = "help_reference"
        commands[f"close {_ref_phrase}"] = "help_reference_close"


def _satellite_action_phrases(commands: dict[str, str]) -> None:
    _SATELLITE_ACTIONS: dict[str, str] = {
        "lock": "lock_on",
        "unlock": "lock_off",
        "next": "next",
        "previous": "prev",
        "skip": "nudge_next",
        "back": "nudge_prev",
        "trash": "trash",
        "wrong action": "wrong_action",
        "action": "cycle_action",
        "scene": "cycle_action",
        "seed": "cycle_seed",
        "reset": "reset",
        "latest": "latest",
        "shuffle": "shuffle",
    }
    for _act_word, _act in _SATELLITE_ACTIONS.items():
        _bare_or_named(commands, _act_word, _act)
    for _of_a_video in ("next", "previous", "trash", "lock", "unlock"):
        commands[f"{_of_a_video} video"] = commands[_of_a_video]

    for _funestra_word, _command in (*((satellite, f"{satellite}_cycle_version") for satellite in _SATELLITE_WORDS),
                                     ("main", "main_funestra_cycle_version")):
        commands.update(named_first_or_last(_funestra_word, "version", _command))


def _group_cycle_and_mode_phrases(commands: dict[str, str]) -> None:
    _SATELLITE_GROUP_ACTIONS: dict[str, tuple[str, ...]] = {
        "action_loop": ("action loop", "loop action", "loop actions", "loop scene", "loop scenes", "lock seed"),
        "seed_loop": ("seed loop", "loop seed", "loop seeds", "lock type"),
        "more_seeds": ("more seeds", "widen net", "widen the net"),
        "no_loop": ("end loop", "no loop", "loop off"),
        "no_filter": ("no filter", "filter off", "clear filter", "show everything"),
        "lock_action": ("lock action", "action lock", "filter"),
    }
    for _group_act, _group_words in _SATELLITE_GROUP_ACTIONS.items():
        for _group_word in _group_words:
            _bare_or_named(commands, _group_word, _group_act)

    _CYCLE_AXES: dict[str, str] = {
        "action": "active_cycle_action",
        "scene": "active_cycle_action",
        "seed": "active_cycle_seed",
        "version": "main_funestra_cycle_version",
    }
    for _axis_word, _axis_cmd in _CYCLE_AXES.items():
        for _cycle_verb in ("cycle", "next", "change"):
            commands[f"{_cycle_verb} {_axis_word}"] = _axis_cmd

    _EITHER_WORD_FOR_A_SOUND_CHANGE: dict[str, tuple[str, ...]] = {
        "mute": ("mute",), "unmute": ("un mute",),
        "volume_down": ("quiet", "quieter"),
        "volume_up": ("loud", "louder"),
    }
    for _change, _either_word in _EITHER_WORD_FOR_A_SOUND_CHANGE.items():
        for _sound_word in _either_word:
            _bare_or_named(commands, _sound_word, _change)

    _FMODE_PHRASES: dict[str, tuple[str, str]] = {
        "f mode": ("fmode", "fmode_toggle"),
        "f mode on": ("fmode_on", "fmode_on"),
        "f mode off": ("fmode_off", "fmode_off"),
    }
    for _fmode_word, (_fmode_act, _fmode_all) in _FMODE_PHRASES.items():
        _bare_or_named(commands, _fmode_word, _fmode_act)
        commands.update(named_first_or_last("all", _fmode_word, _fmode_all))
    commands["fun scripts"] = "main_fmode_on"
    commands.update(named_first_or_last("main", "fun scripts", "main_fmode_on"))
    commands.update(named_first_or_last("all", "reset", "all_reset"))

    _ENHANCEMENT_PHRASES: dict[str, str] = {
        **{words: f"enhancement_{level}"
           for level, words in SAID_FOR_AN_ENHANCEMENT_LEVEL.items()},
        "original and enhanced": "enhancement_both",
    }
    for _phrase, _act in _ENHANCEMENT_PHRASES.items():
        _bare_or_named(commands, _phrase, _act)


def _number_phrases(commands: dict[str, str], flick: str) -> None:
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

    for _word, _value in _NUMBER_WORDS.items():
        for _prefix, _cmd_prefix in _NUMERIC_PREFIXES.items():
            commands[f"{_prefix} {_word}"] = f"{_cmd_prefix}_{_value}"

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
        words = {**_SPOKEN_ONES, **_SPOKEN_TEENS}
        for _tens_word, _tens in _SPOKEN_TENS.items():
            words[_tens_word] = _tens
            if _tens < _MOST_SECONDS:
                for _ones_word, _ones in _SPOKEN_ONES.items():
                    words[f"{_tens_word} {_ones_word}"] = _tens + _ones
        return words


    for _word, _value in _spoken_seconds().items():
        commands[f"{flick} seconds {_word}"] = f"genau_flick_seconds_{_value}"

    _EXTREMES: dict[str, int] = {"min": 0, "max": 100}
    for _label, _value in _EXTREMES.items():
        for _prefix in ("amp", "center"):
            commands[f"{_label} {_prefix}"] = f"{_NUMERIC_PREFIXES[_prefix]}_{_value}"


def _speed_move_and_hud_phrases(commands: dict[str, str]) -> None:
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
        _bare_or_named(commands, _phrase, _act)
    _FUNSCRIPT_MOVES: dict[str, str] = {
        "jump to fun script": "funscript_jump",
        "next fun scripted": "next_funscripted",
    }
    for _phrase, _act in _FUNSCRIPT_MOVES.items():
        _bare_or_named(commands, _phrase, _act)


    for _verb, _act in (("minimize", "hud_minimize"), ("collapse", "hud_minimize"),
                        ("restore", "hud_restore"), ("maximize", "hud_restore"),
                        ("expand", "hud_restore")):
        for _words in (f"{_verb} hud", f"hud {_verb}"):
            _bare_or_named(commands, _words, _act)
        for _satellite in (*_SATELLITE_WORDS, "main"):
            commands[f"{_verb} {_satellite} hud"] = commands[f"{_satellite} {_verb} hud"]

    _CORNER_WORDS: dict[str, str] = {
        f"{_height} {_edge}": f"{_corner}_{_edge}"
        for _height, _corner in (("top", "upper"), ("upper", "upper"),
                                 ("lower", "lower"), ("bottom", "lower"))
        for _edge in ("left", "right")
    }
    for _corner_word, _corner in _CORNER_WORDS.items():
        for _lead in ("", "move "):
            _bare_or_named(commands, f"{_lead}hud {_corner_word}", f"hud_{_corner}")
        for _funestra in (*_SATELLITE_WORDS, "main"):
            commands[f"move {_funestra} hud {_corner_word}"] = commands[f"{_funestra} hud {_corner_word}"]

def build_voice_commands(
    *,
    filter_commands: Mapping[str, str] = MappingProxyType({}),
    clip_jump_phrases: tuple[str, ...] | None = None,
    flick_flip_phrases: tuple[str, ...] | None = None,
    origenerator_phrases: Collection[str] = (),
    genau_flick_words: Noun | None = None,
) -> Mapping[str, str]:
    flick = (genau_flick_words or genau_flick_noun()).one
    commands: dict[str, str] = {
        "quit": "quit",
        "enter vr": "enter_vr",
        "enter v r": "enter_vr",
        "exit vr": "exit_vr",
        "exit v r": "exit_vr",
        "vr reset": "vr_reset",
        "v r reset": "vr_reset",
        "pause": "pause",
        "play": "play",
        "resume": "play",
        "un pause": "play",
        "omni pause": "pause",
        "omni play": "play",
        "relief omni pause": "relief_omnipause",
        RELIEF_SAID_IN_A_HURRY: "relief_omnipause",
        "park": "robot_hand_park",
        "park it": "robot_hand_park",
        "o s r two control off": "robot_hand_park",
        "oh es are two control off": "robot_hand_park",
        "o s r two control on": "robot_hand_release",
        "oh es are two control on": "robot_hand_release",
        "un park": "robot_hand_release",

        **{phrase: "genau_activate" for said in GENAU_SAID for phrase in (said, f"{said} mode")},
        "kino": "kino_activate",
        "kino mode": "kino_activate",
        "aura generator": "satellites_origenerator_activate",
        "aura generator mode": "satellites_origenerator_activate",
        "start broker": "broker_start",
        "stop broker": "broker_stop",
        "projection": "projection_cycle",
        "cycle projection": "projection_cycle",
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
        "version": "main_funestra_cycle_version",
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
        "compilation": "main_funestra_compilation",
        "end compilation": "main_funestra_end_compilation",
        "full video": "main_funestra_full_vid",
        "full vid": "main_funestra_full_vid",
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
        f"lock {flick}": "main_lock_on",
        f"unlock {flick}": "main_lock_off",
        "voice off": "voice_off",
        "mic off": "voice_off",
    }

    if clip_jump_phrases is None:
        clip_jump_phrases = tuple(load_content()["clip_jump_phrases"])
    commands.update(dict.fromkeys(clip_jump_phrases, "main_funestra_clip_jump"))
    if flick_flip_phrases is None:
        flick_flip_phrases = tuple(load_content()["flick_flip_phrases"])
    commands.update(dict.fromkeys(flick_flip_phrases, "genau_flip_ends"))
    _mode_and_reference_phrases(commands)
    _satellite_action_phrases(commands)
    _group_cycle_and_mode_phrases(commands)
    _number_phrases(commands, flick)
    _speed_move_and_hud_phrases(commands)

    for _satellite in _SATELLITE_WORDS:
        commands.update(named_first_or_last(_satellite, FAVORITES_PHRASE,
                                            say_command(_satellite, FAVORITES_PHRASE)))
    commands[FAVORITES_PHRASE] = say_command("active", FAVORITES_PHRASE)

    _shadowed = set(filter_commands) & set(commands)
    if _shadowed:
        logger.warning("These label names are already spoken commands, so saying one keeps its "
                       "command and filters nothing: %s", sorted(_shadowed))
    commands.update({phrase: command for phrase, command in filter_commands.items()
                     if phrase not in _shadowed})
    for _phrase in origenerator_phrases:
        _aimed = {} if _phrase in GENAU_SAID else {_phrase: say_command("active", _phrase)}
        for _satellite in _SATELLITE_WORDS:
            _aimed.update(named_first_or_last(_satellite, _phrase, say_command(_satellite, _phrase)))
        for _spoken, _said in _aimed.items():
            commands.setdefault(_spoken, _said)
    return MappingProxyType(commands)


VOICE_COMMANDS: Mapping[str, str] = build_voice_commands()


_AS_VOSK_HEARS_IT_AND_AS_HE_READS_IT: tuple[tuple[str, str], ...] = (
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
    ("v r", "VR"),
    ("vr", "VR"),
    ("two d", "2D"),
)


def friendly_voice(phrase: str) -> str:
    for raw, nice in _AS_VOSK_HEARS_IT_AND_AS_HE_READS_IT:
        phrase = phrase.replace(raw, nice)
    return phrase

"""Canonical reference of Fun Time hotkeys and voice commands.

The factual mappings live in ``windows_bridge_hotkeys.ahk`` (key -> command) and
:data:`fun_time.voice_control.VOICE_COMMANDS` (phrase -> command); this module
groups the commands into sections and derives each row's phrases from the
vocabulary, so the spoken column cannot drift.  A row is authored as "what it
does — how", the half after the dash being its tooltip, and a section's note is
the tooltip on its title.  A section shared by several Funestras is authored
once, a key column per Funestra, its note carrying the scoping rule and the
voice column the action alone (see :func:`_collapse_scopes`).
"""
from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass

from shared_ui.palette import (
    BG_KEYCAP,
    BG_PRIMARY,
    BG_SECONDARY,
    BLUE,
    BORDER_SUBTLE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    as_hex,
)

from fun_time.content import genau_flick_noun
from fun_time.filter_vocab import display_forms, set_commands_for_scope
from fun_time.voice_commands import (
    FAVORITES_PHRASE,
    GENAU_SAID,
    VOICE_COMMANDS,
    friendly_voice,
    say_command,
)

_DETAIL_DASH = " — "
HOTKEY_HEADER = "Hotkey"
VOICE_HEADER = "Voice command"


@dataclass(frozen=True)
class CommandRef:
    """One reference row: an action and every way to trigger it."""

    description: str
    voice: tuple[str, ...]
    commands: tuple[str, ...]
    key_columns: tuple[tuple[str, ...], ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class ReferenceGroup:
    title: str
    rows: tuple[CommandRef, ...]

    @property
    def has_keys(self) -> bool:
        return any(key for row in self.rows for column in row.key_columns for key in column)


@dataclass(frozen=True)
class ReferenceSection:
    title: str
    groups: tuple[ReferenceGroup, ...]
    key_headers: tuple[str, ...] = (HOTKEY_HEADER,)
    note: str = ""

    @property
    def rows(self) -> tuple[CommandRef, ...]:
        return tuple(row for group in self.groups for row in group.rows)

    @property
    def has_keys(self) -> bool:
        return any(group.has_keys for group in self.groups)


@dataclass(frozen=True)
class _Row:
    """Authored row data; voice phrases are derived from ``commands``."""

    description: str
    hotkeys: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    literal_voice: tuple[str, ...] = ()
    voice_display: tuple[str, ...] | None = None
    columns: tuple[tuple[str, ...], ...] | None = None


@dataclass(frozen=True)
class _Group:
    title: str
    rows: tuple[_Row, ...]


@dataclass(frozen=True)
class _Section:
    """Authored section data."""

    title: str
    groups: tuple[_Group, ...]
    key_headers: tuple[str, ...] = (HOTKEY_HEADER,)
    note: str = ""
    merge_scopes: bool = False


#: The four ways to address a satellite action.
_SCOPES = ("portrait", "landscape", "satellites", "active")

_SPOKEN_COMMANDS = frozenset(VOICE_COMMANDS.values())
_FLICK = genau_flick_noun()


def _sided(*actions: str) -> tuple[str, ...]:
    """Every scope's dispatch id for *actions*: ``"prev"`` -> all four ``_prev``;
    an id no phrase reaches (a key-only toggle) belongs in the row explicitly."""
    return tuple(
        command
        for action in actions
        for scope in _SCOPES
        if (command := f"{scope}_{action}") in _SPOKEN_COMMANDS
    )


def _every_funestra(*actions: str) -> tuple[str, ...]:
    """The satellites' scopes and the Main Funestra's own id for *actions*."""
    return _sided(*actions) + tuple(
        command for action in actions if (command := f"main_{action}") in _SPOKEN_COMMANDS)


_SATELLITE_NOTE = (
    "Alone, a phrase reaches whatever you last navigated — a side, or the Main "
    "Funestra.  Add “portrait”, “landscape”, or “satellites”, before it or after, "
    "to aim it: “portrait next”, “next satellites”."
)
_FUNESTRAS_NOTE = (
    "Alone, a phrase reaches whatever you last navigated.  Add “portrait”, "
    "“landscape”, “main”, or “satellites” (the two satellites), before it or "
    "after, to aim it: “portrait next”, “next main”."
)
_FUNESTRA_KEY_HEADERS = ("Portrait", "Landscape", "Main")


def _satellite_section(title: str, groups: tuple[_Group, ...]) -> _Section:
    return _Section(title, groups, key_headers=_FUNESTRA_KEY_HEADERS[:2],
                    note=_SATELLITE_NOTE, merge_scopes=True)


def _said_to_a_slideshow(*phrases: str) -> tuple[str, ...]:
    return tuple(say_command(side, phrase) for side in ("portrait", "landscape") for phrase in phrases)


_SPEED_VOICE = ("min speed", "max speed", "half speed", "double speed", "reset speed",
                "speed one point five ex")
_SPEED_STEPS = ("speed_min", "speed_max", "speed_25", "speed_50", "speed_75", "speed_100",
                "speed_125", "speed_150", "speed_175", "speed_200")
_SIDES_FAVORITES = tuple(f"{side} {FAVORITES_PHRASE}" for side in ("portrait", "landscape"))
_HUD_CORNERS = ("upper_left", "upper_right", "lower_left", "lower_right")
_MAIN_SOUND = ("audio_volume_down", "audio_volume_up", "audio_mute", "audio_unmute")

_SECTIONS: tuple[_Section, ...] = (
    _Section(
        "Dashboard",
        (
            _Group("General", (
                _Row("quit — close everything", ("Ctrl+Alt+Q",), ("quit",)),
                _Row("OmniPause / resume", ("Esc",), ("omnipause_toggle", "pause", "play")),
                _Row("OmniPause", ("Space",), ("enter_omnipause",), ("pause",)),
                _Row(
                    "relief OmniPause — OmniPause and retract together: everything "
                    "pauses and the OSR2 goes away from you, and it stays retracted "
                    "after you resume, until you drive it again",
                    ("Shift+Esc",),
                    ("relief_omnipause",),
                ),
                _Row(
                    "open / close this hotkeys & voice reference",
                    (),
                    ("help_reference", "help_reference_close"),
                ),
                _Row("disable voice control", (), ("voice_toggle", "voice_off")),
            )),
            _Group("Funestra", (
                _Row(
                    "F-mode all — spoken it needs the word \"all\", since bare \"f "
                    "mode\" reaches the Funestra you last navigated; each Funestra also "
                    "has its own, on its own HUD",
                    ("F",),
                    ("fmode_toggle", "fmode_on", "fmode_off"),
                ),
                _Row(
                    "reset all — every Funestra back to its defaults at once, as "
                    "each Funestra's own reset does",
                    (),
                    ("all_reset",),
                ),
            )),
            _Group("VR", (
                _Row("enter / exit VR", (), ("enter_vr", "exit_vr")),
            )),
        ),
    ),
    _Section(
        "VR",
        (
            _Group("", (
                _Row(
                    "VR reset — the Main Funestra, both satellites and the dashboard back "
                    "to their default spots and sizes in the headset, and level again",
                    (),
                    ("vr_reset",),
                ),
                _Row("recenter — the VR scene onto wherever the headset faces now", (),
                     ("recenter_view",)),
                _Row(
                    "tilt the VR screens up / down and level them again — squeezing "
                    "a trigger and moving the controller turns and tilts them too",
                    (),
                    ("tilt_up", "tilt_down", "tilt_reset"),
                ),
            )),
        ),
    ),
    _Section(
        "OSR2",
        (
            _Group("Basic", (
                _Row(
                    "control on / off — park holds the OSR2 settled home, and Genau's "
                    "picture with it; a funscript stands down for it too, and the room "
                    "plays on unheard until unpark puts the motion back to whatever it "
                    "was doing, cruise included.  Control off settles the OSR2 home and "
                    "lets go of it there; the device itself stays on",
                    (),
                    ("robot_hand_park", "robot_hand_release", "osr2_control_off",
                     "robot_hand_retract"),
                    voice_display=("park", "park it", "unpark", "OSR2 off"),
                ),
            )),
            _Group("Robot Hand", (
                _Row("amplitude up / down", ("9", "7"), ("robot_hand_amplitude_up", "robot_hand_amplitude_down")),
                _Row("set amplitude", (), (), ("min amp", "max amp", "amp 0–100")),
                _Row("center up / down", ("O", "U"), ("robot_hand_center_up", "robot_hand_center_down")),
                _Row("set center", (), (), ("min center", "max center", "center 0–100")),
                _Row(
                    "speed up / down — the motion's rate, or the video's playback "
                    "rate while a funscript is driving the OSR2 (the script scales "
                    "with it)",
                    ("L", "J"),
                    ("speed_up", "speed_down"),
                ),
                _Row("set speed", (), (), ("speed 0–100",)),
                _Row("previous waveform shape", (), ("robot_hand_cycle_shape_prev",)),
                _Row("next waveform shape", ("I",), ("robot_hand_cycle_shape",)),
                _Row("enable / disable cruise control — varies the motion", ("C",),
                     ("robot_hand_toggle_cruise", "robot_hand_cruise_on", "robot_hand_cruise_off")),
                _Row(
                    "enable / disable human-inspired motion — plays phrases from real "
                    "hand-made scripts in place of the waveform, inside the Amp and "
                    "Center bars, at the Speed bar's pace; switches cruise off",
                    (),
                    ("robot_hand_toggle_learned", "robot_hand_learned_on", "robot_hand_learned_off"),
                ),
                _Row("offset the motion a ¼ cycle", ("\\",), ("quarter_button",)),
            )),
            _Group("Broker", (
                _Row("start / stop the Broker", ("B",), ("broker_panel", "broker_start", "broker_stop")),
            )),
        ),
    ),
    _Section(
        "Funestras",
        (
            _Group("Modes", (
                _Row(
                    f"Genau mode — Genau's {_FLICK.many} on the Main Funestra, the Robot "
                    "Hand driving the OSR2",
                    commands=("genau_activate",),
                    columns=((), (), ("G",)),
                ),
                _Row(
                    "Kino mode — the library's videos on the Main Funestra, each one's "
                    "funscript driving the OSR2 where it has action and the Robot Hand "
                    "the rest; a satellite's Kino mode is the library's clips; said "
                    "alone it puts all three Funestras in Kino mode",
                    commands=("kino_activate", "main_kino_activate", "satellites_kino_activate"),
                    voice_display=("kino mode",),
                ),
                _Row(
                    "Origenerator mode — the satellites show Origenerator's slideshows "
                    "over the Funestras, with Origenerator itself over the Random Favs "
                    "Browser; X switches the satellites between Origenerator and Kino",
                    commands=("origenerator_activate", "satellites_toggle"),
                    columns=(("X",), ("X",), ()),
                ),
            )),
            _Group("Navigation", (
                _Row("previous", commands=_every_funestra("prev"),
                     columns=(("Left",), ("A",), ("M",))),
                _Row("next", commands=_every_funestra("next"),
                     columns=(("Right",), ("D",), (".",))),
                _Row(
                    "weird — a satellite unfavorites the clip, or marks it weird when "
                    "it is not a favorite; in Origenerator mode it deletes the picture "
                    "and moves on",
                    commands=_sided("trash"),
                    columns=(("Up",), ("W",), ()),
                ),
                _Row(
                    "lock / unlock — locked, what is on screen repeats; unlocked it "
                    "moves on.  A satellite in Origenerator mode favorites the locked "
                    "slide and asks for a better version of it",
                    commands=("portrait_lock", "landscape_lock", "main_lock")
                    + _every_funestra("lock_on", "lock_off"),
                    columns=(("Down",), ("S",), (",",)),
                ),
            )),
            _Group("Playlist", (
                _Row("latest — reload what it plays newest-first", commands=_every_funestra("latest")),
                _Row("shuffle — reshuffle what it plays (cancels latest; keeps F-mode and the filter)",
                     commands=_every_funestra("shuffle")),
                _Row(
                    "F-mode — a satellite plays only the favorites; the Main Funestra "
                    "only the videos that have a funscript",
                    commands=_every_funestra("fmode", "fmode_on", "fmode_off"),
                ),
                _Row(
                    "reset — back to every default: no filter, no lock, no loop, no "
                    "F-mode, normal speed, shuffled from the top; the Main Funestra's "
                    "every length and shape too (leaving any compilation)",
                    commands=_every_funestra("reset"),
                ),
            )),
            _Group("Sound", (
                _Row(
                    "volume down / up, in tenths — the Main Funestra's sound is the "
                    f"video's in Kino mode and the {_FLICK.one} music in Genau mode",
                    commands=_sided("volume_down", "volume_up") + _MAIN_SOUND[:2],
                ),
                _Row("mute / unmute", commands=_sided("mute", "unmute") + _MAIN_SOUND[2:]),
            )),
            _Group("HUD", (
                _Row(
                    "relocate the HUD — round the corners of the picture, or against a "
                    "side of the Funestra in the headset, the Main Funestra's pair "
                    "walking the ring.  Said with a corner (\"top left\", \"lower "
                    "right\"; \"move\" may lead, and \"upper\" says the same as \"top\") "
                    "it goes straight there, in the headset an upper corner hanging it "
                    "over the Funestra's top edge and a lower one under its lower edge.  "
                    "A click in a corner of the picture sends it there and opens it, "
                    "and so does a squeeze beside a side of the Funestra in the headset",
                    commands=(
                        "portrait_hud_left", "portrait_hud_right", "portrait_hud_up", "portrait_hud_down",
                        "landscape_hud_left", "landscape_hud_right", "landscape_hud_up", "landscape_hud_down",
                        "main_hud_counterclockwise", "main_hud_clockwise",
                    ) + _every_funestra(*(f"hud_{corner}" for corner in _HUD_CORNERS)),
                    voice_display=("hud top left", "hud top right", "hud lower left", "hud lower right"),
                    columns=(("Ctrl+Left", "Ctrl+Right", "Ctrl+Up", "Ctrl+Down"),
                             ("Ctrl+A", "Ctrl+D", "Ctrl+W", "Ctrl+S"),
                             ("Ctrl+M", "Ctrl+.")),
                ),
                _Row(
                    "minimize the HUD — it collapses to a square with a plus on it, "
                    "and the plus brings it back",
                    commands=_every_funestra("hud_minimize", "hud_restore"),
                ),
            )),
            _Group("Playback", (
                _Row(
                    "playback speed up / down — when the Main Funestra's rate changes, "
                    "both satellites take it",
                    commands=_sided("speed_up", "speed_down") + ("main_player_speed_up", "main_player_speed_down"),
                    voice_display=("playback speed up", "playback slow down", "playback speed down"),
                ),
                _Row(
                    "set playback speed — 0.25× to 2×; the Main Funestra's funscript follows",
                    commands=_sided(*_SPEED_STEPS) + tuple(f"main_player_{step}" for step in _SPEED_STEPS),
                    voice_display=_SPEED_VOICE,
                ),
            )),
        ),
        key_headers=_FUNESTRA_KEY_HEADERS,
        note=_FUNESTRAS_NOTE,
        merge_scopes=True,
    ),
    _Section(
        "Main Funestra",
        (
            _Group("", (
                _Row(
                    "open the browser — the library's videos in Kino mode, Genau's "
                    f"{_FLICK.many} in Genau mode; the pick plays here",
                    (),
                    ("browse_library",),
                ),
                _Row(
                    "included video types — the videos in Kino mode, "
                    f"Genau's {_FLICK.many} in Genau mode: \"flat only\" (or \"2D only\"), "
                    "\"VR only\" or \"flat and VR\"; its HUD's own pair of buttons reaches a "
                    "fourth state, neither, which holds what is on screen",
                    (),
                    ("main_projection_flat", "main_projection_vr",
                     "main_projection_both", "main_projection_none"),
                ),
            )),
            _Group("Kino", (
                _Row(
                    "loop control — hold and release to set a loop, press to end loop",
                    ("R",),
                    ("main_player_record_down", "main_player_record_up", "main_player_record_tap", "main_player_loop_cancel"),
                ),
                _Row("save clip", (";",), ("genaumacher_save",)),
                _Row("nudge back 10 seconds", ("-",), ("main_nudge_prev",), voice_display=("main back",)),
                _Row("nudge forward 10 seconds", ("+",), ("main_nudge_next",), voice_display=("main skip",)),
                _Row(
                    "length of what plays — \"mixed\" (the default, everything), "
                    "\"clips\", or \"full\"; \"mixed\" leaves any compilation with it, "
                    "and its HUD's own pair reaches a fourth state, neither, which "
                    "holds the video on screen",
                    ("T",),
                    ("main_player_toggle_length", "main_player_length_clips", "main_player_length_full",
                     "main_player_length_mixed", "main_player_length_none"),
                ),
                _Row(
                    "clip navigation — \"compilation\" plays the clip's compilation "
                    "in order and \"end compilation\" leaves it for the length mode "
                    "you were in; \"full video\" plays the full scene the clip came "
                    "from, and the clip jump goes back to the clip",
                    (),
                    ("main_player_compilation", "main_player_end_compilation", "main_player_full_vid",
                     "main_player_clip_jump"),
                ),
                _Row(
                    "funscript navigation — \"jump to funscript\" skips ahead to "
                    "where this video's scripting starts up again; \"next "
                    "funscripted\" leaves for the next scripted video in the "
                    "playlist, landing where its action begins",
                    (),
                    ("main_player_funscript_jump", "main_player_next_funscripted"),
                ),
                _Row(
                    "step through the other versions of this video — the same "
                    "content at another size or from another encode",
                    ("Shift+M", "Shift+.", "V"),
                    ("main_player_cycle_version_back", "main_player_cycle_version"),
                ),
                _Row(
                    "cycle the video's VR projection — flat screen, equirectangular, "
                    "fisheye, stereographic, equisolid, rectilinear, 360, remembered per "
                    "video; a controller's stick pushed right or left steps forward or "
                    "back through them, and while the picture wraps the view its up and "
                    "down shortens or stretches the picture",
                    ("P",),
                    ("projection_cycle", "projection_cycle_back"),
                ),
                _Row(
                    "narrow / widen the angle a wrapped VR picture spans — remembered per "
                    "video",
                    ("Alt+Left", "Alt+Right"),
                    ("projection_narrower", "projection_wider"),
                ),
            )),
            _Group("Genau", (
                _Row(f"previous Genau {_FLICK.one}", (), ("genau_prev_flick",)),
                _Row(f"next Genau {_FLICK.one}", (), ("genau_next_flick",)),
                _Row(
                    f"flip the Genau {_FLICK.one} half a loop — for a picture running "
                    "opposite the OSR2; it stays flipped, and saying it again puts it back",
                    (),
                    ("genau_flip_ends",),
                ),
                _Row(f"mark the Genau {_FLICK.one} weird — skip it, and out of rotation", ("K",),
                     ("genau_weird_flick",)),
                _Row(
                    f"seconds a {_FLICK.one} holds the screen before Genau moves on — only "
                    "while it is unlocked (the , key holds it)",
                    (),
                    ("genau_flick_seconds_down", "genau_flick_seconds_up"),
                    (f"{_FLICK.one} seconds 1–60",),
                ),
            )),
        ),
    ),
    _satellite_section(
        "Satellite Funestras",
        (
            _Group("", (
                _Row(
                    "favorites — in Kino mode the side's F-mode, only its favorites; in "
                    "Origenerator mode a slideshow of its favorites collection",
                    commands=_said_to_a_slideshow(FAVORITES_PHRASE),
                    voice_display=_SIDES_FAVORITES,
                ),
                _Row("skip / back 10 seconds — within the clip",
                     commands=_sided("nudge_next", "nudge_prev")),
                _Row(
                    "step through the other versions of this clip — in Kino mode the "
                    "upscale the library plays and the original it was made from; in "
                    "Origenerator mode an enhancement, the picture it was made from, or "
                    "the upscale Evolver made of a video",
                    commands=("portrait_cycle_version_back", "portrait_cycle_version",
                              "landscape_cycle_version_back", "landscape_cycle_version",
                              "satellites_cycle_version"),
                    columns=(("Shift+Left", "Shift+Right"), ("Shift+A", "Shift+D")),
                ),
            )),
            _Group("Filter", (
                _Row(
                    "filter by act — say the act on its own",
                    commands=set_commands_for_scope("satellites")
                    + set_commands_for_scope("portrait")
                    + set_commands_for_scope("landscape"),
                    voice_display=display_forms(),
                ),
                _Row("filter to the act on screen", commands=_sided("lock_action")),
                _Row(
                    "drop the filter — keep the order and everything else; in "
                    "Origenerator mode every narrowing at once: the favorites, the "
                    "enhanced ones and the act filter",
                    commands=_sided("no_filter"),
                ),
            )),
        ),
    ),
    _satellite_section(
        "Nav Map",
        (
            _Group("", (
                _Row(
                    "cycle action — same subject(s) and scene, another act: the next "
                    "one down the map's column",
                    commands=_sided("cycle_action"),
                ),
                _Row("cycle seed — the same scene from another seed, along the map's row",
                     commands=_sided("cycle_seed")),
                _Row(
                    "wrong action — in Kino mode: the clip's act is mislabeled; strike "
                    "it so Evolver's backfill tool asks about it again",
                    commands=_sided("wrong_action"),
                ),
                _Row(
                    "navigate the map — move the lit cell up or down its column of "
                    "acts, switching to that one",
                    commands=("portrait_nav_up", "portrait_nav_down",
                              "landscape_nav_up", "landscape_nav_down"),
                    columns=(("Shift+Up", "Shift+Down"), ("Shift+W", "Shift+S")),
                ),
            )),
            _Group("Loops", (
                _Row("more seeds — widen the seed row past exact matches, and loop it",
                     commands=_sided("more_seeds")),
                _Row("loop the actions — repeat the map's column around this one",
                     commands=_sided("action_loop")),
                _Row("loop the seeds — repeat the map's row around this one",
                     commands=_sided("seed_loop")),
                _Row(
                    "step the loop on — seeds, then actions, then off",
                    commands=("portrait_loop", "landscape_loop"),
                    columns=(("Home",), ("E",)),
                ),
                _Row("stop looping — back to browsing, keeping the filter", commands=_sided("no_loop")),
            )),
        ),
    ),
    _Section(
        "Origenerator",
        (
            _Group("", (
                _Row(
                    "play the favorites, experiments or requests — a slideshow of that "
                    "collection",
                    commands=_said_to_a_slideshow(FAVORITES_PHRASE, "experiments", "requests"),
                    voice_display=("portrait favorites", "landscape experiments", "portrait requests"),
                ),
                _Row(
                    "play, pause or stop the slideshow",
                    commands=_said_to_a_slideshow("play slideshow", "start slideshow", "pause slideshow",
                                                  "stop slideshow"),
                    voice_display=("landscape play slideshow", "portrait pause slideshow",
                                   "portrait stop slideshow"),
                ),
                _Row(
                    "fix the picture on screen — its face, hands, teeth or eyes",
                    commands=_said_to_a_slideshow("fix face", "fix hands", "fix teeth", "fix eyes"),
                    voice_display=("portrait fix teeth", "landscape fix hands"),
                ),
                _Row(
                    f"animate the picture as a Genau {_FLICK.one}",
                    commands=_said_to_a_slideshow(*GENAU_SAID),
                    voice_display=("portrait genau",),
                ),
                _Row(
                    "show only the enhanced pictures",
                    commands=_said_to_a_slideshow("enhanced only", "filter enhanced"),
                    voice_display=("landscape enhanced only", "portrait filter enhanced"),
                ),
            )),
        ),
    ),
    _Section(
        "Other",
        (
            _Group("", (
                _Row("accept the last command voice was not sure enough of", ("Enter",),
                     ("voice_accept",)),
                _Row(
                    "crown the Main Funestra or the Portrait Funestra — while the crowned "
                    "one plays a portrait video it takes most of the secondary monitor, "
                    "and the other tiles what it shows across the strip that is left",
                    (),
                    ("main_crown", "portrait_crown"),
                    voice_display=("crown main", "crown portrait"),
                ),
            )),
        ),
    ),
)


#: The words that aim a phrase at a Funestra.  "active" is not among them: it is
#: the scope you get by saying none of these.
_SCOPE_WORDS = frozenset(("portrait", "landscape", "satellites", "main"))


def _collapse_scopes(phrase: str) -> str:
    """Strip the scope word out of *phrase*: "portrait next", "next satellites"
    and "next main" all become "next"; the section's note says how to aim it."""
    return " ".join(word for word in phrase.split() if word not in _SCOPE_WORDS)


def _voice_for(
    commands: tuple[str, ...], *, fold: Callable[[str], str] | None = None
) -> tuple[str, ...]:
    """Spoken phrases for *commands* in command order (on before off, up before
    down), each command's synonyms sorted among themselves, sound-alikes shown
    under their friendly names, *fold* applied before any of that."""
    by_command: dict[str, set[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        if cmd in commands:
            shown = friendly_voice(phrase)
            if fold is not None:
                shown = fold(shown)
            if shown:
                by_command.setdefault(cmd, set()).add(shown)
    result: list[str] = []
    for cmd in commands:
        for phrase in sorted(by_command.get(cmd, ())):
            if phrase not in result:
                result.append(phrase)
    return tuple(result)


def _display_voice(row: _Row, *, merge_scopes: bool) -> tuple[str, ...]:
    """The voice column for *row* — derived from its commands unless aliased."""
    if row.voice_display is not None:
        return row.voice_display
    fold = _collapse_scopes if merge_scopes else None
    return _voice_for(row.commands, fold=fold) + row.literal_voice


def _key_columns(row: _Row, section: _Section) -> tuple[tuple[str, ...], ...]:
    """The row's keys under each of the section's key columns."""
    columns = (row.hotkeys,) if row.columns is None else row.columns
    return columns + ((),) * (len(section.key_headers) - len(columns))


def _reference_row(row: _Row, section: _Section) -> CommandRef:
    description, _, detail = row.description.partition(_DETAIL_DASH)
    return CommandRef(
        description=description,
        voice=_display_voice(row, merge_scopes=section.merge_scopes),
        commands=row.commands,
        key_columns=_key_columns(row, section),
        detail=detail,
    )


def build_reference_sections() -> tuple[ReferenceSection, ...]:
    """Build the display reference, deriving voice phrases from VOICE_COMMANDS."""
    return tuple(
        ReferenceSection(
            title=section.title,
            groups=tuple(
                ReferenceGroup(group.title, tuple(_reference_row(row, section) for row in group.rows))
                for group in section.groups
            ),
            key_headers=section.key_headers,
            note=section.note,
        )
        for section in _SECTIONS
    )


def _tooltips() -> tuple[str, ...]:
    """Every tooltip in the order the page carries them: a section's note on its
    title, then each of its rows' details."""
    return tuple(
        text
        for section in build_reference_sections()
        for text in (section.note, *(row.detail for row in section.rows))
        if text
    )


def reference_details() -> dict[str, str]:
    """Each tooltip, keyed by the anchor its title or action is wrapped in."""
    return {f"detail:{index}": text for index, text in enumerate(_tooltips())}


# --- HTML rendering ---------------------------------------------------------
_BG = as_hex(BG_PRIMARY)
_HEADER_BG = as_hex(BG_SECONDARY)
_TEXT = as_hex(TEXT_PRIMARY)
_MUTED = as_hex(TEXT_MUTED)
_ACCENT = as_hex(BLUE)
_KEYCAP_BG = as_hex(BG_KEYCAP)
_BORDER = as_hex(BORDER_SUBTLE)


def _keycap(label: str) -> str:
    return (
        f'<span style="background:{_KEYCAP_BG};border:1px solid {_BORDER};'
        f'padding:1px 5px;color:{_TEXT};font-family:Consolas,monospace">'
        f"{html.escape(label)}</span>"
    )


def _cell(content: str, *, span: str = "") -> str:
    return f'<td valign="top"{span} style="padding:3px 8px;color:{_TEXT}">{content}</td>'


def _head(label: str, *, span: str = "") -> str:
    return _cell(f'<b style="color:{_MUTED}">{html.escape(label)}</b>', span=span)


def _anchored(text: str, anchor: str, *, color: str) -> str:
    return f'<a href="{anchor}" style="color:{color};text-decoration:none">{html.escape(text)}</a>'


def _header_rows(section: ReferenceSection, group: ReferenceGroup) -> str:
    """One head row, or two where "Hotkey" spans a column per Funestra; no Hotkey over a keyless group."""
    row = f'<tr style="background:{_HEADER_BG}">'
    if not group.has_keys:
        return row + _head("Action") + _head(VOICE_HEADER) + "</tr>"
    columns = len(section.key_headers)
    if columns == 1:
        return row + _head("Action") + _head(HOTKEY_HEADER) + _head(VOICE_HEADER) + "</tr>"
    return (
        row + _head("Action", span=' rowspan="2"')
        + _head(HOTKEY_HEADER, span=f' colspan="{columns}" align="center"')
        + _head(VOICE_HEADER, span=' rowspan="2"') + "</tr>"
        + row + "".join(_head(header) for header in section.key_headers) + "</tr>"
    )


def _key_cells(row: CommandRef) -> str:
    return "".join(
        _cell(" ".join(_keycap(key) for key in column) or f'<span style="color:{_MUTED}">—</span>')
        for column in row.key_columns
    )


def render_reference_html() -> str:
    """The reference as HTML for QTextBrowser: inline styles only, since Qt's
    rich text has no ``<style>`` block, and no heading, since the window chrome
    carries the popup's title."""
    parts: list[str] = [
        f'<body style="background:{_BG};color:{_TEXT};'
        'font-family:\'Segoe UI\',sans-serif;font-size:10pt">',
    ]
    anchors = iter(reference_details())
    for section in build_reference_sections():
        title = (_anchored(section.title, next(anchors), color=_ACCENT) if section.note
                 else html.escape(section.title))
        parts.append(f'<h3 style="color:{_ACCENT};margin:14px 0 2px 0">{title}</h3>')
        for group in section.groups:
            if group.title:
                parts.append(
                    f'<h4 style="color:{_TEXT};margin:10px 0 2px 0">{html.escape(group.title)}</h4>'
                )
            parts.append('<table cellspacing="0" cellpadding="0" style="width:100%">')
            parts.append(_header_rows(section, group))
            for row in group.rows:
                action = (_anchored(row.description, next(anchors), color=_TEXT) if row.detail
                          else html.escape(row.description))
                keys = _key_cells(row) if group.has_keys else ""
                phrases = (
                    ", ".join(f'“{html.escape(p)}”' for p in row.voice)
                    or f'<span style="color:{_MUTED}">—</span>'
                )
                parts.append("<tr>" + _cell(action) + keys + _cell(phrases) + "</tr>")
            parts.append("</table>")
    parts.append("</body>")
    return "".join(parts)

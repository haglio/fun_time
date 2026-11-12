from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from types import MappingProxyType

from shared_ui.reference import (
    HOTKEY_HEADER,
    CommandRef,
    ReferenceGroup,
    ReferenceSection,
)
from shared_ui.reference import reference_details as _details_of
from shared_ui.reference import render_reference_html as _render

from fun_time.command_value import say_command
from fun_time.content import Noun, genau_flick_noun
from fun_time.filter_vocab import Acts, display_forms, set_commands_for_scope
from fun_time.voice_commands import FAVORITES_PHRASE, GENAU_SAID, VOICE_COMMANDS, friendly_voice

_DETAIL_DASH = " — "


def _with_the_trigger_held(moved: str) -> str:
    return f"hold trigger + {moved}"


STICK_SIDEWAYS = "stick left / right"
STICK_UP_DOWN = "stick forward / back"
A_OR_X = "A / X"
B_OR_Y = "B / Y"
ANGLE_BAR = "Angle bar"
HOLD_TRIGGER_AND_MOVE = _with_the_trigger_held("move")
HOLD_TRIGGER_AND_STICK_UP_DOWN = _with_the_trigger_held(STICK_UP_DOWN)
HOLD_TRIGGER_AND_A_OR_X = _with_the_trigger_held(A_OR_X)
HOLD_TRIGGER_AND_B_OR_Y = _with_the_trigger_held(B_OR_Y)

@dataclass(frozen=True)
class _Row:
    description: str
    hotkeys: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    literal_voice: tuple[str, ...] = ()
    voice_display: tuple[str, ...] | None = None
    columns: tuple[tuple[str, ...], ...] | None = None
    controller: tuple[str, ...] = ()


_FILTER_BY_ACTION = _Row("filter by action — say the action on its own")


def _named_actions(row: _Row, actions: Acts) -> _Row | None:
    if row is not _FILTER_BY_ACTION:
        return row
    if not actions:
        return None
    return replace(row, commands=tuple(command for scope in ("satellites", "portrait", "landscape")
                                       for command in set_commands_for_scope(scope, actions)),
                   voice_display=display_forms(actions))


@dataclass(frozen=True)
class _Group:
    title: str
    rows: tuple[_Row, ...]


@dataclass(frozen=True)
class _Section:
    title: str
    groups: tuple[_Group, ...]
    key_headers: tuple[str, ...] = (HOTKEY_HEADER,)
    note: str = ""
    merge_scopes: bool = False


_SCOPES = ("portrait", "landscape", "satellites", "active")

_SPOKEN_COMMANDS = frozenset(VOICE_COMMANDS.values())
_FLICK = genau_flick_noun()


def _sided(*actions: str) -> tuple[str, ...]:
    return tuple(
        command
        for action in actions
        for scope in _SCOPES
        if (command := f"{scope}_{action}") in _SPOKEN_COMMANDS
    )


def _every_funestra(*actions: str) -> tuple[str, ...]:
    return _sided(*actions) + tuple(
        command for action in actions if (command := f"main_{action}") in _SPOKEN_COMMANDS)


_FUNESTRAS_NOTE = (
    "Alone, a phrase reaches whatever you last navigated.  Add “portrait”, "
    "“landscape”, “main”, or “satellites”, before it or after, to aim it, for example "
    f"“portrait next” or “next main”.  In Genau mode say “{_FLICK.one}” in place of “video”."
)
_ORIGENERATOR_NOTE = (
    "Alone, a phrase reaches the satellite you last navigated.  Add “portrait”, "
    "“landscape”, or “satellites”, before it or after, to aim it, for example "
    "“portrait fix hands” or “fix face landscape”."
)
_FUNESTRA_KEY_HEADERS = ("Portrait", "Landscape", "Main")


def _said_to_a_slideshow(description: str, *phrases: str) -> _Row:
    return _Row(description,
                literal_voice=tuple(dict.fromkeys(friendly_voice(phrase) for phrase in phrases)))


_SPEED_VOICE = ("min speed", "max speed", "half speed", "double speed", "reset speed",
                "speed one point five ex")
_SPEED_STEPS = ("speed_min", "speed_max", "speed_25", "speed_50", "speed_75", "speed_100",
                "speed_125", "speed_150", "speed_175", "speed_200")
_HUD_CORNERS = ("upper_left", "upper_right", "lower_left", "lower_right")
_HUD_MOVE_KEYS = {
    "left": ("Ctrl+Left", "Ctrl+A", "Ctrl+M"),
    "right": ("Ctrl+Right", "Ctrl+D", "Ctrl+."),
    "up": ("Ctrl+Up", "Ctrl+W", "Ctrl+K"),
    "down": ("Ctrl+Down", "Ctrl+S", "Ctrl+,"),
}
_MAIN_AUDIO = ("audio_volume_down", "audio_volume_up", "audio_mute", "audio_unmute")

def flip_row(flick: Noun) -> _Row:
    return _Row(
        f"flip the Genau {flick.one} half a loop — for a video running "
        "opposite the OSR2; it stays flipped, and saying it again puts it back",
        (),
        ("genau_flip_ends",),
    )


_SECTIONS: tuple[_Section, ...] = (
    _Section(
        "Dashboard",
        (
            _Group("", (
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
            )),
            _Group("Funestras", (
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
            _Group("Voice control", (
                _Row("disable voice control", (), ("voice_toggle", "voice_off")),
                _Row("accept the last command voice was not sure enough of", ("Enter",),
                     ("voice_accept",)),
            )),
        ),
    ),
    _Section(
        "VR",
        (
            _Group("", (
                _Row("enter / exit VR", (), ("enter_vr", "exit_vr")),
                _Row(
                    "VR reset — the Main Funestra, both satellites, and the dashboard back "
                    "to their default spots and sizes in the headset, and level again",
                    (),
                    ("vr_reset",),
                ),
                _Row("recenter — the VR scene onto wherever the headset faces now", (),
                     ("recenter_view",)),
                _Row(
                    "tilt the VR screens up / down and level them again — holding "
                    "a trigger and moving the controller turns and tilts them too",
                    (),
                    ("tilt_up", "tilt_down", "tilt_reset"),
                    controller=(HOLD_TRIGGER_AND_MOVE,),
                ),
                _Row(
                    "cycle the video's VR projection",
                    (),
                    ("projection_cycle", "projection_cycle_back"),
                    controller=(STICK_SIDEWAYS,),
                ),
                _Row(
                    "narrow / widen the angle a wrapped VR video spans — the bar "
                    "labeled Angle beside the projection controls in the headset, "
                    "dragged with the trigger",
                    controller=(ANGLE_BAR,),
                ),
                _Row(
                    "move or resize a VR screen — a squeeze on the handle over a screen "
                    "carries it, and on the handles outside a screen's lower corners "
                    "resizes it; the stick pushed forward or pulled back sizes the Main "
                    "Funestra, and with the trigger held brings every screen nearer or "
                    "farther",
                    controller=(STICK_UP_DOWN, HOLD_TRIGGER_AND_STICK_UP_DOWN),
                ),
            )),
        ),
    ),
    _Section(
        "OSR2",
        (
            _Group("", (
                _Row(
                    "control on / off — park holds the OSR2 settled home, and Genau's "
                    "video with it; a funscript stands down for it too, and Fun Time "
                    "plays on unheard until unpark puts the motion back to whatever it "
                    "was doing.  Control off settles the OSR2 home and "
                    "lets go of it there; the device itself stays on",
                    (),
                    ("robot_hand_park", "robot_hand_release"),
                ),
            )),
            _Group("Robot Hand", (
                _Row("amplitude up / down", ("9", "7"), ("robot_hand_amplitude_up", "robot_hand_amplitude_down")),
                _Row("set amplitude", (), (), ("min amp", "max amp", "amp 0\u2013100")),
                _Row("center up / down", ("O", "U"), ("robot_hand_center_up", "robot_hand_center_down")),
                _Row("set center", (), (), ("min center", "max center", "center 0\u2013100")),
                _Row(
                    "speed up / down — the motion's rate, or the video's playback "
                    "rate while a funscript is driving the OSR2 (the script scales "
                    "with it)",
                    ("L", "J"),
                    ("speed_up", "speed_down"),
                ),
                _Row("set speed", (), (), ("speed 0\u2013100",)),
                _Row("previous waveform shape", (), ("robot_hand_cycle_shape_prev",)),
                _Row("next waveform shape", ("I",), ("robot_hand_cycle_shape",)),
                _Row(
                    "enable / disable human-inspired motion — cruise control drawing "
                    "from phrases extracted from funscripts for live-action scenes",
                    (),
                    ("robot_hand_toggle_learned", "robot_hand_learned_on", "robot_hand_learned_off"),
                ),
            )),
            _Group("Broker", (
                _Row("start / stop the Broker", ("B",), ("broker_panel", "broker_start", "broker_stop")),
            )),
        ),
    ),
    _Section(
        "Funestras",
        (
            _Group("", (
                _Row("previous", commands=(*_every_funestra("prev"), "genau_prev_flick"),
                     columns=(("Left",), ("A",), ("M",))),
                _Row("next", commands=(*_every_funestra("next"), "genau_next_flick"),
                     columns=(("Right",), ("D",), (".",))),
                _Row(
                    "lock / unlock — locked, what is on screen repeats; unlocked it "
                    "moves on.  A satellite favorites the locked "
                    "slide and asks for a better version of it",
                    commands=("portrait_lock", "landscape_lock", "main_lock", *_every_funestra("lock_on"), *_every_funestra("lock_off")),
                    columns=(("Down",), ("S",), (",",)),
                ),
                _Row(
                    "trash — in Kino mode it sends the video to Trash and moves on, or "
                    "unfavorites it instead when it has been previously favorited; on a "
                    "Satellite it deletes the image or video and moves on",
                    commands=(*_sided("trash"), "genau_trash_flick"),
                    columns=(("Up",), ("W",), ("K",)),
                ),
            )),
            _Group("Navigation", (
                _Row(
                    "nudge back 10 seconds",
                    commands=_every_funestra("nudge_prev"),
                    columns=((), (), ("-",)),
                    controller=(B_OR_Y,),
                ),
                _Row(
                    "nudge forward 10 seconds",
                    commands=_every_funestra("nudge_next"),
                    columns=((), (), ("+",)),
                    controller=(A_OR_X,),
                ),
                _Row("previous scene in the video", commands=("main_scene_prev",),
                     controller=(HOLD_TRIGGER_AND_B_OR_Y,)),
                _Row("next scene in the video", commands=("main_scene_next",),
                     controller=(HOLD_TRIGGER_AND_A_OR_X,)),
                _Row(
                    "jump to funscript — skips ahead to where the scripting starts up "
                    "again, while that Funestra has the OSR2",
                    commands=(*_sided("funscript_jump"), "main_funestra_funscript_jump"),
                ),
                _Row(
                    "next funscripted — the next scripted video in the playlist, "
                    "landing where its action begins",
                    commands=(*_sided("next_funscripted"), "main_funestra_next_funscripted"),
                ),
                _Row(
                    "full video / back to the clip — \"full video\" plays the full "
                    "video the clip on screen was cut from, and \"clip jump\" or "
                    "\"jump back\" plays the clip cut from the full video on screen",
                    commands=("main_funestra_full_vid", "main_funestra_clip_jump"),
                ),
                _Row(
                    "compilation / end compilation — \"compilation\" plays the clip's "
                    "compilation in order, and \"end compilation\" leaves it for the "
                    "length mode you were in",
                    commands=("main_funestra_compilation", "main_funestra_end_compilation"),
                ),
            )),
            _Group("Nav Map", (
                _Row(
                    "navigate the map — move the lit cell up or down its column of "
                    "actions, switching to that one",
                    commands=("portrait_nav_up", "portrait_nav_down",
                              "landscape_nav_up", "landscape_nav_down"),
                    columns=(("Shift+Up", "Shift+Down"), ("Shift+W", "Shift+S")),
                ),
                _Row(
                    "cycle action — same seed or initial image, another action",
                    commands=_sided("cycle_action"),
                ),
                _Row("cycle seed — same settings, another seed",
                     commands=_sided("cycle_seed")),
                _Row(
                    "wrong action — the item's actions are wrong; send it back "
                    "to Origenerator's action setter tool to be set again",
                    commands=_sided("wrong_action"),
                ),
            )),
            _Group("Loops", (
                _Row(
                    "cycle loop mode — seeds, then actions, then off",
                    commands=("portrait_loop", "landscape_loop"),
                    columns=(("Home",), ("E",)),
                ),
                _Row("more seeds — widen the net to include seeds from very similar settings",
                     commands=_sided("more_seeds")),
                _Row("loop actions — same seed or initial image, all of its actions",
                     commands=_sided("action_loop")),
                _Row("loop seeds — same settings, all of their seeds",
                     commands=_sided("seed_loop")),
                _Row("stop looping — back to browsing, keeping the filter", commands=_sided("no_loop")),
            )),
            _Group("Filter", (
                _FILTER_BY_ACTION,
                _Row("filter to the action on screen", commands=_sided("lock_action")),
                _Row(
                    "drop all filters — keep the order and everything else",
                    commands=_sided("no_filter"),
                ),
            )),
            _Group("Playlist", (
                _Row("latest — reload what it plays newest-first", commands=_every_funestra("latest")),
                _Row("shuffle — reshuffle what it plays (cancels latest; keeps F-mode and the filter)",
                     commands=_every_funestra("shuffle")),
                _Row(
                    "F-mode — a satellite plays only the favorites, the Main Funestra "
                    "only the well funscripted videos, and in Genau mode only the favorite "
                    f"{_FLICK.many}; “favorites” says a satellite's, “funscripts” the Main "
                    "Funestra's",
                    commands=_every_funestra("fmode", "fmode_on", "fmode_off")
                    + tuple(say_command(scope, FAVORITES_PHRASE) for scope in _SCOPES),
                ),
            )),
            _Group("Included video types", (
                _Row(
                    "included video types (duration) — the Main Funestra's alone: in Kino mode, "
                    "the lengths: \"clips only\", \"full only\", or \"clips and full\" "
                    "(the default, everything), which leaves any compilation with it; "
                    "its HUD's own pair of buttons reaches a fourth state, neither, "
                    "which holds the video on screen",
                    commands=("main_funestra_length_clips", "main_funestra_length_full",
                              "main_funestra_length_mixed", "main_funestra_length_none"),
                ),
                _Row(
                    "included video types (projection) — the videos in Kino mode, "
                    f"Genau's {_FLICK.many} in Genau mode: \"flat only\" (or \"2D only\"), "
                    "\"VR only\", or \"flat and VR\"; its HUD's own pair of buttons reaches a "
                    "fourth state, neither, which holds what is on screen",
                    commands=("main_projection_flat", "main_projection_vr",
                              "main_projection_both", "main_projection_none"),
                ),
                _Row(
                    "included video types (orientation) — the videos in Kino mode, "
                    f"Genau's {_FLICK.many} in Genau mode: \"portrait only\", \"landscape only\", "
                    "or \"portrait and landscape\"; its HUD's own pair of buttons reaches a "
                    "fourth state, neither, which holds what is on screen",
                    commands=("main_orientation_portrait", "main_orientation_landscape",
                              "main_orientation_both", "main_orientation_none"),
                    voice_display=("portrait only", "landscape only", "portrait and landscape",
                                   "landscape and portrait"),
                ),
                _Row(
                    "included video types (enhancement) — \"enhanced only\" keeps "
                    "what has an enhanced version, \"original only\" what has none, and "
                    "\"enhanced and original\" everything",
                    commands=_every_funestra("enhancement_enhanced", "enhancement_original",
                                             "enhancement_both"),
                ),
            )),
            _Group("Audio", (
                _Row(
                    "volume down / up, in tenths — the Main Funestra's audio is the "
                    f"video's in Kino mode and the {_FLICK.one} music in Genau mode",
                    commands=_sided("volume_down", "volume_up") + _MAIN_AUDIO[:2],
                ),
                _Row("mute / unmute", commands=_sided("mute", "unmute") + _MAIN_AUDIO[2:]),
            )),
            _Group("Playback speed", (
                _Row(
                    "playback speed up / down — when the Main Funestra's rate changes, "
                    "both satellites take it",
                    commands=(*_sided("speed_up", "speed_down"), "main_funestra_speed_up", "main_funestra_speed_down"),
                    voice_display=("playback speed up", "playback slow down", "playback speed down"),
                ),
                _Row(
                    "set playback speed — 0.25\u00d7 to 2\u00d7; the Main Funestra's funscript follows",
                    commands=_sided(*_SPEED_STEPS) + tuple(f"main_funestra_{step}" for step in _SPEED_STEPS),
                    voice_display=_SPEED_VOICE,
                ),
            )),
            _Group("HUD", (
                *(_Row(f"move HUD {direction}",
                       commands=tuple(f"{scope}_hud_{direction}" for scope in ("portrait", "landscape", "main")),
                       columns=tuple((key,) for key in keys))
                  for direction, keys in _HUD_MOVE_KEYS.items()),
                _Row(
                    "move HUD to a corner — said with a corner (\"top left\", \"lower right\"; "
                    "\"move\" may lead, \"upper\" says the same as \"top\" and \"bottom\" "
                    "the same as \"lower\") it goes straight there, in the headset an "
                    "upper corner hanging it over the Funestra's top edge and a lower one "
                    "under its lower edge.  A click in a corner of the video sends it "
                    "there and opens it, and so does a squeeze on the plus beside a side "
                    "of the Funestra in the headset",
                    commands=_every_funestra(*(f"hud_{corner}" for corner in _HUD_CORNERS)),
                    voice_display=("hud top left", "hud upper left", "hud top right", "hud upper right",
                                   "hud lower left", "hud bottom left", "hud lower right",
                                   "hud bottom right"),
                ),
                _Row(
                    "minimize HUD — it collapses to a square with a plus on it, "
                    "and the plus brings it back",
                    commands=_every_funestra("hud_minimize", "hud_restore"),
                ),
            )),
            _Group("Other", (
                _Row(
                    "reset — back to every default: no filter, no lock, no loop, no "
                    "F-mode, normal speed, shuffled from the top; the Main Funestra's "
                    "every length, shape, orientation and enhancement too (leaving "
                    "any compilation)",
                    commands=_every_funestra("reset"),
                ),
                _Row(
                    "cycle enhancement — the upscale, the original it was made "
                    "from, or an enhancement",
                    commands=("portrait_cycle_version_back", "portrait_cycle_version",
                              "landscape_cycle_version_back", "landscape_cycle_version",
                              "satellites_cycle_version",
                              "main_funestra_cycle_version_back", "main_funestra_cycle_version"),
                    columns=(("Shift+Left", "Shift+Right"), ("Shift+A", "Shift+D"),
                             ("Shift+M", "Shift+.")),
                ),
                _Row(
                    "crown the Main Funestra or the Portrait Funestra — the crowned "
                    "Funestra takes up the majority of the (portrait-shaped) secondary "
                    "monitor when it is displaying portrait-shaped content; the other "
                    "Funestra then either shows landscape content or tiles its portrait "
                    "content",
                    commands=("main_crown", "portrait_crown"),
                    voice_display=("crown main", "crown portrait"),
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
                    f"{_FLICK.many} in Genau mode; the pick plays here.  In the headset "
                    "a squeeze opens a tile and the stick scrolls the list the pointer "
                    "is over",
                    (),
                    ("browse_library",),
                    controller=(STICK_UP_DOWN,),
                ),
            )),
            _Group("Kino", (
                _Row(
                    "Kino mode",
                    commands=("kino_activate", "main_kino_activate"),
                    voice_display=("kino", "kino mode"),
                ),
                _Row(
                    "loop control — hold to start recording a loop, release to end "
                    "recording, and tap again to terminate a recorded loop",
                    ("R",),
                    ("main_funestra_record_down", "main_funestra_record_up", "main_funestra_record_tap", "main_funestra_loop_cancel"),
                ),
                _Row(
                    f"save loop — save loop to send to Genaumacher to produce a new Genau {_FLICK.one}",
                    (";",),
                    ("genaumacher_save",),
                ),
            )),
            _Group("Genau", (
                _Row(
                    f"Genau mode — Genau's {_FLICK.many} on the Main Funestra, the Robot "
                    "Hand driving the OSR2",
                    commands=("genau_activate",),
                ),
                flip_row(_FLICK),
                _Row(
                    f"seconds a {_FLICK.one} holds the screen before Genau moves on — only "
                    "while it is unlocked (the , key holds it)",
                    (),
                    ("genau_flick_seconds_down", "genau_flick_seconds_up"),
                    (f"{_FLICK.one} seconds 1\u201360",),
                ),
            )),
        ),
    ),
    _Section(
        "Origenerator",
        (
            _Group("", (
                _Row(
                    "Origenerator mode — Origenerator Core shares real estate with the Random "
                    "Faves Browser",
                    commands=("origenerator_activate",),
                    voice_display=("origenerator", "origenerator mode"),
                ),
                _said_to_a_slideshow("fix the image on screen — its face, hands, teeth, or eyes",
                                     "fix face", "fix hands", "fix teeth", "fix eyes"),
                _said_to_a_slideshow(
                    f"animate the image as a Genau {_FLICK.one} — said alone, “genau” is "
                    "the Genau mode, so name a Satellite or “satellites”", *GENAU_SAID),
            )),
        ),
        note=_ORIGENERATOR_NOTE,
        merge_scopes=True,
    ),
    _Section(
        "Random Faves Browser",
        (
            _Group("", (
                _Row(
                    "Random Faves Browser",
                    commands=("rfb_activate",),
                    voice_display=("RFB", "random faves", "random faves browser"),
                ),
            )),
        ),
    ),
)


_SCOPE_WORDS = frozenset(("portrait", "landscape", "satellites", "main"))


def _collapse_scopes(phrase: str) -> str:
    return " ".join(word for word in phrase.split() if word not in _SCOPE_WORDS)


def _voice_for(
    commands: tuple[str, ...], *, fold: Callable[[str], str] | None = None
) -> tuple[str, ...]:
    shown_by_command: dict[str, set[str]] = {}
    for phrase, cmd in VOICE_COMMANDS.items():
        if cmd in commands:
            shown = friendly_voice(phrase)
            if fold is not None:
                shown = fold(shown)
            if shown:
                shown_by_command.setdefault(cmd, set()).add(shown)
    result: list[str] = []
    for cmd in commands:
        for phrase in sorted(shown_by_command.get(cmd, ())):
            if phrase not in result:
                result.append(phrase)
    return tuple(result)


def _display_voice(row: _Row, *, merge_scopes: bool) -> tuple[str, ...]:
    if row.voice_display is not None:
        return row.voice_display
    fold = _collapse_scopes if merge_scopes else None
    return _voice_for(row.commands, fold=fold) + row.literal_voice


def _key_columns(row: _Row, section: _Section) -> tuple[tuple[str, ...], ...]:
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
        controller=row.controller,
    )


def build_reference_sections(actions: Acts = MappingProxyType({})) -> tuple[ReferenceSection, ...]:
    return tuple(
        ReferenceSection(
            title=section.title,
            groups=tuple(
                ReferenceGroup(group.title, tuple(_reference_row(named, section) for row in group.rows
                                                  if (named := _named_actions(row, actions))))
                for group in section.groups
            ),
            key_headers=section.key_headers,
            note=section.note,
        )
        for section in _SECTIONS
    )


def reference_details() -> dict[str, str]:
    return _details_of(build_reference_sections())


def render_reference_html(width: int) -> str:
    return _render(build_reference_sections(), width)

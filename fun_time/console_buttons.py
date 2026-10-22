"""The buttons Fun Time puts on the main console, row by row, declared in
:class:`funestra.hud_button.Button` off what the room knows and what the
Main Funestra published about its video.  Each button's command is a dashboard
command verbatim: a press goes onto the command file, and the dispatcher
routes it to the Funestra the mode says owns it.
"""
from __future__ import annotations

from dataclasses import dataclass

from funestra.console import OSR2_DRIVING, ROW_LABEL_W, VALUE_W, aim_row
from funestra.hud_button import Button
from funestra.hud_marks import (
    BROKER_ICON,
    FMODE_ICON,
    LOCK_ICON,
    NEXT_ICON,
    PREVIOUS_ICON,
    shared_mark,
)
from funestra.modes import LengthMode, LoopState, MainMode
from shared_ui.spacing import BUTTON_WORD_W

from .content import genau_flick_noun
from .crown import CROWN_ICON, Crown
from .funestra_buttons import (
    MAIN_FUNESTRA_NOUN,
    TRASH_ICON,
    browse_order_buttons,
    lock_button,
    minimize_button,
    reset_button,
    transport,
    versions_button,
)
from .inclusion_pairs import (
    LENGTH,
    ORIENTATION,
    PROJECTION,
    Included,
    InclusionPair,
    lengths_included,
)
from .mode_plan import kino_shows
from .osr2_section import take_osr2_button
from .room_funestras import RoomFunestra


@dataclass(frozen=True)
class MainSlot:
    """What the console's buttons are lit and named from: the room's own state
    of the main slot, then what the Main Funestra published about its video,
    each defaulting to "cannot" until the Funestra says otherwise."""

    main_mode: MainMode = MainMode.KINO
    locked: bool = True
    f_mode: bool = False
    latest: bool | None = None
    loop_state: LoopState = LoopState.NORMAL
    learned: bool = False
    shape: str = "sine"
    plays_vr: bool | None = None
    plays_flat: bool | None = None
    plays_portrait: bool = True
    plays_landscape: bool = True
    pace_s: int = 0
    length_mode: LengthMode = LengthMode.MIXED
    compilation: str = ""
    has_compilation: bool = False
    has_other_versions: bool = False
    longer_than_a_step: bool = False
    jump_to: str = ""
    osr2_control: str = OSR2_DRIVING
    # Whether the Funestra is already at every default, which leaves its reset
    # nothing to put back -- the button is drawn faded and takes no press.
    nothing_to_reset: bool = False
    flipped: bool = False
    crowned: bool = True
    has_osr2: bool = True


OPEN_ICON = shared_mark("folder")
RECORD_ICON = shared_mark("disc")
SAVE_ICON = shared_mark("save")
LESS_ICON = shared_mark("minus")
MORE_ICON = shared_mark("plus")
COMPILATION_ICON = shared_mark("compilation")
CLIP_TO_SCENE_ICON = shared_mark("clip_to_scene")
SCENE_TO_CLIP_ICON = shared_mark("scene_to_clip")
FUNSCRIPT_JUMP_ICON = shared_mark("funscript_jump")
FLIP_ENDS_ICON = shared_mark("flip_ends")

MODE_BUTTONS = (
    ("main_kino_activate", "Kino", MainMode.KINO),
    ("genau_activate", "Genau", MainMode.GENAU),
)


def console_rows(slot: MainSlot, *, in_vr: bool = False) -> tuple[tuple[Button, ...], ...]:
    """The mode row (with minimize outside the headset, and the file controls riding
    it), the transport, and the pace of what it steps."""
    return (
        (
            *(
                Button(command, label, f"{label} mode", width=BUTTON_WORD_W,
                       lit=slot.main_mode is main_mode)
                for command, label, main_mode in MODE_BUTTONS
            ),
            *(() if in_vr else (
                minimize_button("main"),
                Button(Crown.MAIN.command, CROWN_ICON,
                       "Crowned — a portrait video here takes most of the secondary "
                       "monitor" if slot.crowned else
                       "Give this Funestra the crown — its portrait videos take "
                       "most of the secondary monitor", lit=slot.crowned),
            )),
            *_file_controls(slot),
        ),
        _transport_row(slot),
        _playback_speed_row() if kino_shows(slot.main_mode) else _flick_seconds_row(),
    )


def osr2_rows(slot: MainSlot) -> tuple[tuple[Button, ...], ...]:
    return (_control_row(slot) if slot.has_osr2 else (take_osr2_button(RoomFunestra.MAIN),),)


def osr2_controls(*, broker: bool) -> tuple[Button, ...]:
    return (
        Button("broker_panel", BROKER_ICON,
               "Broker is running — press to stop it" if broker
               else "Broker is not running — press to start it",
               lit=broker, warn=not broker),
    )


def _file_controls(slot: MainSlot) -> tuple[Button, ...]:
    if not kino_shows(slot.main_mode):
        return (Button("browse_library", OPEN_ICON, f"Browse the {genau_flick_noun().many}",
                       group_break=True),)
    return (
        Button("browse_library", OPEN_ICON, "Browse the library", group_break=True),
        Button("main_player_record_tap", RECORD_ICON,
               "Stop recording — mark the loop's out point"
               if slot.loop_state is LoopState.RECORDING else
               "Looping — press to drop the loop" if slot.loop_state is LoopState.LOOPING
               else "Record loop",
               warn=slot.loop_state is LoopState.RECORDING,
               hold=slot.loop_state is LoopState.LOOPING, group_break=True),
        Button("genaumacher_save", SAVE_ICON, "Save clip"),
    )


def _inclusions(slot: MainSlot) -> tuple[tuple[InclusionPair, Included], ...]:
    shown = []
    if slot.plays_vr is not None and slot.plays_flat is not None:
        shown.append((PROJECTION, (bool(slot.plays_flat), bool(slot.plays_vr))))
    if kino_shows(slot.main_mode):
        shown.append((LENGTH, lengths_included(slot.length_mode)))
    shown.append((ORIENTATION, (slot.plays_portrait, slot.plays_landscape)))
    return tuple(shown)


def title_filters(slot: MainSlot) -> tuple[str, ...]:
    return tuple(word for pair, included in _inclusions(slot)
                 if (word := pair.title_word(included)))


def _inclusion_buttons(slot: MainSlot, *, remembered: bool, things: str) -> tuple[Button, ...]:
    return tuple(button for pair, included in _inclusions(slot)
                 for button in pair.buttons(included, remembered=remembered, things=things))


def _compilation_button(slot: MainSlot) -> Button:
    inside = bool(slot.compilation)
    return Button(
        "main_player_end_compilation" if inside else "main_player_compilation",
        COMPILATION_ICON,
        "Playing this compilation in order — press to leave it" if inside
        else "Play this video's compilation, in order"
        + ("" if slot.has_compilation else " (this one belongs to none)"),
        lit=inside, dim=not (inside or slot.has_compilation), group_break=True,
    )


def _clip_scene_button(slot: MainSlot) -> Button:
    to_scene = slot.jump_to == "scene"
    return Button(
        "main_player_full_vid" if to_scene else "main_player_clip_jump",
        CLIP_TO_SCENE_ICON if to_scene else SCENE_TO_CLIP_ICON,
        "Play the full scene this clip came from" if to_scene
        else "Back to the clip taken from this scene" if slot.jump_to == "clip"
        else "Play the full scene this clip came from"
             " (no full scene in the library for this one)",
        dim=not slot.jump_to, group_break=True,
    )


def _transport_row(slot: MainSlot) -> tuple[Button, ...]:
    if kino_shows(slot.main_mode):
        remembered = bool(slot.compilation)
        return (
            *transport("main", noun=MAIN_FUNESTRA_NOUN, longer_than_a_step=slot.longer_than_a_step),
            lock_button("main", locked=slot.locked, noun=MAIN_FUNESTRA_NOUN),
            Button("main_fmode", FMODE_ICON,
                   "F-Mode — play only the videos that have a funscript",
                   lit=slot.f_mode, favorite=True),
            reset_button("main", nothing_to_reset=slot.nothing_to_reset),
            *browse_order_buttons("main", latest=slot.latest, remembered=remembered),
            *_inclusion_buttons(slot, remembered=remembered, things="videos"),
            _compilation_button(slot),
            _clip_scene_button(slot),
            versions_button("main_player_cycle_version", noun=MAIN_FUNESTRA_NOUN,
                            has_other_versions=slot.has_other_versions),
        )
    flick = genau_flick_noun()
    return (
        Button("genau_prev_flick", PREVIOUS_ICON, f"Previous {flick.one}"),
        Button("genau_next_flick", NEXT_ICON, f"Next {flick.one}"),
        Button("main_lock", LOCK_ICON,
               f"Locked — this {flick.one} repeats; press to move on every "
               f"{slot.pace_s}s" if slot.locked
               else "Unlocked — moving on every "
                    f"{slot.pace_s}s; press to hold this {flick.one}",
               lit=slot.locked, favorite=True, group_break=True),
        Button("main_fmode", FMODE_ICON,
               "Showing the favorites only — press for all of them" if slot.f_mode
               else "F-Mode — play only the favorites",
               lit=slot.f_mode, favorite=True),
        Button("genau_flip_ends", FLIP_ENDS_ICON,
               f"Flipped — this {flick.one} stays that way; press to put it back" if slot.flipped
               else f"Flip this {flick.one}, for a picture running opposite the OSR2 — it stays flipped",
               lit=slot.flipped, group_break=True),
        Button("genau_weird_flick", TRASH_ICON, "Mark weird — move it out", danger=True),
        *browse_order_buttons("main", latest=slot.latest),
        *_inclusion_buttons(slot, remembered=False, things=flick.many),
    )


def _playback_speed_row() -> tuple[Button, ...]:
    return (
        Button("", "Playback speed", "", width=ROW_LABEL_W),
        Button("main_player_speed_down", LESS_ICON, "Play the video slower",
               group_break=True),
        Button("", "", "", width=VALUE_W, host_value="playback_speed"),
        Button("main_player_speed_up", MORE_ICON, "Play the video faster"),
    )


def _flick_seconds_row() -> tuple[Button, ...]:
    flick = genau_flick_noun()
    return (
        Button("", f"{flick.one.capitalize()} seconds", "", width=ROW_LABEL_W),
        Button("genau_flick_seconds_down", LESS_ICON, "Move on sooner", group_break=True),
        Button("", "", "", width=VALUE_W, host_value="advance_interval"),
        Button("genau_flick_seconds_up", MORE_ICON, f"Leave each {flick.one} longer"),
    )


def _control_row(slot: MainSlot) -> tuple[Button, ...]:
    return (
        *aim_row(learned=slot.learned, shape=slot.shape, control=slot.osr2_control),
        *((
            Button("main_player_funscript_jump", FUNSCRIPT_JUMP_ICON,
                   "Skip ahead to where this video's scripting starts up again",
                   group_break=True),
        ) if kino_shows(slot.main_mode) else ()),
    )

from __future__ import annotations

from dataclasses import dataclass

from funestra.hud_button import Button
from funestra.hud_controls import (
    RESET_TOOLTIP,
    TRASH_ICON,
    browse_order_buttons,
    fmode_button,
    funscript_jump_button,
    lock_button,
    lock_tooltip,
    reset_button,
    transport,
    versions_button,
)
from funestra.hud_icons import (
    FMODE_ICON,
    LOCK_ICON,
    NEXT_ICON,
    PREVIOUS_ICON,
    shared_icon,
)
from funestra.main_hud import ROW_LABEL_W, VALUE_W
from funestra.modes import LengthMode, LoopState, MainMode
from shared_ui.spacing import BUTTON_WORD_W

from .content import genau_flick_noun
from .crown import CROWN_ICON, Crown
from .fun_time_funestras import FunTimeFunestra
from .funestra_buttons import NOUN, minimize_funestra_button
from .inclusion_pairs import (
    LENGTH,
    ORIENTATION,
    PROJECTION,
    Included,
    InclusionPair,
    lengths_included,
)
from .mode_plan import kino_shows
from .osr2_section import osr2_button


@dataclass(frozen=True)
class MainFunestraFacts:
    main_mode: MainMode = MainMode.KINO
    locked: bool = True
    f_mode: bool = False
    latest: bool | None = None
    loop_state: LoopState = LoopState.NORMAL
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
    nothing_to_reset: bool = False
    flipped: bool = False
    crowned: bool = True
    has_osr2: bool = True
    has_funscript: bool = False


OPEN_ICON = shared_icon("folder")
RECORD_ICON = shared_icon("disc")
SAVE_ICON = shared_icon("save")
LESS_ICON = shared_icon("minus")
MORE_ICON = shared_icon("plus")
COMPILATION_ICON = shared_icon("compilation")
CLIP_TO_SCENE_ICON = shared_icon("clip_to_scene")
SCENE_TO_CLIP_ICON = shared_icon("scene_to_clip")
FLIP_ENDS_ICON = shared_icon("flip_ends")

MODE_BUTTONS = (
    ("main_kino_activate", "Kino", MainMode.KINO),
    ("genau_activate", "Genau", MainMode.GENAU),
)


def main_hud_rows(facts: MainFunestraFacts, *, in_vr: bool = False) -> tuple[tuple[Button, ...], ...]:
    return (
        (
            *(
                Button(command, label, "", width=BUTTON_WORD_W,
                       lit=facts.main_mode is main_mode)
                for command, label, main_mode in MODE_BUTTONS
            ),
            *(() if in_vr else (
                minimize_funestra_button("main"),
                Button(Crown.MAIN.command, CROWN_ICON,
                       "Crowned — a portrait video here takes most of the secondary "
                       "monitor" if facts.crowned else
                       "Give this Funestra the crown — its portrait videos take "
                       "most of the secondary monitor", lit=facts.crowned),
            )),
            *_file_controls(facts),
        ),
        _transport_row(facts),
        _playback_speed_row() if kino_shows(facts.main_mode) else _flick_seconds_row(),
    )


def osr2_rows(facts: MainFunestraFacts) -> tuple[tuple[Button, ...], ...]:
    return (_control_row(facts),)


def _file_controls(facts: MainFunestraFacts) -> tuple[Button, ...]:
    if not kino_shows(facts.main_mode):
        return (Button("browse_library", OPEN_ICON, f"Browse the {genau_flick_noun().many}",
                       group_break=True),)
    return (
        Button("browse_library", OPEN_ICON, "Browse the library", group_break=True),
        Button("main_funestra_record_tap", RECORD_ICON,
               "Stop recording — mark the loop's out point"
               if facts.loop_state is LoopState.RECORDING else
               "Looping — press to drop the loop" if facts.loop_state is LoopState.LOOPING
               else "Record loop",
               warn=facts.loop_state is LoopState.RECORDING,
               hold=facts.loop_state is LoopState.LOOPING, group_break=True),
        Button("genaumacher_save", SAVE_ICON, "Save clip"),
    )


def _inclusions(facts: MainFunestraFacts) -> tuple[tuple[InclusionPair, Included], ...]:
    shown = []
    if facts.plays_vr is not None and facts.plays_flat is not None:
        shown.append((PROJECTION, (bool(facts.plays_flat), bool(facts.plays_vr))))
    if kino_shows(facts.main_mode):
        shown.append((LENGTH, lengths_included(facts.length_mode)))
    shown.append((ORIENTATION, (facts.plays_portrait, facts.plays_landscape)))
    return tuple(shown)


def title_filters(facts: MainFunestraFacts) -> tuple[str, ...]:
    return tuple(word for pair, included in _inclusions(facts)
                 if (word := pair.title_word(included)))


def _inclusion_buttons(facts: MainFunestraFacts, *, remembered: bool, things: str) -> tuple[Button, ...]:
    return tuple(button for pair, included in _inclusions(facts)
                 for button in pair.buttons(included, remembered=remembered, things=things))


def _compilation_button(facts: MainFunestraFacts) -> Button:
    inside = bool(facts.compilation)
    return Button(
        "main_funestra_end_compilation" if inside else "main_funestra_compilation",
        COMPILATION_ICON,
        "Playing this compilation in order — press to leave it" if inside
        else "Play this video's compilation, in order"
        + ("" if facts.has_compilation else " (this one belongs to none)"),
        lit=inside, dim=not (inside or facts.has_compilation), group_break=True,
    )


def _clip_scene_button(facts: MainFunestraFacts) -> Button:
    to_scene = facts.jump_to == "scene"
    return Button(
        "main_funestra_full_vid" if to_scene else "main_funestra_clip_jump",
        CLIP_TO_SCENE_ICON if to_scene else SCENE_TO_CLIP_ICON,
        "Play the full scene this clip came from" if to_scene
        else "Back to the clip taken from this scene" if facts.jump_to == "clip"
        else "Play the full scene this clip came from"
             " (no full scene in the library for this one)",
        dim=not facts.jump_to, group_break=True,
    )


def _transport_row(facts: MainFunestraFacts) -> tuple[Button, ...]:
    if kino_shows(facts.main_mode):
        remembered = bool(facts.compilation)
        return (
            *transport("main", noun=NOUN, longer_than_a_step=facts.longer_than_a_step),
            funscript_jump_button("main_funestra_funscript_jump", noun=NOUN,
                                  has_osr2=facts.has_osr2, has_funscript=facts.has_funscript),
            lock_button("main", locked=facts.locked, tooltip=lock_tooltip(facts.locked, NOUN)),
            fmode_button("main", "F-Mode — play only the well funscripted videos", lit=facts.f_mode),
            reset_button("main", nothing_to_reset=facts.nothing_to_reset, tooltip=RESET_TOOLTIP),
            *browse_order_buttons("main", latest=facts.latest, remembered=remembered),
            *_inclusion_buttons(facts, remembered=remembered, things="videos"),
            _compilation_button(facts),
            _clip_scene_button(facts),
            versions_button("main_funestra_cycle_version", noun=NOUN,
                            has_other_versions=facts.has_other_versions),
        )
    flick = genau_flick_noun()
    return (
        Button("genau_prev_flick", PREVIOUS_ICON, f"Previous {flick.one}"),
        Button("genau_next_flick", NEXT_ICON, f"Next {flick.one}"),
        Button("main_lock", LOCK_ICON,
               f"Locked — this {flick.one} repeats; press to move on every "
               f"{facts.pace_s}s" if facts.locked
               else "Unlocked — moving on every "
                    f"{facts.pace_s}s; press to hold this {flick.one}",
               lit=facts.locked, favorite=True, group_break=True),
        Button("main_fmode", FMODE_ICON,
               "Showing the favorites only — press for all of them" if facts.f_mode
               else "F-Mode — play only the favorites",
               lit=facts.f_mode, favorite=True),
        Button("genau_flip_ends", FLIP_ENDS_ICON,
               f"Flipped — this {flick.one} stays that way; press to put it back" if facts.flipped
               else f"Flip this {flick.one}, for one running opposite the OSR2 — it stays flipped",
               lit=facts.flipped, group_break=True),
        Button("genau_trash_flick", TRASH_ICON, "Send to Trash — move it out", danger=True),
        *browse_order_buttons("main", latest=facts.latest),
        *_inclusion_buttons(facts, remembered=False, things=flick.many),
    )


def _playback_speed_row() -> tuple[Button, ...]:
    return (
        Button("", "Playback speed", "", width=ROW_LABEL_W),
        Button("main_funestra_speed_down", LESS_ICON, "Play the video slower",
               group_break=True),
        Button("", "", "", width=VALUE_W, host_value="playback_speed"),
        Button("main_funestra_speed_up", MORE_ICON, "Play the video faster"),
    )


def _flick_seconds_row() -> tuple[Button, ...]:
    flick = genau_flick_noun()
    return (
        Button("", f"{flick.one.capitalize()} seconds", "", width=ROW_LABEL_W),
        Button("genau_flick_seconds_down", LESS_ICON, "Move on sooner", group_break=True),
        Button("", "", "", width=VALUE_W, host_value="advance_interval"),
        Button("genau_flick_seconds_up", MORE_ICON, f"Leave each {flick.one} longer"),
    )


def _control_row(facts: MainFunestraFacts) -> tuple[Button, ...]:
    return (osr2_button(FunTimeFunestra.MAIN, has_it=facts.has_osr2),)

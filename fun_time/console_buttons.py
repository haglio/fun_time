"""The buttons Fun Time puts on the main console, row by row, declared in
:class:`player_core.hud_button.Button` off what the room knows and what the
main player published about its video.  Each button's command is a dashboard
command verbatim: a press goes onto the command file, and the dispatcher
routes it to the player the mode says owns it.
"""
from __future__ import annotations

from dataclasses import dataclass

from player_core.console import (
    OSR2_CONTROL_OFF,
    OSR2_DRIVING,
    OSR2_PARKED,
    OSR2_RETRACTED,
    ROW_LABEL_W,
    VALUE_W,
    shape_label,
)
from player_core.hud_button import Button
from player_core.hud_marks import BROKER_ICON, FMODE_ICON, shared_mark
from player_core.modes import LengthMode, LoopState, MainMode
from shared_ui.spacing import BUTTON_WORD_W

from .crown import CROWN_ICON, Crown
from .mode_plan import main_player_displays
from .osr2_section import take_osr2_button
from .player_buttons import (
    LOCK_FACE,
    MAIN_PLAYER_NOUN,
    NEXT_FACE,
    PREV_FACE,
    TRASH_ICON,
    browse_order_buttons,
    lit_or_remembered,
    lock_button,
    minimize_button,
    reset_button,
    transport,
    versions_button,
)
from .players import Player


@dataclass(frozen=True)
class MainSlot:
    """What the console's buttons are lit and named from: the room's own state
    of the main slot, then what the main player published about its video,
    each defaulting to "cannot" until the player says otherwise."""

    main_mode: MainMode = MainMode.KINO
    locked: bool = True
    scripted_filter: bool = False
    latest: bool | None = None
    loop_state: LoopState = LoopState.NORMAL
    cruise: bool = False
    learned: bool = False
    shape: str = "sine"
    plays_vr: bool | None = None
    plays_flat: bool | None = None
    pace_s: int = 0
    length_mode: LengthMode | None = None
    compilation: str = ""
    has_compilation: bool = False
    has_other_versions: bool = False
    longer_than_a_step: bool = False
    jump_to: str = ""
    favorites_filter: bool | None = None
    enhanced_filter: bool | None = None
    osr2_control: str = OSR2_DRIVING
    # Whether the player is already at every default, which leaves its reset
    # nothing to put back -- the button is drawn faded and takes no press.
    nothing_to_reset: bool = False
    flipped: bool = False
    crowned: bool = True
    has_osr2: bool = True


# The glyphs this console types, as against the family's marks it names below.
_GLYPHS = {
    "open": "📂", "record": "⏺", "save": "💾", "minus": "−", "plus": "+",
}
ENHANCE_FILTER_ICON = shared_mark("enhance_filter")
FULL_LENGTH_ICON = shared_mark("full_length")
SHORTS_ICON = shared_mark("shorts")
VR_ICON = shared_mark("vr_hemisphere")
FLAT_ICON = shared_mark("flat_2d")
COMPILATION_ICON = shared_mark("compilation")
CLIP_TO_SCENE_ICON = shared_mark("clip_to_scene")
SCENE_TO_CLIP_ICON = shared_mark("scene_to_clip")
FUNSCRIPT_JUMP_ICON = shared_mark("funscript_jump")
PARK_ICON = shared_mark("park")
RETRACT_ICON = shared_mark("retract")
RELEASE_ICON = shared_mark("release")
CONTROL_OFF_ICON = shared_mark("control_off")
QUARTER_ICON = shared_mark("quarter_offset")
FLIP_ENDS_ICON = shared_mark("flip_ends")
WAVE_ICON = shared_mark("wave")

MODE_BUTTONS = (
    ("main_kino_activate", "Kino", MainMode.KINO),
    ("genau_activate", "Genau", MainMode.GENAU),
)


def console_rows(slot: MainSlot, *, in_vr: bool = False) -> tuple[tuple[Button, ...], ...]:
    """The mode row (with minimize outside the headset, and the file controls riding
    it), the transport, the pace of what it steps, and the Robot Hand's hands-free row."""
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
                       "Give this player the crown — its portrait videos take "
                       "most of the secondary monitor", lit=slot.crowned),
            )),
            *_file_controls(slot),
        ),
        _transport_row(slot),
        _playback_speed_row() if main_player_displays(slot.main_mode) else _clip_seconds_row(),
        _control_row(slot) if slot.has_osr2 else (take_osr2_button(Player.MAIN),),
    )


def osr2_controls(*, broker: bool) -> tuple[Button, ...]:
    return (
        Button("broker_panel", BROKER_ICON,
               "OSR2 broker is running — press to stop it" if broker
               else "OSR2 broker is not running — press to start it",
               lit=broker, warn=not broker),
    )


def _file_controls(slot: MainSlot) -> tuple[Button, ...]:
    if not main_player_displays(slot.main_mode):
        return (Button("browse_library", _GLYPHS["open"], "Browse the clips", group_break=True),)
    return (
        Button("browse_library", _GLYPHS["open"], "Browse the library", group_break=True),
        Button("main_player_record_tap", _GLYPHS["record"],
               "Stop recording — mark the loop's out point"
               if slot.loop_state is LoopState.RECORDING else
               "Looping — press to drop the loop" if slot.loop_state is LoopState.LOOPING
               else "Record loop",
               warn=slot.loop_state is LoopState.RECORDING,
               hold=slot.loop_state is LoopState.LOOPING, group_break=True),
        Button("clipper_save", _GLYPHS["save"], "Save clip"),
    )


def _inclusion_button(command: str, mark: str, kind: str, *, on: bool,
                      remembered: bool, group_break: bool = False) -> Button:
    tooltip = f"Including {kind}" if on else f"Not including {kind}"
    return Button(command, mark, tooltip, group_break=group_break,
                  **lit_or_remembered(on, remembered))


def _projection_buttons(slot: MainSlot, *, remembered: bool, things: str) -> tuple[Button, ...]:
    if slot.plays_vr is None or slot.plays_flat is None:
        return ()
    flat, vr = bool(slot.plays_flat), bool(slot.plays_vr)
    return (
        _inclusion_button(
            ("main_projection_vr" if vr else "main_projection_none") if flat
            else ("main_projection_both" if vr else "main_projection_flat"),
            FLAT_ICON, f"2D {things}", on=flat, remembered=remembered, group_break=True),
        _inclusion_button(
            ("main_projection_flat" if flat else "main_projection_none") if vr
            else ("main_projection_both" if flat else "main_projection_vr"),
            VR_ICON, f"VR {things}", on=vr, remembered=remembered),
    )


def _length_buttons(slot: MainSlot, *, remembered: bool) -> tuple[Button, ...]:
    if slot.length_mode is None:
        return ()
    mixed = slot.length_mode is LengthMode.MIXED
    shorts = mixed or slot.length_mode is LengthMode.SHORTS
    full = mixed or slot.length_mode is LengthMode.FULL
    return (
        _inclusion_button(
            ("main_player_length_full" if full else "main_player_length_none") if shorts
            else ("main_player_length_mixed" if full else "main_player_length_shorts"),
            SHORTS_ICON, "shorts", on=shorts, remembered=remembered, group_break=True),
        _inclusion_button(
            ("main_player_length_shorts" if shorts else "main_player_length_none") if full
            else ("main_player_length_mixed" if shorts else "main_player_length_full"),
            FULL_LENGTH_ICON, "full-length scenes", on=full, remembered=remembered),
    )


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
    if main_player_displays(slot.main_mode):
        remembered = bool(slot.compilation)
        return (
            *transport("main", noun=MAIN_PLAYER_NOUN, longer_than_a_step=slot.longer_than_a_step),
            lock_button("main", locked=slot.locked, noun=MAIN_PLAYER_NOUN),
            Button("main_fmode", FMODE_ICON,
                   "F-Mode — play only the videos that have a funscript",
                   lit=slot.scripted_filter, favorite=True),
            reset_button("main", nothing_to_reset=slot.nothing_to_reset),
            *browse_order_buttons("main", latest=slot.latest, remembered=remembered),
            *_projection_buttons(slot, remembered=remembered, things="videos"),
            *_length_buttons(slot, remembered=remembered),
            _compilation_button(slot),
            _clip_scene_button(slot),
            versions_button("main_player_cycle_version", noun=MAIN_PLAYER_NOUN,
                            has_other_versions=slot.has_other_versions),
        )
    return (
        Button("genau_prev_clip", PREV_FACE, "Previous clip"),
        Button("genau_next_clip", NEXT_FACE, "Next clip"),
        Button("main_lock", LOCK_FACE,
               "Locked — this clip repeats; press to move on every "
               f"{slot.pace_s}s" if slot.locked
               else "Unlocked — moving on every "
                    f"{slot.pace_s}s; press to hold this clip",
               lit=slot.locked, favorite=True, group_break=True),
        *(() if slot.favorites_filter is None else (
            Button("main_fmode", FMODE_ICON,
                   "Showing the favorites only — press for all of them"
                   if slot.favorites_filter
                   else "F-Mode — play only the favorites",
                   lit=slot.favorites_filter, favorite=True),
        )),
        *(() if slot.enhanced_filter is None else (
            Button("genau_filter_enhanced", ENHANCE_FILTER_ICON,
                   "Showing the enhanced pictures only — press for all of them"
                   if slot.enhanced_filter
                   else "Show only the pictures that have been enhanced",
                   lit=slot.enhanced_filter, enhanced=True, group_break=True),
        )),
        Button("genau_flip_ends", FLIP_ENDS_ICON,
               "Flipped — this clip stays that way; press to put it back" if slot.flipped
               else "Flip this clip, for a picture running opposite the OSR2 — it stays flipped",
               lit=slot.flipped, group_break=True),
        Button("genau_weird_clip", TRASH_ICON, "Mark weird — move it out", danger=True),
        *browse_order_buttons("main", latest=slot.latest),
        *_projection_buttons(slot, remembered=False, things="clips"),
    )


def _playback_speed_row() -> tuple[Button, ...]:
    return (
        Button("", "Playback speed", "", width=ROW_LABEL_W),
        Button("main_player_speed_down", _GLYPHS["minus"], "Play the video slower",
               group_break=True),
        Button("", "", "", width=VALUE_W, host_value="playback_speed"),
        Button("main_player_speed_up", _GLYPHS["plus"], "Play the video faster"),
    )


def _clip_seconds_row() -> tuple[Button, ...]:
    return (
        Button("", "Clip seconds", "", width=ROW_LABEL_W),
        Button("genau_clip_seconds_down", _GLYPHS["minus"], "Move on sooner", group_break=True),
        Button("", "", "", width=VALUE_W, host_value="advance_interval"),
        Button("genau_clip_seconds_up", _GLYPHS["plus"], "Leave each clip longer"),
    )


def _control_row(slot: MainSlot) -> tuple[Button, ...]:
    return (
        *aim_row(cruise=slot.cruise, learned=slot.learned, shape=slot.shape,
                 control=slot.osr2_control),
        *((
            Button("main_player_funscript_jump", FUNSCRIPT_JUMP_ICON,
                   "Skip ahead to where this video's scripting starts up again",
                   group_break=True),
        ) if main_player_displays(slot.main_mode) else ()),
    )


def aim_row(*, cruise: bool, learned: bool, shape: str, control: str) -> tuple[Button, ...]:
    return (
        Button("robot_hand_toggle_cruise", "cc",
               "Cruise control: vary the motion hands-free", lit=cruise),
        Button("robot_hand_toggle_learned", "hi",
               "Human inspired: motion drawn from real hand-made scripts, not a waveform",
               lit=learned),
        Button("robot_hand_cycle_shape", WAVE_ICON, f"Waveform: {shape_label(shape)}"),
        Button("quarter_button", QUARTER_ICON, "Offset the motion a ¼ cycle"),
        Button("osr2_control_off", CONTROL_OFF_ICON,
               "Control off — the OSR2 settles home and is left there; nothing "
               "here moves it again until you park, retract or drive it.  The "
               "device itself stays on: this is the app letting go of it, not "
               "the OSR2 switching off",
               warn=control == OSR2_CONTROL_OFF, group_break=True),
        Button("robot_hand_park", PARK_ICON,
               "Parked — the OSR2 held still, settled home",
               lit=control == OSR2_PARKED),
        Button("robot_hand_retract", RETRACT_ICON,
               "Retracted — the OSR2 held still at the far end, away from you",
               lit=control == OSR2_RETRACTED),
        Button("robot_hand_release", RELEASE_ICON,
               "Driving — the OSR2 back on whatever the motion was doing, "
               "cruise included",
               lit=control == OSR2_DRIVING),
    )

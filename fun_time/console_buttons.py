"""The buttons Fun Time puts on the main console, row by row, declared in
:class:`player_core.hud_button.Button` off what the room knows and what the
main player published about its video.  Each button's action is a dashboard
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
)
from player_core.hud_button import Button
from player_core.hud_marks import BROKER_ICON, FMODE_ICON, MINIMIZE_ICON, shared_mark
from player_core.hud_status import LATEST_LABEL, SHUFFLE_LABEL
from shared_ui.spacing import BUTTON_WORD_W

from .mode_plan import main_player_displays

# The main player's length modes.  MIXED has no button of its own: it is every
# length there is, which the console says by lighting both.
FULL, SHORTS, MIXED = "full", "shorts", "mixed"

_SHAPE_LABELS = {"rounded_square": "Square"}


def shape_label(shape: str) -> str:
    if shape in _SHAPE_LABELS:
        return _SHAPE_LABELS[shape]
    return " ".join(word.capitalize() for word in shape.split("_"))


@dataclass(frozen=True)
class MainSlot:
    """What the console's buttons are lit and named from: the room's own state
    of the main slot, then what the main player published about its video,
    each defaulting to "cannot" until the player says otherwise."""

    mode: str = "video"
    locked: bool = True
    f_mode: bool = False
    latest: bool | None = None
    record: str = "normal"
    cruise: bool = False
    learned: bool = False
    shape: str = "sine"
    plays_vr: bool | None = None
    plays_flat: bool | None = None
    pace_s: int = 0
    length_mode: str = ""
    compilation: str = ""
    has_compilation: bool = False
    has_other_versions: bool = False
    jump_to: str = ""
    favorites_filter: bool | None = None
    enhanced_filter: bool | None = None
    osr2_control: str = OSR2_DRIVING


# The glyphs this console types, as against the family's marks it names below.
_GLYPHS = {
    "prev": "⏮", "next": "⏭", "back": "⏪", "fwd": "⏩",
    "open": "📂", "record": "⏺", "save": "💾",
    "lock": "🔒", "minus": "−", "plus": "+",
}
TRASH_ICON = shared_mark("trash")
RESET_ICON = shared_mark("reset")
ENHANCE_FILTER_ICON = shared_mark("enhance_filter")
SHUFFLE_ICON = shared_mark("shuffle")
LATEST_ICON = shared_mark("latest")
FULL_LENGTH_ICON = shared_mark("clock_full")
SHORTS_ICON = shared_mark("clock_short")
VR_ICON = shared_mark("vr_hemisphere")
FLAT_ICON = shared_mark("flat_2d")
VERSIONS_ICON = shared_mark("versions")
COMPILATION_ICON = shared_mark("compilation")
CLIP_TO_SCENE_ICON = shared_mark("clip_to_scene")
SCENE_TO_CLIP_ICON = shared_mark("scene_to_clip")
FUNSCRIPT_JUMP_ICON = shared_mark("funscript_jump")
PARK_ICON = shared_mark("park")
RETRACT_ICON = shared_mark("retract")
RELEASE_ICON = shared_mark("release")
CONTROL_OFF_ICON = shared_mark("control_off")
QUARTER_ICON = shared_mark("quarter_offset")
WAVE_ICON = shared_mark("wave")

MODE_BUTTONS = (
    ("main_video_activate", "Video", "video"),
    ("genau_activate", "Genau", "genau"),
)


def console_rows(slot: MainSlot, *, modes: bool = True) -> tuple[tuple[Button, ...], ...]:
    """The mode row (with minimize and the file controls riding it), the
    transport, the pace of what it steps, and the Robot Hand's hands-free row.
    *modes* off drops the mode row, for a console inside another app's window."""
    rows: list[tuple[Button, ...]] = []
    if modes:
        rows.append((
            *(
                Button(action, label, f"{label} mode", width=BUTTON_WORD_W,
                       lit=slot.mode == mode)
                for action, label, mode in MODE_BUTTONS
            ),
            Button("main_minimize", MINIMIZE_ICON,
                   "Minimize this player — bring it back from the taskbar",
                   group_break=True),
            *_file_controls(slot),
        ))
    rows.append(_transport_row(slot))
    rows.append(_playback_speed_row() if main_player_displays(slot.mode) else _clip_seconds_row())
    rows.append(_control_row(slot))
    return tuple(rows)


def osr2_controls(*, broker: bool) -> tuple[Button, ...]:
    return (
        Button("broker_panel", BROKER_ICON,
               "OSR2 broker is running — press to stop it" if broker
               else "OSR2 broker is not running — press to start it",
               lit=broker, warn=not broker),
    )


def _file_controls(slot: MainSlot) -> tuple[Button, ...]:
    if not main_player_displays(slot.mode):
        return ()
    return (
        Button("browse_library", _GLYPHS["open"], "Browse the library", group_break=True),
        Button("main_player_record_tap", _GLYPHS["record"],
               "Stop recording — mark the loop's out point"
               if slot.record == "recording" else
               "Looping — press to drop the loop" if slot.record == "looping"
               else "Record loop",
               warn=slot.record == "recording",
               hold=slot.record == "looping", group_break=True),
        Button("clipper_save", _GLYPHS["save"], "Save clip"),
    )


def _browse_order_buttons(slot: MainSlot, *, remembered: bool = False) -> tuple[Button, ...]:
    if slot.latest is None:
        return ()
    on = (not slot.latest, bool(slot.latest))
    return (
        Button("main_shuffle", SHUFFLE_ICON, f"{SHUFFLE_LABEL} — reshuffle what plays",
               lit=on[0] and not remembered, remembered=on[0] and remembered,
               group_break=True),
        Button("main_latest", LATEST_ICON, f"{LATEST_LABEL} — reload it newest-first",
               lit=on[1] and not remembered, remembered=on[1] and remembered),
    )


def _projection_buttons(slot: MainSlot, *, remembered: bool) -> tuple[Button, ...]:
    if slot.plays_vr is None or slot.plays_flat is None:
        return ()
    vr, flat = bool(slot.plays_vr), bool(slot.plays_flat)

    def state(on: bool) -> dict:
        return {"lit": on and not remembered, "remembered": on and remembered}

    return (
        Button(
            ("main_projection_flat" if flat else "main_projection_none") if vr
            else ("main_projection_both" if flat else "main_projection_vr"),
            VR_ICON,
            "Only the VR videos are playing" if vr and not flat
            else "Drop the VR videos" if vr
            else "Put the VR videos back", group_break=True, **state(vr)),
        Button(
            ("main_projection_vr" if vr else "main_projection_none") if flat
            else ("main_projection_both" if vr else "main_projection_flat"),
            FLAT_ICON,
            "Only the flat videos are playing" if flat and not vr
            else "Drop the flat videos" if flat
            else "Put the flat videos back", **state(flat)),
    )


def _length_buttons(slot: MainSlot, *, remembered: bool) -> tuple[Button, ...]:
    if not slot.length_mode:
        return ()
    mixed = slot.length_mode == MIXED
    full = mixed or slot.length_mode == FULL
    shorts = mixed or slot.length_mode == SHORTS

    def state(on: bool) -> dict:
        return {"lit": on and not remembered, "remembered": on and remembered}

    return (
        Button(
            ("main_player_length_shorts" if shorts else "main_player_length_none") if full
            else ("main_player_length_mixed" if shorts else "main_player_length_full"),
            FULL_LENGTH_ICON,
            "Only the full-length scenes are playing" if full and not shorts
            else "Drop the full-length scenes" if full
            else "Put the full-length scenes back", group_break=True, **state(full)),
        Button(
            ("main_player_length_full" if full else "main_player_length_none") if shorts
            else ("main_player_length_mixed" if full else "main_player_length_shorts"),
            SHORTS_ICON,
            "Only the shorts are playing" if shorts and not full
            else "Drop the shorts" if shorts
            else "Put the shorts back", **state(shorts)),
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
    if main_player_displays(slot.mode):
        remembered = bool(slot.compilation)
        return (
            Button("main_prev", _GLYPHS["prev"], "Previous video"),
            Button("main_nudge_prev", _GLYPHS["back"], "Back 10s"),
            Button("main_nudge_next", _GLYPHS["fwd"], "Forward 10s"),
            Button("main_next", _GLYPHS["next"], "Next video"),
            Button("main_lock", _GLYPHS["lock"],
                   "Locked — this video repeats; press to play on through the "
                   "playlist" if slot.locked
                   else "Unlocked — plays on through the playlist; press to hold "
                        "this video",
                   lit=slot.locked, favorite=True, group_break=True),
            Button("main_fmode", FMODE_ICON,
                   "F-Mode — play only the videos that have a funscript",
                   lit=slot.f_mode, favorite=True),
            Button("main_reset", RESET_ICON,
                   "Reset — the whole library back, with F-Mode off", group_break=True),
            *_browse_order_buttons(slot, remembered=remembered),
            *_projection_buttons(slot, remembered=remembered),
            *_length_buttons(slot, remembered=remembered),
            _compilation_button(slot),
            _clip_scene_button(slot),
            Button("main_player_cycle_version", VERSIONS_ICON,
                   "Another version of this video"
                   + ("" if slot.has_other_versions else " (none for this one)"),
                   dim=not slot.has_other_versions, group_break=True),
        )
    return (
        Button("genau_prev_clip", _GLYPHS["prev"], "Previous clip"),
        Button("genau_next_clip", _GLYPHS["next"], "Next clip"),
        Button("main_lock", _GLYPHS["lock"],
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
        Button("genau_weird_clip", TRASH_ICON, "Mark weird — move it out",
               danger=True, group_break=slot.enhanced_filter is None),
        *_browse_order_buttons(slot),
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
    control = slot.osr2_control
    return (
        Button("robot_hand_toggle_cruise", "cc",
               "Cruise control: vary the motion hands-free", lit=slot.cruise),
        Button("robot_hand_toggle_learned", "lm",
               "Learned motion: play what real scripts do, not a waveform", lit=slot.learned),
        Button("robot_hand_cycle_shape", WAVE_ICON, f"Waveform: {shape_label(slot.shape)}"),
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
        *((
            Button("main_player_funscript_jump", FUNSCRIPT_JUMP_ICON,
                   "Skip ahead to where this video's scripting starts up again",
                   group_break=True),
        ) if main_player_displays(slot.mode) else ()),
    )

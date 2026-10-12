"""What the main console shows about the room, and the file it reaches on: a
:class:`~funestra.console.ConsoleModel` carrying the buttons Fun Time
declares (:mod:`fun_time.console_buttons`), published as text and parsed back
by ``funestra.console``."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from funestra.console import OSR2_DRIVING, ConsoleModel
from funestra.hud_placement import HudCorner, HudEdge
from funestra.modes import LengthMode, MainMode, Osr2State
from funestra.robot_hand import FULL_INTENSITY

from main_funestra.controls import longer_than_a_step

from .console_buttons import MainSlot, console_rows, osr2_controls, osr2_rows
from .funestra_status import GenauStatus, MainFunestraStatus
from .mode_plan import kino_shows

MAIN_FUNESTRA_CONSOLE_FILENAME = "main_player_console.json"

def osr2_state(*, main_mode: MainMode, osr2_mode: str, funscript_driving: bool) -> Osr2State:
    """Which of the OSR2 states has the device, for the console to badge.

    Only a Main Funestra that is *on screen* can be driving: ``funscript_driving`` is read
    off the Main Funestra's status file, which describes the video the Main Funestra is parked on whether or
    not it is playing, and in genau mode the Main Funestra is paused off screen with the last
    scripted video it showed still in that file.  Asked without the mode it said
    "funscript" all through a genau-mode session, which is the console's word for
    "something other than the Robot Hand has the device" — so every ± mark and
    draggable band on the drive readout went dead.
    """
    return device_word(osr2_mode, funscript_driving and kino_shows(main_mode))


def device_word(osr2_mode: str, funscript_driving: bool) -> Osr2State:
    if osr2_mode == "off":
        return Osr2State.OFF
    if osr2_mode == "auto":
        return Osr2State.AUTO
    if funscript_driving:
        return Osr2State.FUNSCRIPT
    return Osr2State.ROBOT_HAND


@dataclass(frozen=True)
class MainSlotInputs:
    """Everything the main console's panel is built from — the satellites'
    :class:`~fun_time.lock_hud.SatelliteInputs` for the main slot.  The lock and
    the browse order are one flag each, resolved to whichever Funestra is on it."""

    main_mode: MainMode
    main_funestra: MainFunestraStatus
    genau: GenauStatus
    active: bool
    osr2_mode: str
    broker: bool
    osr2_control: str = OSR2_DRIVING
    hud_corner: HudCorner = HudCorner.UPPER_LEFT
    hud_edge: HudEdge = HudEdge.LOWER
    hud_minimized: bool = False
    genau_pace_s: int = 0
    scripted_filter: bool = False
    latest: bool = False
    genau_latest: bool = False
    plays_vr: bool | None = None
    plays_flat: bool | None = None
    length_mode: LengthMode = LengthMode.MIXED
    genau_plays_vr: bool | None = None
    genau_plays_flat: bool | None = None
    nothing_to_reset: bool = False
    in_vr: bool = False
    crowned: bool = True
    has_osr2: bool = True
    max_intensity: int = FULL_INTENSITY


def console_model(inputs: MainSlotInputs) -> ConsoleModel:
    """The console panel as the Main Funestra parses it: the room around the
    drive readout, and the buttons declared from it."""
    main_funestra, genau = inputs.main_funestra, inputs.genau
    video = kino_shows(inputs.main_mode)
    slot = MainSlot(
        main_mode=inputs.main_mode,
        locked=main_funestra.locked if video else genau.locked,
        scripted_filter=inputs.scripted_filter,
        latest=inputs.latest if video else inputs.genau_latest,
        loop_state=main_funestra.loop_state,
        cruise=genau.cruise_active,
        learned=genau.learned_active,
        shape=genau.shape,
        plays_vr=inputs.plays_vr if video else inputs.genau_plays_vr,
        plays_flat=inputs.plays_flat if video else inputs.genau_plays_flat,
        pace_s=inputs.genau_pace_s,
        length_mode=inputs.length_mode,
        compilation=main_funestra.compilation,
        has_compilation=main_funestra.has_compilation,
        has_other_versions=main_funestra.has_other_versions,
        longer_than_a_step=longer_than_a_step(main_funestra.duration_ms),
        jump_to=main_funestra.jump_to,
        osr2_control=inputs.osr2_control,
        nothing_to_reset=inputs.nothing_to_reset,
        flipped=genau.flipped,
        crowned=inputs.crowned,
        has_osr2=inputs.has_osr2,
    )
    return ConsoleModel(
        main_mode=inputs.main_mode,
        hud_corner=inputs.hud_corner,
        hud_edge=inputs.hud_edge,
        hud_minimized=inputs.hud_minimized,
        active=inputs.active,
        osr2=osr2_state(main_mode=inputs.main_mode, osr2_mode=inputs.osr2_mode,
                        funscript_driving=main_funestra.funscript_driving),
        osr2_control=slot.osr2_control,  # beside what has the device, what is DONE to it
        locked=slot.locked,
        latest=slot.latest,
        rows=console_rows(slot, in_vr=inputs.in_vr),
        osr2_rows=osr2_rows(slot),
        osr2_controls=osr2_controls(broker=inputs.broker) if inputs.has_osr2 else (),
        has_osr2=inputs.has_osr2,
        max_intensity=inputs.max_intensity,
    )


def main_funestra_console_path(state_dir: Path) -> Path:
    return state_dir / MAIN_FUNESTRA_CONSOLE_FILENAME

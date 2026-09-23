"""What the main console shows about the room, and the file it reaches on: a
:class:`~player_core.console.ConsoleModel` carrying the buttons Fun Time
declares (:mod:`fun_time.console_buttons`), published as text and parsed back
by ``player_core.console``."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from player_core.console import OSR2_DRIVING, ConsoleModel
from player_core.modes import MainMode, Osr2State

from .console_buttons import MainSlot, console_rows, osr2_controls
from .mode_plan import main_player_displays
from .player_status import GenauStatus, MainPlayerStatus

MAIN_PLAYER_CONSOLE_FILENAME = "main_player_console.json"

def osr2_state(*, main_mode: MainMode, osr2_mode: str, funscript_driving: bool) -> Osr2State:
    """Which of the OSR2 states has the device, for the console to badge.

    Only a main player that is *on screen* can be driving: ``funscript_driving`` is read
    off the main player's status file, which describes the video the main player is parked on whether or
    not it is playing, and in genau mode the main player is paused off screen with the last
    scripted video it showed still in that file.  Asked without the mode it said
    "funscript" all through a genau-mode session, which is the console's word for
    "something other than the Robot Hand has the device" — so every ± mark and
    draggable band on the drive readout went dead.
    """
    if osr2_mode == "off":
        return Osr2State.OFF
    if osr2_mode == "auto":
        return Osr2State.AUTO
    if funscript_driving and main_player_displays(main_mode):
        return Osr2State.FUNSCRIPT
    return Osr2State.ROBOT_HAND


@dataclass(frozen=True)
class MainSlotInputs:
    """Everything the main console's panel is built from — the satellites'
    :class:`~fun_time.lock_hud.SatelliteInputs` for the main slot.  The lock and
    the browse order are one flag each, resolved to whichever player is on it."""

    main_mode: MainMode
    main_player: MainPlayerStatus
    genau: GenauStatus
    active: bool
    osr2_mode: str
    broker: bool
    osr2_control: str = OSR2_DRIVING
    genau_pace_s: int = 0
    scripted_filter: bool = False
    latest: bool = False
    genau_latest: bool = False
    plays_vr: bool | None = None
    plays_flat: bool | None = None
    nothing_to_reset: bool = False
    in_vr: bool = False


def console_model(inputs: MainSlotInputs) -> ConsoleModel:
    """The console panel as the main player parses it: the room around the
    drive readout, and the buttons declared from it."""
    main_player, genau = inputs.main_player, inputs.genau
    video = main_player_displays(inputs.main_mode)
    slot = MainSlot(
        main_mode=inputs.main_mode,
        locked=main_player.locked if video else genau.locked,
        scripted_filter=inputs.scripted_filter,
        latest=inputs.latest if video else inputs.genau_latest,
        loop_state=main_player.loop_state,
        cruise=genau.cruise_active,
        learned=genau.learned_active,
        shape=genau.shape,
        plays_vr=inputs.plays_vr,
        plays_flat=inputs.plays_flat,
        pace_s=inputs.genau_pace_s,
        length_mode=main_player.length_mode,
        compilation=main_player.compilation,
        has_compilation=main_player.has_compilation,
        has_other_versions=main_player.has_other_versions,
        jump_to=main_player.jump_to,
        osr2_control=inputs.osr2_control,
        nothing_to_reset=inputs.nothing_to_reset,
        flipped=genau.flipped,
    )
    return ConsoleModel(
        main_mode=inputs.main_mode,
        active=inputs.active,
        osr2=osr2_state(main_mode=inputs.main_mode, osr2_mode=inputs.osr2_mode,
                        funscript_driving=main_player.funscript_driving),
        osr2_control=slot.osr2_control,  # beside what has the device, what is DONE to it
        locked=slot.locked,
        latest=slot.latest,
        rows=console_rows(slot, in_vr=inputs.in_vr),
        osr2_controls=osr2_controls(broker=inputs.broker),
    )


def main_player_console_path(state_dir: Path) -> Path:
    return state_dir / MAIN_PLAYER_CONSOLE_FILENAME

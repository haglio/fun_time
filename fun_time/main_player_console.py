"""What the main console shows about the room, and the file it reaches on: a
:class:`~player_core.console.ConsoleModel` carrying the buttons Fun Time
declares (:mod:`fun_time.console_buttons`), published as text and parsed back
by ``player_core.console``."""
from __future__ import annotations

from pathlib import Path

from player_core.console import OSR2_DRIVING, ConsoleModel
from player_core.modes import MainMode, Osr2State

from .console_buttons import MainSlot, console_rows, osr2_controls
from .mode_plan import main_player_displays
from .player_status import GenauStatus, MainPlayerStatus

MAIN_PLAYER_CONSOLE_FILENAME = "main_player_console.json"

# What has the OSR2, as one compact word the console badges.  Off and auto are the
# device's own modes; otherwise it comes down to whether a funscript is actually
# *driving* right now — not merely present, so a scripted video's quiet stretch,
# where the Robot Hand fills in, reads as the hand rather than as its funscript,
# and not merely loaded, so a main player paused off screen in genau mode drives nothing.


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


def console_model(
    *,
    main_mode: MainMode,
    active: bool,
    osr2_mode: str,
    broker: bool,
    main_player: MainPlayerStatus,
    osr2_control: str = OSR2_DRIVING,
    genau: GenauStatus,
    genau_pace_s: int = 0,
    scripted_filter: bool = False,
    latest: bool = False,
    genau_latest: bool = False,
    plays_vr: bool | None = None,
    plays_flat: bool | None = None,
    nothing_to_reset: bool = False,
) -> ConsoleModel:
    """The console panel as the main player parses it: the room around the
    drive readout, and the buttons declared from it.  The lock and the browse
    order are one flag each, resolved to whichever player is on the main slot;
    the shape flags are the headset's filter, None where the rotation holds one
    shape; *genau_pace_s* is Genau's clip pace, off its drive readout."""
    video = main_player_displays(main_mode)
    slot = MainSlot(
        main_mode=main_mode,
        locked=main_player.locked if video else genau.locked,
        scripted_filter=scripted_filter,
        latest=latest if video else genau_latest,
        loop_state=main_player.loop_state,
        cruise=genau.cruise_active,
        learned=genau.learned_active,
        shape=genau.shape,
        plays_vr=plays_vr,
        plays_flat=plays_flat,
        pace_s=genau_pace_s,
        length_mode=main_player.length_mode,
        compilation=main_player.compilation,
        has_compilation=main_player.has_compilation,
        has_other_versions=main_player.has_other_versions,
        jump_to=main_player.jump_to,
        osr2_control=osr2_control,
        nothing_to_reset=nothing_to_reset,
    )
    return ConsoleModel(
        main_mode=main_mode,
        active=active,
        osr2=osr2_state(main_mode=main_mode, osr2_mode=osr2_mode,
                        funscript_driving=main_player.funscript_driving),
        osr2_control=slot.osr2_control,  # beside what has the device, what is DONE to it
        locked=slot.locked,
        latest=slot.latest,
        rows=console_rows(slot),
        osr2_controls=osr2_controls(broker=broker),
    )


def main_player_console_path(state_dir: Path) -> Path:
    return state_dir / MAIN_PLAYER_CONSOLE_FILENAME

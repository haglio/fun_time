"""What the main console shows about the room, and the file it reaches on.

The player on the main slot knows what it is playing.  It does not know which
mode the slot is in, what has the OSR2, whether the broker is up, or which player
a bare command would reach — all of that is the orchestrator's.  So this is what
reaches the player for its console to be drawable: a small JSON panel, published
the way each satellite's map is, and read back by ``nau.console``.
"""
from __future__ import annotations

from pathlib import Path

from .mode_plan import nau_displays
from .player_status import GenauStatus

NAU_CONSOLE_FILENAME = "nau_console.json"

# What has the OSR2, as one compact word the console badges.  Off and auto are the
# device's own modes; otherwise it comes down to whether a funscript is actually
# *driving* right now — not merely present, so a scripted video's quiet stretch,
# where the Robot Hand fills in, reads as the hand rather than as its funscript,
# and not merely loaded, so a Nau paused off screen in genau mode drives nothing.
OSR2_OFF = "off"
OSR2_AUTO = "auto"
OSR2_FUNSCRIPT = "funscript"
OSR2_ROBOT_HAND = "robot_hand"


def osr2_state(*, mode: str, osr2_mode: str, funscript_driving: bool) -> str:
    """Which of the OSR2 states has the device, for the console to badge.

    Only a Nau that is *on screen* can be driving: ``funscript_driving`` is read
    off Nau's status file, which describes the video Nau is parked on whether or
    not it is playing, and in genau mode Nau is paused off screen with the last
    scripted video it showed still in that file.  Asked without the mode it said
    "funscript" all through a genau-mode session, which is the console's word for
    "something other than the Robot Hand has the device" — so every ± mark and
    draggable band on the drive readout went dead.
    """
    if osr2_mode == "off":
        return OSR2_OFF
    if osr2_mode == "auto":
        return OSR2_AUTO
    if funscript_driving and nau_displays(mode):
        return OSR2_FUNSCRIPT
    return OSR2_ROBOT_HAND


def console_payload(
    *,
    mode: str,
    active: bool,
    osr2_mode: str,
    funscript_driving: bool,
    broker: bool,
    record: str = "normal",
    nau_locked: bool = True,
    genau: GenauStatus,
    f_mode: bool = False,
    latest: bool = False,
    genau_latest: bool = False,
    plays_vr: bool | None = None,
    plays_flat: bool | None = None,
) -> dict:
    """The console panel as the main player parses it.

    The drive readout's own numbers (amplitude, center, speed, the trace and its
    limits) travel on the separate drive file Genau publishes; this carries the
    room around them.

    *record* is Nau's own loop machine (normal / recording / looping), and rides
    here because the console is drawn in genau mode too, by a player with no loop
    machine to ask — and because Nau already tells us in its status file.

    The lock is published as one flag for one padlock, resolved to whichever
    player is on the main slot: *nau_locked* while Nau shows its video, Genau's
    own hold on its clip in genau mode.  Both players open locked, both mean
    repeat-one on what is on screen, and the console shows one of them at a time —
    so the mode decides which, here, rather than the console drawing two padlocks
    and leaving the reader to work out whose is whose.

    The browse order is one flag for one slot in the same way, and resolved the
    same way: *latest* is Nau's playlist order, *genau_latest* the order Genau
    last rescanned its clips folder in, and the mode says which of them the
    console is describing.  Published for the same reason ``f_mode`` is — the
    order is the orchestrator's, set by a spoken word or a key it owns, and
    neither player can tell which way round the browse it is walking was built.

    ``f_mode`` is the main player's own F-mode.  Nau is told the flag directly too
    (``SET_F_MODE``, for its status line), but the console's button has to light
    off what the orchestrator holds, exactly as the satellites' do: the flag is
    set from three places at once and only one of them is the player.

    The two shape flags are the same for the headset's filter, with a third
    answer: None where the rotation holds one shape, which draws no pair at all.
    """
    return {
        "mode": mode,
        "active": active,
        "f_mode": f_mode,
        "latest": latest if nau_displays(mode) else genau_latest,
        "osr2": osr2_state(mode=mode, osr2_mode=osr2_mode,
                           funscript_driving=funscript_driving),
        "broker": broker,
        "record": record,
        # The hold of whichever player owns the slot, bounced back off its status
        # file: the console draws the padlock, and the player drawing that console
        # is not always the player it is about.
        "locked": nau_locked if nau_displays(mode) else genau.locked,
        "cruise": genau.cruise_active,
        "shape": genau.shape,
        "plays_vr": plays_vr,
        "plays_flat": plays_flat,
    }


def nau_console_path(state_dir: Path) -> Path:
    return state_dir / NAU_CONSOLE_FILENAME

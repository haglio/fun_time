"""What the main console shows about the room, and the file it reaches on.

The player on the main slot knows what it is playing.  It does not know which
mode the slot is in, what has the OSR2, whether Genau may take it over, whether
the broker is up, or which player a bare command would reach — all of that is the
orchestrator's.  The dashboard used to draw it as a box per player; the console
draws it now, so this is what has to reach the player for that console to be
drawable: a small JSON panel, published the way each satellite's map is (see
:mod:`fun_time.hud_transport`), and read back by ``nau.console``.
"""
from __future__ import annotations

from pathlib import Path

from .dashboard_runtime import GenauStatus
from .mode_plan import genau_active, nau_displays

NAU_CONSOLE_FILENAME = "nau_console.json"

# What has the OSR2, as one compact word the console boxes.  Off and auto are the
# device's own modes; otherwise it comes down to whether a funscript is actually
# *driving* right now — not merely present, so a scripted video's quiet stretch,
# where Genau fills in, reads as Genau rather than as its funscript.
OSR2_OFF = "off"
OSR2_AUTO = "auto"
OSR2_FUNSCRIPT = "funscript"
OSR2_GENAU = "genau"
OSR2_IDLE = "idle"


def osr2_state(*, mode: str, osr2_mode: str, funscript_driving: bool) -> str:
    """Which of the OSR2 states has the device, for the console to box."""
    if osr2_mode == "off":
        return OSR2_OFF
    if osr2_mode == "auto":
        return OSR2_AUTO
    if funscript_driving:
        return OSR2_FUNSCRIPT
    return OSR2_GENAU if genau_active(mode) else OSR2_IDLE


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

    ``f_mode`` is the main player's own F-mode — its playlist narrowed to the videos
    that have a funscript.  Nau is told the flag directly too (``SET_F_MODE``, for
    its status line), but the console's button has to light off what the
    orchestrator holds, exactly as the satellites' do: the flag is set from three
    places at once and only one of them is the player.
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
    }


def nau_console_path(state_dir: Path) -> Path:
    return state_dir / NAU_CONSOLE_FILENAME

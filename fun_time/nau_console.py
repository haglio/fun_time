"""What the primary console shows about the room, and the file it reaches on.

The player on the primary slot knows what it is playing.  It does not know which
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
from .mode_plan import genau_active

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
    genau: GenauStatus,
) -> dict:
    """The console panel as the primary player parses it.

    The drive readout's own numbers (amplitude, centre, speed, the trace and its
    limits) travel on the separate drive file Genau publishes; this carries the
    room around them.
    """
    return {
        "mode": mode,
        "active": active,
        "osr2": osr2_state(mode=mode, osr2_mode=osr2_mode,
                           funscript_driving=funscript_driving),
        "broker": broker,
        "cruise": genau.cruise_active,
        # Auto advance is armed apart from cruise — cruise varies the stroke, auto
        # advance moves on to the next clip — and a held clip is it armed but
        # sitting still, which the console lights as its own state.
        "auto_advance": genau.auto_advance_active,
        "clip_locked": genau.clip_locked,
        "shape": genau.shape,
    }


def nau_console_path(state_dir: Path) -> Path:
    return state_dir / NAU_CONSOLE_FILENAME

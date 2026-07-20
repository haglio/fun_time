"""What Nau's console shows about the room, and the file it reaches Nau on.

Nau knows what it is playing.  It does not know which mode the primary slot is
in, what has the OSR2, whether Genau may take it over, or where Genau's controls
have run out of range — all of that is the orchestrator's, and the dashboard used
to draw it as a box per player on a schematic of the two monitors.

The controls that went with it now live on Nau's own HUD, so this is what has to
reach Nau for that HUD to be drawable: a small JSON panel, published exactly the
way each satellite's map is (see :mod:`fun_time.hud_transport`), and read back by
``nau.console`` in the genau repo.
"""
from __future__ import annotations

from pathlib import Path

from .dashboard_runtime import GenauStatus
from .modes import has_matching_funscript

NAU_CONSOLE_FILENAME = "nau_console.json"

# What the OSR2 line says, by what actually has the device.  The dashboard said
# this in a box of its own with a cable drawn to the primary player; on the HUD
# it is a line, because a cable between two things drawn in the same panel says
# nothing.
OSR2_OFF = "OSR2 · off"
OSR2_AUTO = "OSR2 · auto"
OSR2_FUNSCRIPT = "OSR2 · funscript control"
OSR2_GENAU = "OSR2 · Genau"
OSR2_IDLE = "OSR2 · idle, no funscript"

# Genau's controls, and the status flag that says each end of each range is
# reached.  The console greys a control out at its limit, so these are the names
# it greys them out by.
_LIMITS = (
    ("amp_max", "amp_at_max"), ("amp_min", "amp_at_min"),
    ("ctr_max", "ctr_at_max"), ("ctr_min", "ctr_at_min"),
    ("spd_max", "spd_at_max"), ("spd_min", "spd_at_min"),
)


def osr2_label(*, mode: str, osr2_mode: str, primary_path: str) -> str:
    """What has the device, in the words the console prints.

    Off and auto are the device's own modes and answer regardless.  Otherwise it
    comes down to whether the video on screen has a funscript to drive from — and,
    when it does not, whether Genau is there to take over, which is what makes
    "idle" mean idle rather than "Genau has it".
    """
    if osr2_mode == "off":
        return OSR2_OFF
    if osr2_mode == "auto":
        return OSR2_AUTO
    if primary_path and has_matching_funscript(primary_path):
        return OSR2_FUNSCRIPT
    return OSR2_GENAU if mode in ("genau", "hybrid") else OSR2_IDLE


def console_payload(
    *,
    mode: str,
    osr2_mode: str,
    primary_path: str,
    takeover_allowed: bool,
    genau: GenauStatus,
) -> dict:
    """The console panel as Nau parses it."""
    return {
        "mode": mode,
        "osr2": osr2_label(mode=mode, osr2_mode=osr2_mode, primary_path=primary_path),
        "takeover_allowed": takeover_allowed,
        "cruise": genau.cruise_active,
        "shape": genau.shape,
        "limits": sorted(name for name, flag in _LIMITS if getattr(genau, flag)),
    }


def nau_console_path(state_dir: Path) -> Path:
    return state_dir / NAU_CONSOLE_FILENAME

from __future__ import annotations

from dataclasses import dataclass

from .broker_control import PARK_CMD, RESUME_CMD, RETRACT_CMD
from .mode_plan import genau_active, nau_displays


@dataclass(frozen=True)
class OmniPausePlan:
    action: str
    next_omni_paused: bool
    genau_branch: bool
    resume_nau_playback: bool
    # Where this leaves the OSR2: parked home on a plain enter, retracted away
    # on a relief enter, back on the script feed on a leave.
    broker_command: str
    log_message: str
    # Whether leaving may resume Genau's stroke outright.  Not in hybrid: there
    # the per-video arbiter owns which of Genau and the funscript has the device,
    # and a blanket resume here started Genau against a funscript that was still
    # driving — two drivers on the OSR2 at once until the next arbiter tick.
    resume_genau_playback: bool = False


def build_omnipause_plan(action: str, *, omni_paused: bool, primary_mode: str) -> OmniPausePlan:
    """Decide what one omnipause action means.

    ``toggle`` resolves against the current state; ``enter`` and ``leave`` are
    that decision already made.  ``relief`` is an enter that sends the OSR2 to
    the far end of its stroke rather than home — the sensation emergency, where
    the device has to be off the user rather than merely still.
    """
    if action == "toggle":
        action = "leave" if omni_paused else "enter"

    if action in ("enter", "relief"):
        retract = action == "relief"
        return OmniPausePlan(
            action=action,
            next_omni_paused=True,
            genau_branch=genau_active(primary_mode),
            resume_nau_playback=False,
            broker_command=RETRACT_CMD if retract else PARK_CMD,
            log_message=(
                "OmniPause: entering (relief — retracting the OSR2)"
                if retract
                else "OmniPause: entering"
            ),
        )

    if action == "leave":
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            genau_branch=genau_active(primary_mode),
            # Nau owns the display in nau and hybrid, so leaving omnipause
            # resumes its playback there (in genau mode Genau owns the display).
            resume_nau_playback=nau_displays(primary_mode),
            # Only genau mode, where Genau always has the device.  In hybrid the
            # arbiter re-asserts the driver on its next tick, and resuming Genau
            # here would race it onto a funscript's stretch.
            resume_genau_playback=primary_mode == "genau",
            broker_command=RESUME_CMD,
            log_message="OmniPause: leaving",
        )

    raise ValueError(f"Unsupported omnipause action: {action}")

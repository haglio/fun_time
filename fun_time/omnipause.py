from __future__ import annotations

from dataclasses import dataclass

from app_support import osr2_hold
from funestra.console import HOLD_VERB, OSR2_DRIVING

from .mode_plan import MAIN_GENAU_MODE, kino_shows
from .room_funestras import RoomFunestra


@dataclass(frozen=True)
class OmniPausePlan:
    action: str
    next_omni_paused: bool
    resume_main_funestra_playback: bool
    # Where this leaves the OSR2: parked home on an enter; on a leave, back on
    # the script feed, or still where a park or retract is holding it.
    broker_command: str
    log_message: str
    # Whether leaving may resume the Robot Hand outright.  Not where the arbiter
    # owns which of the hand and a funscript has the device -- kino mode, or a
    # side Funestra holding the OSR2: a blanket resume there started the hand
    # against a funscript still driving, two drivers on the OSR2 at once.
    resume_genau_playback: bool = False


def build_omnipause_plan(action: str, *, omni_paused: bool, main_mode: str,
                        osr2_control: str = OSR2_DRIVING,
                        osr2_funestra: RoomFunestra = RoomFunestra.MAIN) -> OmniPausePlan:
    """Decide what one omnipause action means.

    ``toggle`` resolves against the current state; ``enter`` and ``leave`` are
    that decision already made.
    """
    if action == "toggle":
        action = "leave" if omni_paused else "enter"

    if action == "enter":
        return OmniPausePlan(
            action=action,
            next_omni_paused=True,
            resume_main_funestra_playback=False,
            broker_command=osr2_hold.PARK,
            log_message="OmniPause: entering",
        )

    if action == "leave":
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            # The Main Funestra owns the display in kino mode, so leaving omnipause
            # resumes its playback there (in genau mode Genau owns the display).
            resume_main_funestra_playback=kino_shows(main_mode),
            resume_genau_playback=(main_mode == MAIN_GENAU_MODE
                                   and osr2_funestra is RoomFunestra.MAIN),
            broker_command=HOLD_VERB.get(osr2_control, osr2_hold.RESUME),
            log_message="OmniPause: leaving",
        )

    raise ValueError(f"Unsupported omnipause action: {action}")

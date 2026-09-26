from __future__ import annotations

from dataclasses import dataclass

from player_core.console import OSR2_CONTROL_OFF, OSR2_DRIVING

from .broker_control import HOLD_VERB, PARK_CMD, RESUME_CMD
from .mode_plan import MAIN_GENAU_MODE, main_player_displays
from .players import Player


@dataclass(frozen=True)
class OmniPausePlan:
    action: str
    next_omni_paused: bool
    resume_main_player_playback: bool
    # Where this leaves the OSR2: parked home on an enter; on a leave, back on
    # the script feed, or still where a park or retract is holding it.
    broker_command: str
    log_message: str
    # Whether leaving may resume the Robot Hand outright.  Not where the arbiter
    # owns which of the hand and a funscript has the device -- video mode, or a
    # side player holding the OSR2: a blanket resume there started the hand
    # against a funscript still driving, two drivers on the OSR2 at once.
    resume_genau_playback: bool = False


def build_omnipause_plan(action: str, *, omni_paused: bool, main_mode: str,
                        osr2_control: str = OSR2_DRIVING,
                        osr2_player: Player = Player.MAIN) -> OmniPausePlan:
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
            resume_main_player_playback=False,
            broker_command=PARK_CMD,
            log_message="OmniPause: entering",
        )

    if action == "leave":
        return OmniPausePlan(
            action="leave",
            next_omni_paused=False,
            # The main player owns the display in video mode, so leaving omnipause
            # resumes its playback there (in genau mode Genau owns the display).
            resume_main_player_playback=main_player_displays(main_mode),
            resume_genau_playback=(main_mode == MAIN_GENAU_MODE
                                   and osr2_control != OSR2_CONTROL_OFF
                                   and osr2_player is Player.MAIN),
            broker_command=HOLD_VERB.get(osr2_control, RESUME_CMD),
            log_message="OmniPause: leaving",
        )

    raise ValueError(f"Unsupported omnipause action: {action}")

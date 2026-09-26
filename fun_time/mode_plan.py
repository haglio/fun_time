from __future__ import annotations

from dataclasses import dataclass

from player_core.modes import MainMode
from player_core.player_verbs import DISPLAY_OFF, DISPLAY_ON

# The main slot's two modes.  In both the Robot Hand is at work: in genau mode it
# drives the OSR2 outright under Genau's clips, and in video mode the arbiter
# hands the device between it and the video's funscript.  The axis is in the
# name because the satellites' own "video" mode is that same string, and
# unprefixed the two were one name inside command_dispatch, which handles both.
MAIN_VIDEO_MODE = MainMode.VIDEO
MAIN_GENAU_MODE = MainMode.GENAU
MAIN_MODES: tuple[MainMode, ...] = (MAIN_VIDEO_MODE, MAIN_GENAU_MODE)

# The mode every session is BUILT in, whatever it opens in: the defaults
# everywhere — flag files, window bands, a fresh BridgeState — are this one's.
STARTUP_MAIN_MODE = MAIN_VIDEO_MODE


@dataclass(frozen=True)
class ModeSwitchPlan:
    target_mode: MainMode
    is_transition: bool
    genau_cmd: str | None
    hud_cmd: str | None
    main_player_should_play: bool | None
    # Distinct from main_player_should_play: a paused main player still holds the frame it
    # stopped on, and the idle main-slot player is minimized rather than hidden,
    # so an alt-tab back to it lands on that frame unless it is blanked.
    main_player_display_cmd: str | None
    log_message: str


def main_player_displays(mode: MainMode) -> bool:
    """Return True if the main player owns the on-screen display (and its interaction)."""
    return mode == MAIN_VIDEO_MODE


def hud_verb(mode: MainMode) -> str:
    """What Genau's window is in *mode*: the HUD layer over the main player, or the display."""
    return "HUD_ON" if main_player_displays(mode) else "HUD_OFF"


def main_player_display_verb(mode: MainMode) -> str:
    """Whether the main player paints in *mode* — :func:`hud_verb`'s mirror."""
    return DISPLAY_ON if main_player_displays(mode) else DISPLAY_OFF


def build_mode_switch_plan(
    *,
    current_mode: MainMode,
    target_mode: MainMode,
    omni_paused: bool,
) -> ModeSwitchPlan:
    """Plan a switch between the main slot's modes; refuse any other."""
    for mode in (current_mode, target_mode):
        if mode not in MAIN_MODES:
            raise ValueError(f"Not a main-slot mode: {mode!r}")
    if current_mode == target_mode:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            genau_cmd=None,
            hud_cmd=None,
            main_player_should_play=None,
            main_player_display_cmd=None,
            log_message=f"Already in {target_mode} mode",
        )

    return ModeSwitchPlan(
        target_mode=target_mode,
        is_transition=True,
        genau_cmd=None if omni_paused else "RESUME",
        hud_cmd=None if main_player_displays(target_mode) else hud_verb(target_mode),
        main_player_should_play=None if omni_paused else main_player_displays(target_mode),
        main_player_display_cmd=(main_player_display_verb(target_mode)
                                 if main_player_displays(target_mode) else None),
        log_message=f"Switched to {target_mode} mode",
    )

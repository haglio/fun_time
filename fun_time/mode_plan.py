from __future__ import annotations

from dataclasses import dataclass

# The mode every session is BUILT in, whatever it opens in: the defaults
# everywhere — flag files, window bands, a fresh BridgeState — are this one's.
STARTUP_MAIN_MODE = "video"

# The main slot's two modes.  In both the Robot Hand is at work: in genau mode it
# drives the OSR2 outright under Genau's clips, and in video mode the arbiter
# hands the device between it and the video's funscript.
VIDEO_MODE = "video"


@dataclass(frozen=True)
class ModeSwitchPlan:
    target_mode: str
    is_transition: bool
    # RESUME on every transition: in genau mode Genau drives from here, and in
    # video mode the arbiter takes it from here.
    genau_cmd: str | None
    hud_cmd: str | None
    nau_should_play: bool | None
    # Distinct from nau_should_play: a paused Nau still holds the frame it
    # stopped on, and the idle main-slot player is minimized rather than hidden,
    # so an alt-tab back to it lands on that frame unless it is blanked.
    nau_display_cmd: str | None
    log_message: str


def nau_displays(mode: str) -> bool:
    """Return True if Nau owns the on-screen display (and its interaction)."""
    return mode == VIDEO_MODE


def hud_verb(mode: str) -> str:
    """What Genau's window is in *mode*: the HUD layer over Nau, or the display."""
    return "HUD_ON" if nau_displays(mode) else "HUD_OFF"


def nau_display_verb(mode: str) -> str:
    """Whether Nau paints in *mode* — :func:`hud_verb`'s mirror."""
    return "DISPLAY_ON" if nau_displays(mode) else "DISPLAY_OFF"


def build_mode_switch_plan(
    *,
    current_mode: str,
    target_mode: str,
    omni_paused: bool,
) -> ModeSwitchPlan:
    """Plan a switch between the main slot's modes: video and genau."""
    if current_mode == target_mode:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            genau_cmd=None,
            hud_cmd=None,
            nau_should_play=None,
            nau_display_cmd=None,
            log_message=f"Already in {target_mode} mode",
        )

    if omni_paused:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            genau_cmd=None,
            hud_cmd=None,
            nau_should_play=None,
            nau_display_cmd=None,
            log_message=f"Mode set to {target_mode} (omnipaused)",
        )

    return ModeSwitchPlan(
        target_mode=target_mode,
        is_transition=True,
        genau_cmd="RESUME",
        hud_cmd=hud_verb(target_mode),
        nau_should_play=nau_displays(target_mode),
        nau_display_cmd=nau_display_verb(target_mode),
        log_message=f"Switched to {target_mode} mode",
    )

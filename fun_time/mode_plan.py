from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeSwitchPlan:
    target_mode: str
    is_transition: bool
    genau_cmd: str | None
    hud_cmd: str | None
    vlc_should_play: bool | None
    nau_should_play: bool | None
    log_message: str


def genau_active(mode: str) -> bool:
    """Return True if Genau should receive commands in this mode."""
    return mode in ("genau", "hybrid")


def vlc_primary_active(mode: str) -> bool:
    """Return True if the primary VLC is a playing surface in this mode."""
    return mode in ("vlc", "hybrid")


def build_mode_switch_plan(
    *,
    current_mode: str,
    target_mode: str,
    omni_paused: bool,
) -> ModeSwitchPlan:
    """Plan a switch between the primary modes: nau, genau, hybrid.

    Nau plays exactly when the mode is nau; Genau runs in genau and hybrid;
    the primary VLC plays exactly when the mode is hybrid (it exists only to
    display video under Genau's HUD).
    """
    if current_mode == target_mode:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            genau_cmd=None,
            hud_cmd=None,
            vlc_should_play=None,
            nau_should_play=None,
            log_message=f"Already in {target_mode} mode",
        )

    if omni_paused:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            genau_cmd=None,
            hud_cmd=None,
            vlc_should_play=None,
            nau_should_play=None,
            log_message=f"Mode set to {target_mode} (omnipaused)",
        )

    wasgenau_active = genau_active(current_mode)
    willgenau_active = genau_active(target_mode)

    genau_cmd: str | None = None
    if willgenau_active and not wasgenau_active:
        genau_cmd = "RESUME"
    elif not willgenau_active and wasgenau_active:
        genau_cmd = "PAUSE"

    hud_cmd: str | None = None
    if target_mode == "hybrid":
        hud_cmd = "HUD_ON"
    elif current_mode == "hybrid":
        hud_cmd = "HUD_OFF"

    vlc_should_play: bool | None = None
    if target_mode == "hybrid":
        vlc_should_play = True
    elif current_mode == "hybrid":
        vlc_should_play = False

    nau_should_play: bool | None = None
    if target_mode == "nau":
        nau_should_play = True
    elif current_mode == "nau":
        nau_should_play = False

    return ModeSwitchPlan(
        target_mode=target_mode,
        is_transition=True,
        genau_cmd=genau_cmd,
        hud_cmd=hud_cmd,
        vlc_should_play=vlc_should_play,
        nau_should_play=nau_should_play,
        log_message=f"Switched to {target_mode} mode",
    )

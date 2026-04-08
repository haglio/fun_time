from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeSwitchPlan:
    target_mode: str
    is_transition: bool
    genau_cmd: str | None
    hud_cmd: str | None
    vlc_should_play: bool | None
    log_message: str


def genau_active(mode: str) -> bool:
    """Return True if Genau should receive commands in this mode."""
    return mode in ("genau", "hybrid")


def build_mode_switch_plan(
    *,
    current_mode: str,
    target_mode: str,
    omni_paused: bool,
) -> ModeSwitchPlan:
    if current_mode == target_mode:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            genau_cmd=None,
            hud_cmd=None,
            vlc_should_play=None,
            log_message=f"Already in {target_mode} mode",
        )

    if omni_paused:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            genau_cmd=None,
            hud_cmd=None,
            vlc_should_play=None,
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
    if target_mode == "hybrid" and current_mode != "hybrid":
        hud_cmd = "HUD_ON"
    elif current_mode == "hybrid" and target_mode != "hybrid":
        hud_cmd = "HUD_OFF"

    vlc_should_play: bool | None = None
    if target_mode == "genau" and current_mode != "genau":
        vlc_should_play = False
    elif target_mode in ("vlc", "hybrid") and current_mode == "genau":
        vlc_should_play = True

    return ModeSwitchPlan(
        target_mode=target_mode,
        is_transition=True,
        genau_cmd=genau_cmd,
        hud_cmd=hud_cmd,
        vlc_should_play=vlc_should_play,
        log_message=f"Switched to {target_mode} mode",
    )

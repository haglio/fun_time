from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeSwitchPlan:
    target_mode: str
    is_transition: bool
    genau_cmd: str | None
    hud_cmd: str | None
    nau_should_play: bool | None
    log_message: str
    # Leaving hybrid re-enables Nau's funscript T-Code: the per-video arbiter
    # mutes it during funscript gaps, so nau mode would otherwise inherit a
    # muted Nau.  Entering/within hybrid is the arbiter's job, not the plan's.
    reenable_nau_tcode: bool = False


def genau_active(mode: str) -> bool:
    """Return True if Genau drives the OSR2 (and shows its HUD) in this mode."""
    return mode in ("genau", "hybrid")


def nau_displays(mode: str) -> bool:
    """Return True if Nau owns the on-screen display (and its interaction).

    Nau is the primary player in both nau and hybrid; in hybrid Genau merely
    drives the OSR2 and paints its HUD over Nau's video.
    """
    return mode in ("nau", "hybrid")


def build_mode_switch_plan(
    *,
    current_mode: str,
    target_mode: str,
    omni_paused: bool,
) -> ModeSwitchPlan:
    """Plan a switch between the primary modes: nau, genau, hybrid.

    Nau owns the display in nau and hybrid; Genau drives the OSR2 and shows its
    HUD in genau and hybrid.  So Nau keeps playing across a nau<->hybrid switch
    and only starts or stops when the display actually returns to or leaves it.
    """
    if current_mode == target_mode:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            genau_cmd=None,
            hud_cmd=None,
            nau_should_play=None,
            log_message=f"Already in {target_mode} mode",
        )

    if omni_paused:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            genau_cmd=None,
            hud_cmd=None,
            nau_should_play=None,
            log_message=f"Mode set to {target_mode} (omnipaused)",
        )

    will_genau = genau_active(target_mode)

    # Assert Genau's driving state for the target authoritatively, not just on a
    # genau-active change: the per-video hybrid arbiter can leave Genau paused
    # mid-hybrid (a funscripted video was driving the OSR2), so a hybrid->genau
    # switch must RESUME even though both modes are genau-active.
    genau_cmd = "RESUME" if will_genau else "PAUSE"

    hud_cmd: str | None = None
    if target_mode == "hybrid":
        hud_cmd = "HUD_ON"
    elif current_mode == "hybrid":
        hud_cmd = "HUD_OFF"

    was_nau_display = nau_displays(current_mode)
    will_nau_display = nau_displays(target_mode)
    nau_should_play: bool | None = None
    if will_nau_display and not was_nau_display:
        nau_should_play = True
    elif was_nau_display and not will_nau_display:
        nau_should_play = False

    return ModeSwitchPlan(
        target_mode=target_mode,
        is_transition=True,
        genau_cmd=genau_cmd,
        hud_cmd=hud_cmd,
        nau_should_play=nau_should_play,
        log_message=f"Switched to {target_mode} mode",
        reenable_nau_tcode=current_mode == "hybrid" and target_mode != "hybrid",
    )

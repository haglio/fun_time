from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app_support.osr2_device import Osr2Mode, Osr2Trouble
from funestra.hud_controls import longer_than_a_step
from funestra.hud_placement import HudCorner, HudEdge
from funestra.main_hud import MainHudModel
from funestra.modes import LengthMode, MainMode, Osr2State

from .funestra_status import GenauStatus, MainFunestraStatus
from .main_hud_buttons import MainFunestraFacts, main_hud_rows, osr2_rows, title_filters
from .mode_plan import kino_shows

MAIN_FUNESTRA_HUD_FILENAME = "main_player_console.json"

def osr2_state(*, main_mode: MainMode, osr2_mode: Osr2Mode, funscript_driving: bool,
               trouble: Osr2Trouble | None = None) -> Osr2State:
    return Osr2State.of(osr2_mode, funscript_driving=funscript_driving and kino_shows(main_mode),
                        trouble=trouble)


@dataclass(frozen=True)
class MainFunestraInputs:
    main_mode: MainMode
    main_funestra: MainFunestraStatus
    genau: GenauStatus
    active: bool
    osr2_mode: Osr2Mode
    osr2_trouble: Osr2Trouble | None = None
    hud_corner: HudCorner = HudCorner.UPPER_LEFT
    hud_edge: HudEdge = HudEdge.LOWER
    hud_minimized: bool = False
    genau_pace_s: int = 0
    scripted_filter: bool = False
    genau_favorites_filter: bool = False
    latest: bool = False
    genau_latest: bool = False
    plays_vr: bool | None = None
    plays_flat: bool | None = None
    length_mode: LengthMode = LengthMode.MIXED
    genau_plays_vr: bool | None = None
    genau_plays_flat: bool | None = None
    plays_portrait: bool = True
    plays_landscape: bool = True
    genau_plays_portrait: bool = True
    genau_plays_landscape: bool = True
    nothing_to_reset: bool = False
    in_vr: bool = False
    crowned: bool = True
    has_osr2: bool = True


def main_hud_model(inputs: MainFunestraInputs) -> MainHudModel:
    main_funestra, genau = inputs.main_funestra, inputs.genau
    video = kino_shows(inputs.main_mode)
    facts = MainFunestraFacts(
        main_mode=inputs.main_mode,
        locked=main_funestra.locked if video else genau.locked,
        f_mode=inputs.scripted_filter if video else inputs.genau_favorites_filter,
        latest=inputs.latest if video else inputs.genau_latest,
        loop_state=main_funestra.loop_state,
        plays_vr=inputs.plays_vr if video else inputs.genau_plays_vr,
        plays_flat=inputs.plays_flat if video else inputs.genau_plays_flat,
        plays_portrait=inputs.plays_portrait if video else inputs.genau_plays_portrait,
        plays_landscape=inputs.plays_landscape if video else inputs.genau_plays_landscape,
        pace_s=inputs.genau_pace_s,
        length_mode=inputs.length_mode,
        compilation=main_funestra.compilation,
        has_compilation=main_funestra.has_compilation,
        has_other_versions=main_funestra.has_other_versions,
        longer_than_a_step=longer_than_a_step(main_funestra.duration_ms),
        jump_to=main_funestra.jump_to,
        nothing_to_reset=inputs.nothing_to_reset,
        flipped=genau.flipped,
        crowned=inputs.crowned,
        has_osr2=inputs.has_osr2,
        has_funscript=main_funestra.has_funscript,
    )
    return MainHudModel(
        main_mode=inputs.main_mode,
        hud_corner=inputs.hud_corner,
        hud_edge=inputs.hud_edge,
        hud_minimized=inputs.hud_minimized,
        active=inputs.active,
        osr2=osr2_state(main_mode=inputs.main_mode, osr2_mode=inputs.osr2_mode,
                        funscript_driving=main_funestra.funscript_driving,
                        trouble=inputs.osr2_trouble),
        locked=facts.locked,
        latest=facts.latest,
        filters=title_filters(facts),
        rows=main_hud_rows(facts, in_vr=inputs.in_vr),
        osr2_rows=osr2_rows(facts),
        has_osr2=inputs.has_osr2,
    )


def main_funestra_hud_path(state_dir: Path) -> Path:
    return state_dir / MAIN_FUNESTRA_HUD_FILENAME

from __future__ import annotations

from dataclasses import dataclass

from app_support.osr2_device import Osr2Mode, Osr2Trouble
from funestra.hud_controls import longer_than_a_step
from funestra.hud_kinds import HudFacts, HudKind
from funestra.hud_placement import HudCorner, HudEdge
from funestra.main_hud import OSR2_CONTROL_UNANSWERED
from funestra.modes import LengthMode, MainMode, Osr2State
from funestra.satellite_hud import HudModel

from .content import genau_flick_noun
from .fun_time_funestras import FunTimeFunestra
from .funestra_status import GenauStatus, MainFunestraStatus
from .mode_plan import kino_shows


def osr2_state(*, main_mode: MainMode, osr2_mode: Osr2Mode, funscript_driving: bool,
               trouble: Osr2Trouble | None = None) -> Osr2State:
    status_is_of_a_video_now_on_screen = kino_shows(main_mode)
    return Osr2State.of(osr2_mode,
                        funscript_driving=funscript_driving and status_is_of_a_video_now_on_screen,
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
    favorites_filter: bool = False
    enhanced_filter: bool = False
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
    is_favorite: bool = False
    osr2_control: str = OSR2_CONTROL_UNANSWERED
    paused: bool = False


def main_hud_model(inputs: MainFunestraInputs) -> HudModel:
    main_funestra, genau = inputs.main_funestra, inputs.genau
    video = kino_shows(inputs.main_mode)
    flick = genau_flick_noun()
    return HudModel(
        funestra=FunTimeFunestra.MAIN.label,
        kind=HudKind.MAIN,
        facts=HudFacts(
            funestra=FunTimeFunestra.MAIN.label,
            main_mode=inputs.main_mode,
            locked=main_funestra.locked if video else genau.locked,
            favorites=inputs.favorites_filter if video else inputs.genau_favorites_filter,
            enhanced=inputs.enhanced_filter and video,
            latest=inputs.latest if video else inputs.genau_latest,
            snippet=main_funestra.snippet_state.value,
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
            image=main_funestra.image,
            jump_to=main_funestra.jump_to,
            nothing_to_reset=inputs.nothing_to_reset,
            flipped=genau.flipped,
            favorite=inputs.is_favorite,
            in_vr=inputs.in_vr,
            crowned=inputs.crowned,
            has_osr2=inputs.has_osr2,
            has_funscript=main_funestra.has_funscript,
            flick_one=flick.one,
            flick_many=flick.many,
        ),
        hud_corner=inputs.hud_corner,
        hud_edge=inputs.hud_edge,
        hud_minimized=inputs.hud_minimized,
        active=inputs.active,
        is_favorite=inputs.is_favorite,
        osr2=osr2_state(main_mode=inputs.main_mode, osr2_mode=inputs.osr2_mode,
                        funscript_driving=main_funestra.funscript_driving,
                        trouble=inputs.osr2_trouble),
        osr2_control=inputs.osr2_control,
        paused=inputs.paused,
    )

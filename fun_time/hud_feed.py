from __future__ import annotations

from app_support import osr2_device
from app_support.osr2_device import Osr2Mode, Osr2Trouble
from funestra import broker
from funestra.main_hud import main_hud_text
from funestra.modes import MainMode, Osr2State
from funestra.osr2_visualizer import read_drive
from funestra.satellite_hud import HudModel, hud_text, parse_hud

from .bridge_records import BridgeConfig
from .crown import Crown
from .dispatch_loop_setup import LOOP_TICK_S
from .fun_time_funestras import FunTimeFunestra
from .funestra_status import (
    MainFunestraStatus,
    read_genau_status,
    read_main_funestra_status,
)
from .hud_transport import HudPublisher, hosted_model, worn
from .main_funestra_hud import MainFunestraInputs, main_hud_model, osr2_state
from .main_list_commands import (
    genau_flick_shapes,
    main_funestra_at_defaults,
    main_video_shapes,
)
from .osr2_section import (
    DASHBOARD_OSR2_PART,
    DeviceBlock,
    Osr2PartFacts,
    funestra_with_the_osr2,
    osr2_part,
    tell_a_hosted_satellite_whose_the_osr2_is,
)
from .satellite_control import read_satellite_status
from .shared_state import BridgeState

PUBLISH_INTERVAL_S = 3 * LOOP_TICK_S


def _bare_words_reach(funestra: FunTimeFunestra, state: BridgeState) -> bool:
    return state.active_funestra == funestra


class HudFeed:
    def __init__(self, *, config: BridgeConfig, publisher: HudPublisher | None) -> None:
        self.config = config
        self.publisher = publisher
        self._last_publish = 0.0
        self._hosted_huds: dict[FunTimeFunestra, HudModel | None] = {}
        self._last_worn_hosted: dict[FunTimeFunestra, HudModel | None] = {}
        self._osr2_told: dict[FunTimeFunestra, bool] = {}

    def publish_due(self, state: BridgeState, *, now: float) -> None:
        if now - self._last_publish < PUBLISH_INTERVAL_S:
            return
        self._last_publish = now
        self.publish(state)

    def publish(self, state: BridgeState) -> None:
        if self.publisher is None:
            return
        genau = read_genau_status(self.config.genau_status_file)
        broker_running = broker.is_running(self.config.broker_state)
        osr2_mode, osr2_trouble = self.osr2_mode(), self.osr2_trouble()
        with_the_osr2 = funestra_with_the_osr2(state)
        main_funestra = read_main_funestra_status(self.config.main_funestra_status_file)
        osr2 = self._holders_osr2(with_the_osr2, state.main_mode, main_funestra,
                                  osr2_mode, osr2_trouble)

        def device(funestra: FunTimeFunestra) -> DeviceBlock:
            if funestra is not with_the_osr2:
                return DeviceBlock.offered_to(funestra)
            return DeviceBlock.held_by(funestra, osr2)

        for funestra in FunTimeFunestra.SATELLITES:
            tell_a_hosted_satellite_whose_the_osr2_is(self.config, self._osr2_told, funestra,
                                                      funestra is with_the_osr2)
            self.publisher.publish_text(funestra.label, hud_text(worn(hosted_model(
                funestra.label, self._worn_hosted_hud(funestra), state.satellite(funestra),
                in_vr=self.config.vr_main_funestra, crowned=state.crowned == funestra.label,
                active=_bare_words_reach(funestra, state)), device(funestra))))
        main_shapes = main_video_shapes(state, self.config)
        genau_shapes = genau_flick_shapes(state, self.config)
        self.publisher.publish_text("main_funestra", main_hud_text(main_hud_model(MainFunestraInputs(
            main_mode=state.main_mode,
            hud_corner=state.main_hud_corner,
            hud_edge=state.main_hud_edge,
            hud_minimized=state.main_hud_minimized,
            active=_bare_words_reach(FunTimeFunestra.MAIN, state),
            scripted_filter=state.main_scripted_filter,
            genau_favorites_filter=state.genau_favorites_filter,
            latest=state.main_latest,
            genau_latest=state.genau_latest,
            plays_vr=main_shapes.plays_vr if main_shapes.offered else None,
            plays_flat=main_shapes.plays_flat if main_shapes.offered else None,
            length_mode=state.main_length_mode,
            genau_plays_vr=genau_shapes.plays_vr if genau_shapes.offered else None,
            genau_plays_flat=genau_shapes.plays_flat if genau_shapes.offered else None,
            plays_portrait=state.main_plays_portrait,
            plays_landscape=state.main_plays_landscape,
            genau_plays_portrait=state.genau_plays_portrait,
            genau_plays_landscape=state.genau_plays_landscape,
            osr2_mode=osr2_mode,
            osr2_trouble=osr2_trouble,
            main_funestra=main_funestra,
            genau=genau,
            genau_pace_s=self._genau_pace_s(),
            nothing_to_reset=main_funestra_at_defaults(state, self.config, main_funestra),
            in_vr=self.config.vr_main_funestra,
            crowned=state.crowned is Crown.MAIN,
            has_osr2=with_the_osr2 is FunTimeFunestra.MAIN,
        ))))
        self.publisher.publish_text(DASHBOARD_OSR2_PART, main_hud_text(osr2_part(
            with_the_osr2, Osr2PartFacts(
                main_mode=state.main_mode, osr2=osr2, control=state.osr2_control,
                paused=state.omni_paused, learned=genau.learned_active, shape=genau.shape,
                broker=broker_running, max_intensity=state.max_intensity,
            ))))

    def _holders_osr2(self, holder: FunTimeFunestra, main_mode: MainMode,
                      main_funestra: MainFunestraStatus, osr2_mode: Osr2Mode,
                      trouble: Osr2Trouble | None) -> Osr2State:
        if holder is FunTimeFunestra.MAIN:
            return osr2_state(main_mode=main_mode, osr2_mode=osr2_mode,
                              funscript_driving=main_funestra.funscript_driving, trouble=trouble)
        scripted = read_satellite_status(self.config.satellite(holder).status_file)
        return Osr2State.of(osr2_mode, funscript_driving=scripted.funscript_driving, trouble=trouble)

    def _worn_hosted_hud(self, funestra: FunTimeFunestra) -> HudModel | None:
        worn = self._hosted_hud(funestra) or self._last_worn_hosted.get(funestra)
        self._last_worn_hosted[funestra] = worn
        return worn

    def _hosted_hud(self, funestra: FunTimeFunestra) -> HudModel | None:
        path = self.config.satellite(funestra).origenerator_hud_file
        if path is None:
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        except OSError:
            return self._the_hud_the_app_is_republishing(funestra)
        hud = self._hosted_huds[funestra] = parse_hud(text)
        return hud

    def _the_hud_the_app_is_republishing(self, funestra: FunTimeFunestra) -> HudModel | None:
        return self._hosted_huds.get(funestra)

    def _genau_pace_s(self) -> int:
        drive = read_drive(self.config.genau_drive_file)
        return drive.advance_interval if drive is not None else 0

    def osr2_mode(self) -> Osr2Mode:
        return osr2_device.mode(self.config.broker_state)

    def osr2_trouble(self) -> Osr2Trouble | None:
        return osr2_device.trouble(self.config.broker_state)

from __future__ import annotations

from pathlib import Path

from app_support import favorites, osr2_device
from app_support.media_renditions import renditions
from app_support.osr2_device import Osr2Mode, Osr2Trouble
from funestra import broker
from funestra.hud_controls import longer_than_a_step
from funestra.main_hud import main_hud_text
from funestra.modes import MainMode, Osr2State, SatellitesMode
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
from .hud_transport import HudPublisher, HudState, hosted_model, worn
from .lock_hud import SatelliteInputs, build_satellite_hud
from .main_funestra_hud import MainFunestraInputs, main_hud_model, osr2_state
from .main_list_commands import (
    genau_flick_shapes,
    main_funestra_at_defaults,
    main_video_shapes,
)
from .media_metadata import load_metadata, metadata_path_for
from .origenerator_switches import hosts, origenerator_shows
from .osr2_section import (
    DASHBOARD_OSR2_PART,
    DeviceBlock,
    Osr2PartFacts,
    forget_what_a_satellite_was_told_of_the_osr2,
    funestra_with_the_osr2,
    osr2_part,
    tell_a_hosted_satellite_whose_the_osr2_is,
)
from .satellite_commands import satellite_at_defaults
from .satellite_control import SatelliteStatus, read_satellite_status
from .shared_state import BridgeState

PUBLISH_INTERVAL_S = 3 * LOOP_TICK_S


def _has_not_started_yet(status: SatelliteStatus, last_named: SatelliteStatus | None) -> bool:
    return last_named is None and not status.video


def _read_lost_a_race_with_the_republish(
        status: SatelliteStatus, last_named: SatelliteStatus | None) -> bool:
    return last_named is not None and not status.video


class _FavoritesReadOnceUntilTheRecordMoves:
    def __init__(self) -> None:
        self._read: dict[Path, tuple[tuple[int, int], bool]] = {}

    def of(self, video: str, metadata_root: Path | None) -> bool:
        record = metadata_path_for(video, metadata_root) if video else None
        if record is None:
            return False
        try:
            stat = record.stat()
        except OSError:
            return False
        stamp = (stat.st_mtime_ns, stat.st_size)
        read = self._read.get(record)
        if read is None or read[0] != stamp:
            read = self._read[record] = (stamp, favorites.is_favorite(load_metadata(record)))
        return read[1]


def _bare_words_reach(funestra: FunTimeFunestra, state: BridgeState) -> bool:
    return state.active_funestra == funestra


class HudFeed:
    def __init__(self, *, config: BridgeConfig, publisher: HudPublisher | None) -> None:
        self.config = config
        self.publisher = publisher
        self._last_publish = 0.0
        self._favorites = _FavoritesReadOnceUntilTheRecordMoves()
        self._last_satellite_status: dict[str, SatelliteStatus] = {}
        self._the_apps_on_screen: dict[str, str] = {}
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

        def satellite(funestra: FunTimeFunestra) -> SatelliteInputs:
            name = funestra.label
            status = self._satellite_status(name, self.config.satellite(funestra).status_file)
            current = status.video
            values = state.satellite(funestra)
            return SatelliteInputs(
                funestra=name, sources=self.config.satellite(funestra).sources,
                current=current, locked=values.locked,
                hud_corner=values.hud_corner, hud_edge=values.hud_edge,
                hud_minimized=values.hud_minimized,
                filter_query=values.filter,
                loop_axis=values.loop,
                nav_map_anchor=values.nav_map_anchor,
                widen_video=values.widen_video,
                nav_anchor=values.nav_anchor,
                latest=values.latest,
                favorites_filter=values.favorites_filter,
                is_favorite=self._favorites.of(current, self.config.regen_metadata_root),
                nothing_to_reset=satellite_at_defaults(values, status),
                has_other_versions=bool(renditions(current, self.config.regen_media_root)),
                longer_than_a_step=longer_than_a_step(status.duration_ms),
                has_osr2=funestra is with_the_osr2,
                has_funscript=status.has_funscript,
                mode=SatellitesMode.KINO if self.config.origenerator_enabled else None,
                active=_bare_words_reach(funestra, state),
                origenerator_ready=state.origenerator_ready,
                in_vr=self.config.vr_main_funestra,
                crowned=state.crowned == name,
            )

        told = self._osr2_told
        for funestra in FunTimeFunestra.SATELLITES:
            if hosts(funestra, state, self.config):
                tell_a_hosted_satellite_whose_the_osr2_is(self.config, told, funestra,
                                                     funestra is with_the_osr2)
                held = state.satellite(funestra)
                self.publisher.publish_text(funestra.label, hud_text(worn(hosted_model(
                    funestra.label, self._worn_hosted_hud(funestra, state), held,
                    HudState(origenerator_ready=state.origenerator_ready,
                             in_vr=self.config.vr_main_funestra,
                             crowned=state.crowned == funestra.label),
                    active=_bare_words_reach(funestra, state)), device(funestra))))
                continue
            forget_what_a_satellite_was_told_of_the_osr2(told, funestra)
            self.publisher.publish(funestra.label, build_satellite_hud(
                satellite(funestra), self.config.regen_metadata_root,
            ), device(funestra))
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

    def _worn_hosted_hud(self, funestra: FunTimeFunestra, state: BridgeState) -> HudModel | None:
        worn = self._hosted_hud(funestra)
        if worn is None and not origenerator_shows(state.satellite(funestra).mode):
            worn = self._last_worn_hosted.get(funestra)
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

    def the_video_it_comes_home_to(self, funestra: FunTimeFunestra) -> str:
        name = funestra.label
        self._the_apps_on_screen[name] = read_satellite_status(
            self.config.satellite(funestra).status_file).video
        left_on = self._last_satellite_status.get(name)
        return left_on.video if left_on is not None else ""

    def _satellite_status(self, funestra: str, status_file: Path) -> SatelliteStatus:
        status = read_satellite_status(status_file)
        last_named = self._last_satellite_status.get(funestra)
        if _has_not_started_yet(status, last_named):
            return status
        if (_read_lost_a_race_with_the_republish(status, last_named)
                or self._still_names_the_apps_picture(funestra, status)):
            return last_named if last_named is not None else status
        self._the_apps_on_screen.pop(funestra, None)
        self._last_satellite_status[funestra] = status
        return status

    def _still_names_the_apps_picture(self, funestra: str, status: SatelliteStatus) -> bool:
        return status.video == self._the_apps_on_screen.get(funestra)

    def osr2_mode(self) -> Osr2Mode:
        return osr2_device.mode(self.config.broker_state)

    def osr2_trouble(self) -> Osr2Trouble | None:
        return osr2_device.trouble(self.config.broker_state)

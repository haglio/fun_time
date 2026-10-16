from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from app_support.state_files import GENAU_DRIVE, GENAU_STATUS
from funestra.drive_readout import DriveHud, read_drive
from funestra.status import FunestraStatus

from .funestra_status import (
    GenauStatus,
    MainFunestraStatus,
    published_genau_status,
    published_main_funestra_status,
)
from .origenerator_switches import origenerator_shows
from .room_funestras import RoomFunestra
from .satellite_control import published_satellite_status
from .shared_state import BridgeState


@dataclass(frozen=True)
class FunestraReports:
    main: MainFunestraStatus | None = None
    satellites: Mapping[RoomFunestra, FunestraStatus | None] = field(default_factory=dict)
    genau_drive: DriveHud | None = None
    genau: GenauStatus | None = None


def reports_left_in(state_dir: Path, *, main: MainFunestraStatus | None,
                    satellites: Mapping[RoomFunestra, FunestraStatus | None]) -> FunestraReports:
    return FunestraReports(main=main, satellites=satellites,
                           genau_drive=read_drive(Path(state_dir) / GENAU_DRIVE),
                           genau=published_genau_status(Path(state_dir) / GENAU_STATUS))


def read_funestra_reports(config) -> FunestraReports:
    return reports_left_in(
        config.state_dir, main=published_main_funestra_status(config.main_funestra_status_file),
        satellites={funestra: published_satellite_status(config.satellite(funestra).status_file)
                    for funestra in RoomFunestra.SATELLITES})


def with_what_the_funestras_report(state: BridgeState, reports: FunestraReports) -> BridgeState:
    if reports.main is not None:
        main = reports.main
        loop_in_ms, loop_out_ms = main.loop_bounds or (0, 0)
        state = replace(state, main_speed=main.speed, main_locked=main.locked,
                        main_loop_video=main.video if main.loop_bounds else "",
                        main_loop_in_ms=loop_in_ms, main_loop_out_ms=loop_out_ms)
    for funestra, status in reports.satellites.items():
        if status is None:
            continue
        if not origenerator_shows(state.satellite(funestra).mode):
            state = state.with_satellite(funestra, speed=status.speed)
        if status.volume is not None and status.muted is not None:
            state = state.with_satellite(funestra, volume=status.volume, muted=status.muted)
    if reports.genau_drive is not None:
        drive = reports.genau_drive
        state = replace(state, genau_speed=drive.speed, genau_amplitude=drive.amplitude,
                        genau_center=drive.center, genau_shape=drive.shape,
                        genau_flick_seconds=drive.advance_interval)
    if reports.genau is not None:
        state = replace(state, genau_learned=reports.genau.learned_active,
                        genau_locked=reports.genau.locked)
    return state

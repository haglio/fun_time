from __future__ import annotations

import json
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

HEADSET_LAYOUT_FILENAME = "vr_layout.json"


def _the_headsets_layout(state_dir: Path) -> str | None:
    try:
        reported = json.loads((Path(state_dir) / HEADSET_LAYOUT_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return json.dumps(reported, separators=(",", ":")) if isinstance(reported, dict) else None


@dataclass(frozen=True)
class FunestraReports:
    main: MainFunestraStatus | None = None
    satellites: Mapping[RoomFunestra, FunestraStatus | None] = field(default_factory=dict)
    genau_drive: DriveHud | None = None
    genau: GenauStatus | None = None
    headset_layout: str | None = None


def reports_published_in(state_dir: Path, *, main_status_file: Path,
                         satellite_status_files: Mapping[RoomFunestra, Path]) -> FunestraReports:
    return FunestraReports(
        main=published_main_funestra_status(Path(main_status_file)),
        satellites={funestra: published_satellite_status(Path(status_file))
                    for funestra, status_file in satellite_status_files.items()},
        genau_drive=read_drive(Path(state_dir) / GENAU_DRIVE),
        genau=published_genau_status(Path(state_dir) / GENAU_STATUS),
        headset_layout=_the_headsets_layout(state_dir))


def read_funestra_reports(config) -> FunestraReports:
    return reports_published_in(
        config.state_dir, main_status_file=config.main_funestra_status_file,
        satellite_status_files={funestra: config.satellite(funestra).status_file
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
    if reports.headset_layout is not None:
        state = replace(state, headset_layout=reports.headset_layout)
    return state

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from app_support import state_files

from .config import RegenConfig, SatelliteChannel
from .event_log import MESSAGE, SOURCE_SYSTEM
from .fun_time_funestras import FunTimeFunestra
from .loopback_server import LOOPBACK_PORT
from .main_list import MainList
from .main_list_builds import BUILDS_HERE, MainListBuilds

if TYPE_CHECKING:
    from .command_value import Command
    from .shared_state import BridgeState


@dataclass
class BridgeConfig:
    portrait: SatelliteChannel
    landscape: SatelliteChannel
    trash_dir: Path
    state_dir: Path
    main_sources: str
    broker_mode_file: Path
    genau_cmd_file: Path
    genau_paused_file: Path
    genau_status_file: Path
    audio_paused_file: Path
    audio_volume_file: Path
    main_funestra_cmd_file: Path
    main_funestra_paused_file: Path
    main_funestra_status_file: Path
    dashboard_state_file: Path
    loopback_port: int = LOOPBACK_PORT
    main_funestra_notice_file: Path | None = None
    vr_main_funestra: bool = False
    vr_library_dirs: str = ""
    genau_vr_flicks: str = ""
    python_exe: str = ""
    broker_cmd_file: Path | None = None
    broker_tray_launcher: Path | None = None
    origenerator_enabled: bool = False
    origenerator_cmd_file: Path | None = None
    origenerator_said_at_suffix: str = ""
    origenerator_paused_file: Path | None = None
    origenerator_lock_phrases: frozenset[str] = frozenset()
    origenerator_status_file: Path | None = None
    wrong_actions_said_in: Path | None = None
    broker_state_dir: Path | None = None
    regen_media_root: Path | None = None
    regen_metadata_root: Path | None = None
    regen_generate_video_url: str = RegenConfig.generate_video_url
    regen_generate_image_url: str = RegenConfig.generate_image_url
    main_list_builds: MainListBuilds = BUILDS_HERE

    def satellite(self, funestra: FunTimeFunestra) -> SatelliteChannel:
        return (self.portrait if FunTimeFunestra(funestra) is FunTimeFunestra.PORTRAIT
                else self.landscape)

    @property
    def hosts_origenerator(self) -> bool:
        return self.origenerator_enabled and self.origenerator_cmd_file is not None

    @property
    def main_list(self) -> MainList:
        return MainList(sources=self.main_sources, state_dir=self.state_dir,
                        vr_dirs=self.vr_library_dirs, metadata_root=self.regen_metadata_root)

    @property
    def broker_state(self) -> Path:
        return self.broker_state_dir or self.state_dir

    @property
    def genau_drive_file(self) -> Path:
        return self.state_dir / state_files.GENAU_DRIVE

    @property
    def regen(self) -> RegenConfig:
        return RegenConfig(
            generate_video_url=self.regen_generate_video_url,
            generate_image_url=self.regen_generate_image_url,
            media_root=self.regen_media_root,
            metadata_root=self.regen_metadata_root,
        )


class Op(StrEnum):
    MESSAGE = "message"
    SHOW_WINDOW = "show_window"
    HIDE_WINDOW = "hide_window"
    ACTIVATE_WINDOW = "activate_window"
    MINIMIZE_WINDOW = "minimize_window"
    RESTORE_PARKED = "restore_parked"
    RESTACK_RFB = "restack_rfb"
    DISABLE_ALL_TOPMOST = "disable_all_topmost"
    RESTORE_ALL_TOPMOST = "restore_all_topmost"
    SUSPEND_HOTKEYS = "suspend_hotkeys"
    UNSUSPEND_HOTKEYS = "unsuspend_hotkeys"
    OPEN_RFB_TAB = "open_rfb_tab"
    SAVE_CLIP = "save_clip"
    MAIN_FUNESTRA_ANSWERS = "main_funestra_answers"
    NARROWING_ANSWERS = "narrowing_answers"
    IGNORED = "ignored"
    HAND_OVER_FUNESTRAS = "hand_over_funestras"
    TAKE_BACK_FUNESTRAS = "take_back_funestras"
    FOLLOW_THE_LOCK = "follow_the_lock"


@dataclass(frozen=True)
class WindowOp:
    op: str
    key: str = ""
    source: str = SOURCE_SYSTEM
    level: int = MESSAGE


Result = "tuple[BridgeState, list[WindowOp]]"
Handler = Callable[["BridgeState", "BridgeConfig", str], Result]
SatelliteHandler = Callable[[FunTimeFunestra, "BridgeState", "BridgeConfig", str], Result]
Route = Callable[["Command", "BridgeState", "BridgeConfig", str], Result]


IN_KINO_MODE = "in Kino mode"


IN_GENAU_MODE = "in Genau mode"



OUTSIDE_VR = "outside VR"


ON_THE_MAIN_FUNESTRA = "on the Main Funestra"


def ignored(state: BridgeState, why: str) -> tuple[BridgeState, list[WindowOp]]:
    return state, [WindowOp(op=Op.IGNORED, key=why)]


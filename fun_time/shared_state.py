from __future__ import annotations

import configparser
import io
import logging
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from pathlib import Path

from funestra.flick_advance import FlickAdvanceState
from funestra.hud_placement import HudCorner, HudEdge
from funestra.main_hud import OSR2_DRIVING
from funestra.modes import LengthMode, MainMode, SatellitesMode, read_mode
from funestra.robot_hand import FULL_INTENSITY, RobotHandState

from .audio_volume import MAX_VOLUME
from .crown import Crown
from .fun_time_funestras import FunTimeFunestra
from .mode_plan import STARTUP_MAIN_MODE, EnhancementLevel
from .origenerator_switches import KINO_MODE, RfbMode, origenerator_shows
from .polled_files import READER_HOLD_BUDGET_S, replace_despite_readers

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SatelliteState:
    locked: bool = False
    hud_corner: HudCorner = HudCorner.UPPER_LEFT
    hud_edge: HudEdge = HudEdge.LOWER
    hud_minimized: bool = False
    filter: str = ""
    favorites_filter: bool = False
    enhancement: EnhancementLevel = EnhancementLevel.BOTH
    latest: bool = False
    loop: str = ""
    nav_map_anchor: str = ""
    widen_video: str = ""
    nav_anchor: str = ""
    mode: SatellitesMode = KINO_MODE
    handed_over: bool = False
    speed: float = 1.0
    volume: int = MAX_VOLUME
    muted: bool = True


def _satellite_key(name: str, funestra: FunTimeFunestra) -> str:
    return f"{funestra.label}_{name}"


@dataclass
class BridgeState:
    portrait: SatelliteState = field(default_factory=SatelliteState)
    landscape: SatelliteState = field(default_factory=SatelliteState)
    main_mode: MainMode = STARTUP_MAIN_MODE
    rfb_mode: RfbMode = RfbMode.RFB
    origenerator_ready: bool = False
    main_scripted_filter: bool = False
    main_hud_corner: HudCorner = HudCorner.UPPER_LEFT
    main_hud_edge: HudEdge = HudEdge.LOWER
    main_hud_minimized: bool = False
    omni_paused: bool = False
    main_latest: bool = False
    main_shuffle_seed: int = 0
    main_length_mode: LengthMode = LengthMode.MIXED
    main_enhancement: EnhancementLevel = EnhancementLevel.BOTH
    main_plays_vr: bool = True
    main_plays_flat: bool = True
    main_plays_portrait: bool = True
    main_plays_landscape: bool = True
    genau_latest: bool = False
    genau_plays_vr: bool = True
    genau_plays_flat: bool = True
    genau_plays_portrait: bool = True
    genau_plays_landscape: bool = True
    genau_favorites_filter: bool = False
    active_funestra: int = 1
    volume: int = MAX_VOLUME
    muted: bool = False
    max_intensity: int = FULL_INTENSITY
    main_speed: float = 1.0
    main_locked: bool = True
    main_loop_video: str = ""
    main_loop_bounds_ms: str = ""
    genau_speed: int = RobotHandState().speed
    genau_amplitude: int = RobotHandState().amplitude
    genau_center: int = RobotHandState().center
    genau_shape: str = RobotHandState().shape.value
    genau_flick_seconds: int = FlickAdvanceState().interval
    genau_learned: bool = False
    genau_locked: bool = True
    mic_muted: bool = False
    headset_layout: str = ""
    osr2_control: str = OSR2_DRIVING
    crowned: Crown = Crown.MAIN
    majority: Crown = Crown.PORTRAIT
    osr2_funestra: int = 1

    def satellite(self, funestra: FunTimeFunestra) -> SatelliteState:
        return self.portrait if FunTimeFunestra(funestra) is FunTimeFunestra.PORTRAIT else self.landscape

    def with_satellite(self, funestra: FunTimeFunestra, **changes) -> BridgeState:
        satellite = replace(self.satellite(funestra), **changes)
        if FunTimeFunestra(funestra) is FunTimeFunestra.PORTRAIT:
            return replace(self, portrait=satellite)
        return replace(self, landscape=satellite)

    def origenerator_holds_a_satellite(self) -> bool:
        return any(origenerator_shows(self.satellite(funestra).mode)
                   for funestra in FunTimeFunestra.SATELLITES)


SHARED_STATE_FILENAME = "shared_bridge_state.ini"


def shared_state_path(state_dir: Path) -> Path:
    return Path(state_dir) / SHARED_STATE_FILENAME


def _written_satellites(state: BridgeState) -> dict[str, str]:
    return {
        _satellite_key(f.name, funestra): _written(getattr(state.satellite(funestra), f.name))
        for funestra in FunTimeFunestra.SATELLITES
        for f in fields(SatelliteState)
    }


def _written(value: bool | int | float | str) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def main_loop_bounds(state: BridgeState) -> tuple[int, int]:
    try:
        in_ms, out_ms = state.main_loop_bounds_ms.split()
        return int(in_ms), int(out_ms)
    except ValueError:
        return 0, 0


def _session_fields():
    return [f for f in fields(BridgeState) if f.default_factory is not SatelliteState]


def write_shared_state(state_file: Path, state: BridgeState) -> None:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["state"] = {
        **{f.name: _written(getattr(state, f.name)) for f in _session_fields()},
        **_written_satellites(state),
    }
    _publish(state_file, parser)


def _publish(state_file: Path, parser: configparser.ConfigParser) -> None:
    text = io.StringIO()
    parser.write(text)
    if not replace_despite_readers(state_file, text.getvalue()):
        logger.warning("Could not write %s: another process kept it open for %gs",
                       state_file.name, READER_HOLD_BUDGET_S)


def _read_satellite(section, funestra: FunTimeFunestra) -> SatelliteState:
    return SatelliteState(**{
        f.name: _read_back(section.get(_satellite_key(f.name, funestra)), f.default)
        for f in fields(SatelliteState)
    })


def _read_back(written: str | None, default: bool | int | float | str) -> bool | int | float | str:
    if written is None:
        return default
    if isinstance(default, bool):
        return written == "1"
    if isinstance(default, Enum):
        return read_mode(type(default), written, default)
    for number in (float, int):
        if isinstance(default, number):
            try:
                return number(written)
            except ValueError:
                return default
    return written


_LAST_SESSIONS_KEYS = {
    "locked2": ("portrait_locked",),
    "locked3": ("landscape_locked",),
    "active_side": ("active_funestra",),
    "main_f_mode": ("main_scripted_filter",),
    "portrait_f_mode": ("portrait_favorites_filter",),
    "landscape_f_mode": ("landscape_favorites_filter",),
    "active_player": ("active_funestra",),
    "osr2_player": ("osr2_funestra",),
    "satellites_mode": ("portrait_mode", "landscape_mode", "rfb_mode"),
    "rfb_slot": ("rfb_mode",),
    "portrait_map_anchor": ("portrait_nav_map_anchor",),
    "landscape_map_anchor": ("landscape_nav_map_anchor",),
    "portrait_widen_clip": ("portrait_widen_video",),
    "landscape_widen_clip": ("landscape_widen_video",),
}


def migrate_shared_state(state_file: Path) -> bool:
    if not state_file.exists():
        return False
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(state_file), encoding="utf-8")
    if "state" not in parser:
        return False
    section = parser["state"]
    old_keys = [old for old in _LAST_SESSIONS_KEYS if old in section]
    if not old_keys:
        return False
    for old in old_keys:
        for new in _LAST_SESSIONS_KEYS[old]:
            section.setdefault(new, section[old])
        del section[old]
    _publish(state_file, parser)
    return True


def written_with(state_file: Path, key: str) -> bool:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(state_file), encoding="utf-8")
    return "state" in parser and key in parser["state"]


def read_shared_state(state_file: Path) -> BridgeState | None:
    if not state_file.exists():
        return None
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(state_file), encoding="utf-8")
    if "state" not in parser:
        return None
    s = parser["state"]
    return BridgeState(
        portrait=_read_satellite(s, FunTimeFunestra.PORTRAIT),
        landscape=_read_satellite(s, FunTimeFunestra.LANDSCAPE),
        **{f.name: _read_back(s.get(f.name), f.default) for f in _session_fields()},
    )

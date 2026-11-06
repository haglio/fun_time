from __future__ import annotations

from dataclasses import replace
from functools import partial

from funestra.file_channel import append_command
from funestra.hud_placement import HudCorner, HudEdge

from .bridge_records import BridgeConfig, Handler, Result, Route, SatelliteHandler, WindowOp
from .command_value import Command
from .fun_time_funestras import FunTimeFunestra
from .origenerator_switches import hosts
from .shared_state import BridgeState

MAIN_HUD_TURNS = {"main_hud_clockwise": True, "main_hud_counterclockwise": False}

_DIRECTIONS = ("left", "right", "up", "down")


def _restored_at(funestra: FunTimeFunestra, place: str, state: BridgeState,
                 config: BridgeConfig) -> BridgeState:
    if config.vr_main_funestra:
        return _hung_open_against(funestra, place, state)
    return _opened_in(funestra, place, state, config)


def _hung_open_against(funestra: FunTimeFunestra, edge: str, state: BridgeState) -> BridgeState:
    if edge not in HudEdge:
        return state
    if funestra is FunTimeFunestra.MAIN:
        return replace(state, main_hud_edge=HudEdge(edge), main_hud_minimized=False)
    return state.with_satellite(funestra, hud_edge=HudEdge(edge), hud_minimized=False)


def _opened_in(funestra: FunTimeFunestra, corner: str, state: BridgeState,
               config: BridgeConfig) -> BridgeState:
    if corner not in HudCorner:
        return state
    if funestra is FunTimeFunestra.MAIN:
        return replace(state, main_hud_corner=HudCorner(corner), main_hud_minimized=False)
    _tell_the_hosted_app(funestra, f"hud_corner|{corner}", state, config)
    _tell_the_hosted_app(funestra, "hud_minimized|0", state, config)
    return state.with_satellite(funestra, hud_corner=HudCorner(corner), hud_minimized=False)


def _collapsed_hud(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, *,
                   minimized: bool) -> BridgeState:
    if funestra is FunTimeFunestra.MAIN:
        return replace(state, main_hud_minimized=minimized)
    _tell_the_hosted_app(funestra, f"hud_minimized|{1 if minimized else 0}", state, config)
    return state.with_satellite(funestra, hud_minimized=minimized)


def _main_hud_turned(clockwise: bool, state: BridgeState, config: BridgeConfig) -> BridgeState:
    if config.vr_main_funestra:
        return replace(state, main_hud_edge=state.main_hud_edge.turned(clockwise=clockwise))
    return replace(state, main_hud_corner=state.main_hud_corner.turned(clockwise=clockwise))


def _satellite_hud_moved(direction: str, funestra: FunTimeFunestra, state: BridgeState,
                         config: BridgeConfig) -> BridgeState:
    satellite = state.satellite(funestra)
    if config.vr_main_funestra:
        return state.with_satellite(funestra, hud_edge=satellite.hud_edge.toward(direction))
    corner = satellite.hud_corner.toward(direction)
    _tell_the_hosted_app(funestra, f"hud_corner|{corner}", state, config)
    return state.with_satellite(funestra, hud_corner=corner)


def _tell_the_hosted_app(funestra: FunTimeFunestra, action: str, state: BridgeState,
                         config: BridgeConfig) -> None:
    if hosts(funestra, state, config):
        append_command(config.origenerator_cmd_file, f"{funestra.label}_{action}")


def _parked(window_name: str, _state: BridgeState, _config: BridgeConfig,
            _target_path: str) -> Result:
    return _state, [WindowOp(op="minimize_window", key=window_name)]


def _parked_satellite(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
                      target_path: str) -> Result:
    return _parked(funestra.label, state, config, target_path)


def _placed(placing, state: BridgeState, config: BridgeConfig, _target_path: str) -> Result:
    return placing(state, config), []


def _main_hud(placing) -> Handler:
    return partial(_placed, placing)


def _satellite_hud(placing) -> SatelliteHandler:
    def handler(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
                target_path: str) -> Result:
        return _placed(partial(placing, funestra), state, config, target_path)
    return handler


def _main_hud_restored_at(command: Command, state: BridgeState, config: BridgeConfig,
                          _target_path: str) -> Result:
    return _restored_at(FunTimeFunestra.MAIN, command.argument, state, config), []


def _satellite_hud_restored_at(command: Command, state: BridgeState, config: BridgeConfig,
                               _target_path: str) -> Result:
    return _restored_at(command.satellite, command.argument, state, config), []


HANDLERS: dict[str, Handler] = {
    "main_minimize": partial(_parked, "main_funestra"),
    "main_hud_minimize": _main_hud(partial(_collapsed_hud, FunTimeFunestra.MAIN, minimized=True)),
    "main_hud_restore": _main_hud(partial(_collapsed_hud, FunTimeFunestra.MAIN, minimized=False)),
    **{command: _main_hud(partial(_main_hud_turned, clockwise))
       for command, clockwise in MAIN_HUD_TURNS.items()},
}

VALUED: dict[str, Route] = {"main_hud_restore_at": _main_hud_restored_at}

SATELLITE_ACTIONS: dict[str, SatelliteHandler] = {
    "minimize": _parked_satellite,
    "hud_minimize": _satellite_hud(partial(_collapsed_hud, minimized=True)),
    "hud_restore": _satellite_hud(partial(_collapsed_hud, minimized=False)),
    **{f"hud_{direction}": _satellite_hud(partial(_satellite_hud_moved, direction))
       for direction in _DIRECTIONS},
}

SATELLITE_VALUED: dict[str, Route] = {"hud_restore_at": _satellite_hud_restored_at}

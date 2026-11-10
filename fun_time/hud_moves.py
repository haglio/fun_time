from __future__ import annotations

from dataclasses import replace
from functools import partial

from funestra.file_channel import append_command
from funestra.hud_placement import HudCorner, HudEdge

from .bridge_records import BridgeConfig, Handler, Result, Route, SatelliteHandler, WindowOp
from .command_value import Command
from .fun_time_funestras import FunTimeFunestra
from .shared_state import BridgeState

_DIRECTIONS = ("left", "right", "up", "down")


def _restored_at(funestra: FunTimeFunestra, place: str, state: BridgeState,
                 config: BridgeConfig) -> BridgeState:
    if config.vr_main_funestra:
        return _hung_open_against(funestra, place, state)
    return _opened_in(funestra, place, state, config)


def _hud_corner_of(funestra: FunTimeFunestra, state: BridgeState) -> HudCorner:
    if funestra is FunTimeFunestra.MAIN:
        return state.main_hud_corner
    return state.satellite(funestra).hud_corner


def _hud_edge_of(funestra: FunTimeFunestra, state: BridgeState) -> HudEdge:
    if funestra is FunTimeFunestra.MAIN:
        return state.main_hud_edge
    return state.satellite(funestra).hud_edge


def _hung_against(funestra: FunTimeFunestra, edge: HudEdge, state: BridgeState) -> BridgeState:
    if funestra is FunTimeFunestra.MAIN:
        return replace(state, main_hud_edge=edge)
    return state.with_satellite(funestra, hud_edge=edge)


def _hung_open_against(funestra: FunTimeFunestra, edge: str, state: BridgeState) -> BridgeState:
    if edge not in HudEdge:
        return state
    return _opened(funestra, _hung_against(funestra, HudEdge(edge), state), None)


def _put_in(funestra: FunTimeFunestra, corner: HudCorner, state: BridgeState,
            config: BridgeConfig) -> BridgeState:
    if funestra is FunTimeFunestra.MAIN:
        return replace(state, main_hud_corner=corner)
    _tell_the_hosted_app(funestra, f"hud_corner|{corner}", config)
    return state.with_satellite(funestra, hud_corner=corner)


def _opened(funestra: FunTimeFunestra, state: BridgeState,
            hosted_app_told_through: BridgeConfig | None) -> BridgeState:
    if funestra is FunTimeFunestra.MAIN:
        return replace(state, main_hud_minimized=False)
    if hosted_app_told_through is not None:
        _tell_the_hosted_app(funestra, "hud_minimized|0", hosted_app_told_through)
    return state.with_satellite(funestra, hud_minimized=False)


def _opened_in(funestra: FunTimeFunestra, corner: str, state: BridgeState,
               config: BridgeConfig) -> BridgeState:
    if corner not in HudCorner:
        return state
    return _opened(funestra, _put_in(funestra, HudCorner(corner), state, config), config)


def _collapsed_hud(funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, *,
                   minimized: bool) -> BridgeState:
    if funestra is FunTimeFunestra.MAIN:
        return replace(state, main_hud_minimized=minimized)
    _tell_the_hosted_app(funestra, f"hud_minimized|{1 if minimized else 0}", config)
    return state.with_satellite(funestra, hud_minimized=minimized)


def _hud_moved(direction: str, funestra: FunTimeFunestra, state: BridgeState,
               config: BridgeConfig) -> BridgeState:
    if config.vr_main_funestra:
        return _hung_against(funestra, _hud_edge_of(funestra, state).toward(direction), state)
    return _put_in(funestra, _hud_corner_of(funestra, state).toward(direction), state, config)


def _hud_placed(corner: HudCorner, funestra: FunTimeFunestra, state: BridgeState,
                config: BridgeConfig) -> BridgeState:
    if config.vr_main_funestra:
        return _hung_against(funestra, HudEdge.LOWER if corner.lower else HudEdge.UPPER, state)
    return _put_in(funestra, corner, state, config)


def _tell_the_hosted_app(funestra: FunTimeFunestra, action: str, config: BridgeConfig) -> None:
    if funestra in FunTimeFunestra.SATELLITES:
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
    **{f"main_hud_{direction}": _main_hud(partial(_hud_moved, direction, FunTimeFunestra.MAIN))
       for direction in _DIRECTIONS},
    **{f"main_hud_{corner}": _main_hud(partial(_hud_placed, corner, FunTimeFunestra.MAIN))
       for corner in HudCorner},
}

VALUED: dict[str, Route] = {"main_hud_restore_at": _main_hud_restored_at}

SATELLITE_ACTIONS: dict[str, SatelliteHandler] = {
    "minimize": _parked_satellite,
    "hud_minimize": _satellite_hud(partial(_collapsed_hud, minimized=True)),
    "hud_restore": _satellite_hud(partial(_collapsed_hud, minimized=False)),
    **{f"hud_{direction}": _satellite_hud(partial(_hud_moved, direction))
       for direction in _DIRECTIONS},
    **{f"hud_{corner}": _satellite_hud(partial(_hud_placed, corner)) for corner in HudCorner},
}

SATELLITE_VALUED: dict[str, Route] = {"hud_restore_at": _satellite_hud_restored_at}

from __future__ import annotations

from dataclasses import replace

from funestra.file_channel import append_command
from funestra.hud_placement import HudCorner, HudEdge

from .bridge_records import BridgeConfig, WindowOp
from .fun_time_funestras import FunTimeFunestra
from .origenerator_switches import hosts
from .shared_state import BridgeState

_MINIMIZE_WINDOWS: dict[str, str] = {
    "portrait_minimize": "portrait",
    "landscape_minimize": "landscape",
    "main_minimize": "main_funestra",
}


_HUD_MOVES = {f"{funestra.label}_hud_{direction}": (funestra, direction)
              for funestra in FunTimeFunestra
              for direction in ("left", "right", "up", "down")}


_HUD_PLACEMENTS = {f"{funestra.label}_hud_{corner.value}": (funestra, corner)
                   for funestra in FunTimeFunestra
                   for corner in HudCorner}


_HUD_COLLAPSES = {f"{funestra.label}_hud_{verb}": (funestra, verb == "minimize")
                  for funestra in FunTimeFunestra
                  for verb in ("minimize", "restore")}


_HUD_RESTORES_AT = {f"{funestra.label}_hud_restore_at": funestra for funestra in FunTimeFunestra}


def _restored_at(command: str, state: BridgeState,
                 config: BridgeConfig) -> BridgeState | None:
    head, _, place = command.partition("|")
    funestra = _HUD_RESTORES_AT.get(head)
    if funestra is None:
        return None
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
    _tell_the_hosted_app(funestra, f"hud_corner|{corner}", state, config)
    return state.with_satellite(funestra, hud_corner=corner)


def _opened(funestra: FunTimeFunestra, state: BridgeState,
            hosted_app_told_through: BridgeConfig | None) -> BridgeState:
    if funestra is FunTimeFunestra.MAIN:
        return replace(state, main_hud_minimized=False)
    if hosted_app_told_through is not None:
        _tell_the_hosted_app(funestra, "hud_minimized|0", state, hosted_app_told_through)
    return state.with_satellite(funestra, hud_minimized=False)


def _opened_in(funestra: FunTimeFunestra, corner: str, state: BridgeState,
               config: BridgeConfig) -> BridgeState:
    if corner not in HudCorner:
        return state
    return _opened(funestra, _put_in(funestra, HudCorner(corner), state, config), config)


def _collapsed_hud(command: str, state: BridgeState,
                   config: BridgeConfig) -> BridgeState | None:
    collapse = _HUD_COLLAPSES.get(command)
    if collapse is None:
        return None
    funestra, minimized = collapse
    if funestra is FunTimeFunestra.MAIN:
        return replace(state, main_hud_minimized=minimized)
    _tell_the_hosted_app(funestra, f"hud_minimized|{1 if minimized else 0}", state, config)
    return state.with_satellite(funestra, hud_minimized=minimized)


def _moved_hud(command: str, state: BridgeState,
               config: BridgeConfig) -> BridgeState | None:
    moved = _HUD_MOVES.get(command)
    if moved is not None:
        funestra, direction = moved
        if config.vr_main_funestra:
            return _hung_against(funestra, _hud_edge_of(funestra, state).toward(direction), state)
        return _put_in(funestra, _hud_corner_of(funestra, state).toward(direction), state, config)
    placed = _HUD_PLACEMENTS.get(command)
    if placed is not None:
        funestra, corner = placed
        if config.vr_main_funestra:
            return _hung_against(funestra, HudEdge.LOWER if corner.lower else HudEdge.UPPER, state)
        return _put_in(funestra, corner, state, config)
    return None


def _tell_the_hosted_app(funestra: FunTimeFunestra, action: str, state: BridgeState,
                         config: BridgeConfig) -> None:
    if hosts(funestra, state, config):
        append_command(config.origenerator_cmd_file, f"{funestra.label}_{action}")


def minimize_ops(command: str) -> list[WindowOp] | None:
    window_name = _MINIMIZE_WINDOWS.get(command)
    return None if window_name is None else [WindowOp(op="minimize_window", key=window_name)]


def placed_hud(command: str, state: BridgeState, config: BridgeConfig) -> BridgeState | None:
    return (_moved_hud(command, state, config) or _collapsed_hud(command, state, config)
            or _restored_at(command, state, config))

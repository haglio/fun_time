from __future__ import annotations

from dataclasses import replace

from funestra.file_channel import append_command
from funestra.hud_placement import HudCorner, HudEdge

from .bridge_records import BridgeConfig, WindowOp
from .origenerator_switches import hosts
from .room_funestras import RoomFunestra
from .shared_state import BridgeState

# A Funestra's own minimize button (``fun_time.satellite_buttons``, and the Main
# Funestra's on its console, ``fun_time.console_buttons``), by the window role the
# dispatch loop resolves it to.  Every Funestra's window here is borderless, so
# none of them carries a minimize button of its own, and the only other way to
# park one was the dashboard's minimize — which takes the whole room down
# together.
_MINIMIZE_ROLES: dict[str, str] = {
    "portrait_minimize": "portrait",
    "landscape_minimize": "landscape",
    "main_minimize": "main_funestra",
}


_HUD_MOVES = {f"{funestra.label}_hud_{direction}": (funestra, direction)
              for funestra in RoomFunestra
              for direction in ("left", "right", "up", "down")}


_HUD_PLACEMENTS = {f"{funestra.label}_hud_{corner.value}": (funestra, corner)
                   for funestra in RoomFunestra
                   for corner in HudCorner}


_HUD_COLLAPSES = {f"{funestra.label}_hud_{verb}": (funestra, verb == "minimize")
                  for funestra in RoomFunestra
                  for verb in ("minimize", "restore")}


_HUD_RESTORES_AT = {f"{funestra.label}_hud_restore_at": funestra for funestra in RoomFunestra}


def _restored_at(command: str, state: BridgeState,
                 config: BridgeConfig) -> BridgeState | None:
    head, _, place = command.partition("|")
    funestra = _HUD_RESTORES_AT.get(head)
    if funestra is None:
        return None
    if config.vr_main_funestra:
        return _hung_open_against(funestra, place, state)
    return _opened_in(funestra, place, state, config)


def _hud_corner_of(funestra: RoomFunestra, state: BridgeState) -> HudCorner:
    if funestra is RoomFunestra.MAIN:
        return state.main_hud_corner
    return state.satellite(funestra).hud_corner


def _hud_edge_of(funestra: RoomFunestra, state: BridgeState) -> HudEdge:
    if funestra is RoomFunestra.MAIN:
        return state.main_hud_edge
    return state.satellite(funestra).hud_edge


def _hung_against(funestra: RoomFunestra, edge: HudEdge, state: BridgeState) -> BridgeState:
    if funestra is RoomFunestra.MAIN:
        return replace(state, main_hud_edge=edge)
    return state.with_satellite(funestra, hud_edge=edge)


def _hung_open_against(funestra: RoomFunestra, side: str, state: BridgeState) -> BridgeState:
    if side not in HudEdge:
        return state
    return _opened(funestra, _hung_against(funestra, HudEdge(side), state), None)


def _put_in(funestra: RoomFunestra, corner: HudCorner, state: BridgeState,
            config: BridgeConfig) -> BridgeState:
    if funestra is RoomFunestra.MAIN:
        return replace(state, main_hud_corner=corner)
    _tell_the_hosted_app(funestra, f"hud_corner|{corner}", state, config)
    return state.with_satellite(funestra, hud_corner=corner)


def _opened(funestra: RoomFunestra, state: BridgeState,
            hosted_app_told_through: BridgeConfig | None) -> BridgeState:
    if funestra is RoomFunestra.MAIN:
        return replace(state, main_hud_minimized=False)
    if hosted_app_told_through is not None:
        _tell_the_hosted_app(funestra, "hud_minimized|0", state, hosted_app_told_through)
    return state.with_satellite(funestra, hud_minimized=False)


def _opened_in(funestra: RoomFunestra, corner: str, state: BridgeState,
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
    if funestra is RoomFunestra.MAIN:
        return replace(state, main_hud_minimized=minimized)
    _tell_the_hosted_app(funestra, f"hud_minimized|{1 if minimized else 0}", state, config)
    return state.with_satellite(funestra, hud_minimized=minimized)


def _moved_hud(command: str, state: BridgeState,
               config: BridgeConfig) -> BridgeState | None:
    """A step toward a side, or a corner named outright; in the headset a HUD
    hangs against a side of its Funestra, so a corner's height is the side."""
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


def _tell_the_hosted_app(funestra: RoomFunestra, action: str, state: BridgeState,
                         config: BridgeConfig) -> None:
    if hosts(funestra, state, config):
        append_command(config.origenerator_cmd_file, f"{funestra.label}_{action}")


def minimize_ops(command: str) -> list[WindowOp] | None:
    """The window *command* asks to have parked, or None when it asks for none."""
    role = _MINIMIZE_ROLES.get(command)
    return None if role is None else [WindowOp(op="minimize_role", key=role)]


def placed_hud(command: str, state: BridgeState, config: BridgeConfig) -> BridgeState | None:
    """*state* with the HUD *command* moves, collapses or reopens placed so, or
    None for a command about no HUD."""
    return (_moved_hud(command, state, config) or _collapsed_hud(command, state, config)
            or _restored_at(command, state, config))

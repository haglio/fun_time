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
              for funestra in RoomFunestra.SATELLITES
              for direction in ("left", "right", "up", "down")}


MAIN_HUD_TURNS = {"main_hud_clockwise": True, "main_hud_counterclockwise": False}


_HUD_COLLAPSES = {f"{funestra.label}_hud_{verb}": (funestra, verb == "minimize")
                  for funestra in (*RoomFunestra.SATELLITES, RoomFunestra.MAIN)
                  for verb in ("minimize", "restore")}


_HUD_RESTORES_AT = {f"{funestra.label}_hud_restore_at": funestra
                    for funestra in (*RoomFunestra.SATELLITES, RoomFunestra.MAIN)}


def _restored_at(command: str, state: BridgeState,
                 config: BridgeConfig) -> BridgeState | None:
    head, _, place = command.partition("|")
    funestra = _HUD_RESTORES_AT.get(head)
    if funestra is None:
        return None
    if config.vr_main_funestra:
        return _hung_open_against(funestra, place, state)
    return _opened_in(funestra, place, state, config)


def _hung_open_against(funestra: RoomFunestra, side: str, state: BridgeState) -> BridgeState:
    if side not in HudEdge:
        return state
    if funestra is RoomFunestra.MAIN:
        return replace(state, main_hud_edge=HudEdge(side), main_hud_minimized=False)
    return state.with_satellite(funestra, hud_edge=HudEdge(side), hud_minimized=False)


def _opened_in(funestra: RoomFunestra, corner: str, state: BridgeState,
               config: BridgeConfig) -> BridgeState:
    if corner not in HudCorner:
        return state
    if funestra is RoomFunestra.MAIN:
        return replace(state, main_hud_corner=HudCorner(corner), main_hud_minimized=False)
    _tell_the_hosted_app(funestra, f"hud_corner|{corner}", state, config)
    _tell_the_hosted_app(funestra, "hud_minimized|0", state, config)
    return state.with_satellite(funestra, hud_corner=HudCorner(corner), hud_minimized=False)


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
    moved = _HUD_MOVES.get(command)
    if moved is not None:
        funestra, direction = moved
        return _satellite_hud_moved(funestra, state, config, direction=direction)
    if command in MAIN_HUD_TURNS:
        clockwise = MAIN_HUD_TURNS[command]
        if config.vr_main_funestra:
            return replace(state, main_hud_edge=state.main_hud_edge.turned(
                clockwise=clockwise))
        return replace(state, main_hud_corner=state.main_hud_corner.turned(
            clockwise=clockwise))
    return None


def _satellite_hud_moved(funestra: RoomFunestra, state: BridgeState, config: BridgeConfig, *,
                         direction: str) -> BridgeState:
    side = state.satellite(funestra)
    if config.vr_main_funestra:
        return state.with_satellite(funestra, hud_edge=side.hud_edge.toward(direction))
    corner = side.hud_corner.toward(direction)
    _tell_the_hosted_app(funestra, f"hud_corner|{corner}", state, config)
    return state.with_satellite(funestra, hud_corner=corner)


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

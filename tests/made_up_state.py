from __future__ import annotations

from dataclasses import fields, replace
from enum import Enum

from fun_time.room_funestras import RoomFunestra
from fun_time.session_resume import RESUMED_FIELDS, RESUMED_SATELLITE_FIELDS
from fun_time.shared_state import BridgeState, SatelliteState


def _shifted(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, Enum):
        return next(entry for entry in type(value) if entry is not value)
    if isinstance(value, int | float):
        return value + 1
    return f"{value}-carried"


def shifted_state(state: BridgeState) -> BridgeState:
    shifted = replace(state, **{
        f.name: _shifted(getattr(state, f.name)) for f in fields(state)
        if not isinstance(getattr(state, f.name), SatelliteState)})
    for funestra in RoomFunestra.SATELLITES:
        side = shifted.satellite(funestra)
        shifted = shifted.with_satellite(
            funestra, **{f.name: _shifted(getattr(side, f.name)) for f in fields(side)})
    return shifted


def remembered_values(state: BridgeState) -> dict[str, object]:
    values = {name: getattr(state, name) for name in RESUMED_FIELDS
              if not isinstance(getattr(state, name), SatelliteState)}
    for funestra in RoomFunestra.SATELLITES:
        side = state.satellite(funestra)
        values |= {f"{funestra.label}_{name}": getattr(side, name)
                   for name in RESUMED_SATELLITE_FIELDS}
    return values

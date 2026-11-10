from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import xr

from .controller_bindings import AIM, BACK, FORWARD, STICK_CLICK, STICK_X, STICK_Y, TRIGGER
from .pointer import LEFT, RIGHT, HandInput

_LOCATED = xr.SpaceLocationFlags.ORIENTATION_VALID_BIT | xr.SpaceLocationFlags.POSITION_VALID_BIT


def no_hands() -> dict[str, HandInput]:
    return {LEFT: HandInput(), RIGHT: HandInput()}


@dataclass(frozen=True)
class ControllerActions:
    session: Any
    action_set: Any
    actions: dict[str, Any]
    hands: tuple[tuple[str, Any], ...]
    aim_spaces: dict[str, Any]
    runtime: Any = xr

    def read(self, display_time: int, space: Any) -> dict[str, HandInput]:
        return _read_hands(self, display_time, space)


def _read_hands(controllers: ControllerActions, display_time: int, space: Any) -> dict[str, HandInput]:
    runtime = controllers.runtime
    try:
        runtime.sync_actions(controllers.session, runtime.ActionsSyncInfo(active_action_sets=[
            runtime.ActiveActionSet(action_set=controllers.action_set, subaction_path=0),
        ]))
        return {hand: _hand_input(controllers, hand, path, (display_time, space))
                for hand, path in controllers.hands}
    except xr.ResultException:
        return no_hands()


def _state(controllers: ControllerActions, read, name: str, path: Any):
    get_info = controllers.runtime.ActionStateGetInfo(action=controllers.actions[name], subaction_path=path)
    return read(controllers.session, get_info)


def _float(controllers: ControllerActions, name: str, path: Any) -> float:
    state = _state(controllers, controllers.runtime.get_action_state_float, name, path)
    return state.current_state if state.is_active else 0.0


def _pressed(controllers: ControllerActions, name: str, path: Any) -> bool:
    state = _state(controllers, controllers.runtime.get_action_state_boolean, name, path)
    return bool(state.is_active and state.current_state)


def _aim(controllers: ControllerActions, hand: str, path: Any, when_and_where: tuple[int, Any]):
    if not _state(controllers, controllers.runtime.get_action_state_pose, AIM, path).is_active:
        return None
    display_time, space = when_and_where
    location = controllers.runtime.locate_space(controllers.aim_spaces[hand], space, display_time)
    if location.location_flags & _LOCATED != _LOCATED:
        return None
    position, orientation = location.pose.position, location.pose.orientation
    return (
        (position.x, position.y, position.z),
        (orientation.x, orientation.y, orientation.z, orientation.w),
    )


def _hand_input(controllers: ControllerActions, hand: str, for_this_hand_alone: Any,
                when_and_where: tuple[int, Any]) -> HandInput:
    return HandInput(
        aim=_aim(controllers, hand, for_this_hand_alone, when_and_where),
        trigger=_float(controllers, TRIGGER, for_this_hand_alone),
        stick_x=_float(controllers, STICK_X, for_this_hand_alone),
        stick_y=_float(controllers, STICK_Y, for_this_hand_alone),
        stick_pressed=_pressed(controllers, STICK_CLICK, for_this_hand_alone),
        forward=_pressed(controllers, FORWARD, for_this_hand_alone),
        back=_pressed(controllers, BACK, for_this_hand_alone),
    )

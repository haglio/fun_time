"""What the session binds on each controller the family meets, and the actions it reads."""
from __future__ import annotations

import xr

from .pointer import LEFT, RIGHT

STICK_X = "thumbstick_x"
STICK_Y = "thumbstick_y"
STICK_CLICK = "thumbstick_click"
AIM = "aim"
TRIGGER = "trigger"
FORWARD = "skip_forward"
BACK = "skip_back"

HAND_PATHS = {LEFT: "/user/hand/left", RIGHT: "/user/hand/right"}


def _either_hand(input_path: str) -> tuple[str, ...]:
    return tuple(f"{hand}/input/{input_path}" for hand in HAND_PATHS.values())


def _by_hand(right_input: str, left_input: str) -> tuple[str, ...]:
    return (f"{HAND_PATHS[RIGHT]}/input/{right_input}",
            f"{HAND_PATHS[LEFT]}/input/{left_input}")


CONTROLLER_BINDINGS: dict[str, dict[str, tuple[str, ...]]] = {
    "/interaction_profiles/oculus/touch_controller": {
        STICK_X: _either_hand("thumbstick/x"),
        STICK_Y: _either_hand("thumbstick/y"),
        STICK_CLICK: _either_hand("thumbstick/click"),
        AIM: _either_hand("aim/pose"),
        TRIGGER: _either_hand("trigger/value"),
        FORWARD: _by_hand("a/click", "x/click"),
        BACK: _by_hand("b/click", "y/click"),
    },
    "/interaction_profiles/valve/index_controller": {
        STICK_X: _either_hand("thumbstick/x"),
        STICK_Y: _either_hand("thumbstick/y"),
        STICK_CLICK: _either_hand("thumbstick/click"),
        AIM: _either_hand("aim/pose"),
        TRIGGER: _either_hand("trigger/value"),
        FORWARD: _either_hand("a/click"),
        BACK: _either_hand("b/click"),
    },
    "/interaction_profiles/htc/vive_controller": {
        STICK_X: _either_hand("trackpad/x"),
        STICK_Y: _either_hand("trackpad/y"),
        STICK_CLICK: _either_hand("trackpad/click"),
        AIM: _either_hand("aim/pose"),
        TRIGGER: _either_hand("trigger/value"),
    },
    "/interaction_profiles/khr/simple_controller": {
        AIM: _either_hand("aim/pose"),
        TRIGGER: _either_hand("select/click"),
    },
}

ACTION_TYPES = {
    STICK_X: xr.ActionType.FLOAT_INPUT,
    STICK_Y: xr.ActionType.FLOAT_INPUT,
    STICK_CLICK: xr.ActionType.BOOLEAN_INPUT,
    AIM: xr.ActionType.POSE_INPUT,
    TRIGGER: xr.ActionType.FLOAT_INPUT,
    FORWARD: xr.ActionType.BOOLEAN_INPUT,
    BACK: xr.ActionType.BOOLEAN_INPUT,
}

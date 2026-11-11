from __future__ import annotations

from types import MappingProxyType

CYCLE_PROJECTION = "CYCLE_PROJECTION"
CYCLE_PROJECTION_BACK = "CYCLE_PROJECTION_BACK"
RECENTER = "RECENTER"
LAYOUT_RESET = "LAYOUT_RESET"
TILT_UP = "TILT_UP"
TILT_DOWN = "TILT_DOWN"
TILT_RESET = "TILT_RESET"
NEXT_SCENE = "NEXT_SCENE"
PREV_SCENE = "PREV_SCENE"

VR_MAIN_FUNESTRA_VERBS = MappingProxyType({
    "projection_cycle": CYCLE_PROJECTION,
    "projection_cycle_back": CYCLE_PROJECTION_BACK,
    "recenter_view": RECENTER,
    "tilt_up": TILT_UP,
    "tilt_down": TILT_DOWN,
    "tilt_reset": TILT_RESET,
    "main_scene_prev": PREV_SCENE,
    "main_scene_next": NEXT_SCENE,
    "vr_reset": LAYOUT_RESET,
})

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .pointer import HandInput

CONTROLLER_DEADZONE = 0.1
DOUBLINGS_PER_S = 1.0

NUDGE_FORWARD = "main_nudge_next"
NUDGE_BACK = "main_nudge_prev"
NEXT_SCENE = "main_scene_next"
PREVIOUS_SCENE = "main_scene_prev"


def strongest(axes: Iterable[float]) -> float:
    return max(axes, key=abs, default=0.0)


@dataclass(frozen=True)
class Thumb:
    grow: float = 1.0
    nearer: float = 1.0
    commands: tuple[str, ...] = ()
    settled: bool = False


class Thumbs:
    def __init__(self) -> None:
        self._down: dict[str, tuple[bool, bool]] = {}
        self._moving = False

    def frame(self, hands: Mapping[str, HandInput], squeeze, *, elapsed_s: float) -> Thumb:
        commands = self._pressed(hands, squeezing=squeeze.squeezing)
        stick = strongest(hand.stick for hand in hands.values())
        moving = abs(stick) > CONTROLLER_DEADZONE
        settled, self._moving = self._moving and not moving, moving
        if squeeze.squeezing and (moving or commands):
            squeeze.spend_the_squeeze()
        if not moving:
            return Thumb(commands=commands, settled=settled)
        factor = 2.0 ** (-stick * elapsed_s * DOUBLINGS_PER_S)
        if squeeze.squeezing:
            return Thumb(nearer=factor, commands=commands)
        return Thumb(grow=factor, commands=commands)

    def _pressed(self, hands: Mapping[str, HandInput], *, squeezing: bool) -> tuple[str, ...]:
        forward, back = (NEXT_SCENE, PREVIOUS_SCENE) if squeezing else (NUDGE_FORWARD, NUDGE_BACK)
        commands = []
        for name, hand in hands.items():
            was_forward, was_back = self._down.get(name, (False, False))
            if hand.forward and not was_forward:
                commands.append(forward)
            if hand.back and not was_back:
                commands.append(back)
            self._down[name] = (hand.forward, hand.back)
        return tuple(commands)

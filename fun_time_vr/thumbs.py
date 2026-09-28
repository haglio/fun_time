from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .pointer import LEFT, RIGHT, HandInput

CONTROLLER_DEADZONE = 0.1
DOUBLINGS_PER_S = 1.0
DIALING_SHARE = 0.4

NUDGE_FORWARD = "main_nudge_next"
NUDGE_BACK = "main_nudge_prev"
NEXT_SCENE = "main_scene_next"
PREVIOUS_SCENE = "main_scene_prev"
PROJECTION_ON = "projection_cycle"
PROJECTION_BACK = "projection_cycle_back"

SIDEWAYS_PUSH = 0.6
SIDEWAYS_REST = 0.3


def strongest(axes: Iterable[float]) -> float:
    return max(axes, key=abs, default=0.0)


def _mostly_sideways(hand: HandInput) -> bool:
    return abs(hand.stick_x) > abs(hand.stick_y)


def _upright(hand: HandInput) -> float:
    return 0.0 if _mostly_sideways(hand) else hand.stick_y


def _doubled_by(push: float, elapsed_s: float, share: float = 1.0) -> float:
    return 2.0 ** (-push * elapsed_s * DOUBLINGS_PER_S * share)


def _dialed(push: float, elapsed_s: float) -> float:
    return _doubled_by(push, elapsed_s, DIALING_SHARE) if abs(push) > CONTROLLER_DEADZONE else 1.0


@dataclass(frozen=True)
class Thumb:
    grow: float = 1.0
    nearer: float = 1.0
    zoom: float = 1.0
    stretch: float = 1.0
    commands: tuple[str, ...] = ()
    settled: bool = False


class Thumbs:
    def __init__(self) -> None:
        self._down: dict[str, tuple[bool, bool]] = {}
        self._moving = False
        self._pushed_sideways = 0

    def frame(
        self, hands: Mapping[str, HandInput], squeeze, *, elapsed_s: float, dialing: bool = False,
    ) -> Thumb:
        pressed = self._pressed(hands, squeezing=squeeze.squeezing)
        commands = pressed + self._stepped(hands)
        pushes = {name: _upright(hand) for name, hand in hands.items()}
        dial = dialing and not squeeze.squeezing
        push = strongest([pushes.get(LEFT, 0.0), pushes.get(RIGHT, 0.0)] if dial else pushes.values())
        moving = abs(push) > CONTROLLER_DEADZONE
        settled, self._moving = self._moving and not moving, moving
        if squeeze.squeezing and (moving or pressed):
            squeeze.spend_the_squeeze()
        if not moving:
            return Thumb(commands=commands, settled=settled)
        if squeeze.squeezing:
            return Thumb(nearer=_doubled_by(push, elapsed_s), commands=commands)
        if dial:
            return Thumb(
                zoom=_dialed(pushes.get(LEFT, 0.0), elapsed_s),
                stretch=_dialed(pushes.get(RIGHT, 0.0), elapsed_s), commands=commands)
        return Thumb(grow=_doubled_by(push, elapsed_s), commands=commands)

    def _stepped(self, hands: Mapping[str, HandInput]) -> tuple[str, ...]:
        sideways = strongest(hand.stick_x for hand in hands.values() if _mostly_sideways(hand))
        if abs(sideways) < SIDEWAYS_REST:
            self._pushed_sideways = 0
        pushed = (sideways > SIDEWAYS_PUSH) - (sideways < -SIDEWAYS_PUSH)
        if not pushed or pushed == self._pushed_sideways:
            return ()
        self._pushed_sideways = pushed
        return (PROJECTION_ON,) if pushed > 0 else (PROJECTION_BACK,)

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

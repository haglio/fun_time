from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from funestra.control_registry import Control, Verb, bind, look_up
from funestra.funestra_verbs import RELOAD_PLAYLIST

from .snippet_verbs import RECORD_DOWN, RECORD_TAP, RECORD_UP, SET_SNIPPET, SNIPPET_CANCEL

__all__ = [
    "VERBS",
    "KinoControls",
    "apply_command",
]


@dataclass
class KinoControls:
    playback: Any
    snippet: Any
    versions: Any
    modes: Any
    jumps: Any
    reload_playlist: Callable[[], None]
    clock: Callable[[], float] = time.monotonic


Act = Callable[[KinoControls, str], bool]


def _playhead(controls: KinoControls) -> int:
    return int(controls.playback.position_ms)


LONGEST_A_PRESS_TAKES_TO_ARRIVE_S = 5.0


def _playhead_when_pressed(controls: KinoControls, pressed_at: str) -> int:
    try:
        gone = controls.clock() - float(pressed_at)
    except ValueError:
        return _playhead(controls)
    if controls.playback.is_paused or not 0.0 <= gone <= LONGEST_A_PRESS_TAKES_TO_ARRIVE_S:
        return _playhead(controls)
    played_since_ms = gone * 1000.0 * controls.playback.speed
    return int(max(0.0, controls.playback.position_ms - played_since_ms))


def _record_down(controls: KinoControls, pressed_at: str) -> bool:
    controls.snippet.record_down(_playhead_when_pressed(controls, pressed_at))
    return True


def _record_up(controls: KinoControls, pressed_at: str) -> bool:
    controls.snippet.record_up(_playhead_when_pressed(controls, pressed_at))
    return True


def _record_tap(controls: KinoControls, pressed_at: str) -> bool:
    controls.snippet.record_tap(_playhead_when_pressed(controls, pressed_at))
    return True


def _snippet_cancel(controls: KinoControls, _value: str) -> bool:
    controls.snippet.cancel()
    return True


def _set_snippet(controls: KinoControls, value: str) -> bool:
    in_part, _, out_part = value.partition(" ")
    try:
        in_ms, out_ms = int(in_part), int(out_part)
    except ValueError:
        return False
    controls.snippet.restore(in_ms, out_ms)
    return True


def _cycle_version(step: int) -> Act:
    def act(controls: KinoControls, _value: str) -> bool:
        controls.versions.cycle(step)
        return True
    return act


def _reload_playlist(controls: KinoControls, _value: str) -> bool:
    controls.reload_playlist()
    return True


def _end_compilation(controls: KinoControls, _value: str) -> bool:
    controls.modes.end_compilation()
    return True


def _play_compilation(controls: KinoControls, _value: str) -> bool:
    controls.jumps.play_compilation()
    return True


def _play_full_vid(controls: KinoControls, _value: str) -> bool:
    controls.jumps.play_full_vid()
    return True


def _play_clip_jump(controls: KinoControls, _value: str) -> bool:
    controls.jumps.play_clip_jump()
    return True


CONTROLS: tuple[Control, ...] = (
    Control(
        name="snippet",
        verbs=(
            Verb(RECORD_DOWN, _record_down, may_name_its_item=True),
            Verb(RECORD_UP, _record_up, may_name_its_item=True),
            Verb(RECORD_TAP, _record_tap, may_name_its_item=True),
            Verb(SNIPPET_CANCEL, _snippet_cancel),
            Verb(SET_SNIPPET, _set_snippet, takes_a_value=True),
        ),
    ),
    Control(
        name="version",
        verbs=(
            Verb("CYCLE_VERSION", _cycle_version(1)),
            Verb("CYCLE_VERSION_BACK", _cycle_version(-1)),
        ),
    ),
    Control(name="playlist", verbs=(Verb(RELOAD_PLAYLIST, _reload_playlist),)),
    Control(
        name="compilation",
        verbs=(
            Verb("PLAY_COMPILATION", _play_compilation),
            Verb("END_COMPILATION", _end_compilation),
            Verb("PLAY_FULL_VID", _play_full_vid),
            Verb("PLAY_CLIP_JUMP", _play_clip_jump),
        ),
    ),
)


VERBS = bind(CONTROLS)


def apply_command(command: str, controls: KinoControls) -> bool:
    return look_up(command, VERBS, controls)

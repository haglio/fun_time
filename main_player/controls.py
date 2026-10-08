"""The verbs Kino answers off the Main Funestra's command file, declared once.

The Funestra asks Kino about every line first and answers the rest itself, so
what is here is only what Kino knows and the Funestra does not: the loop
gestures, the other versions the library knows, the length modes, Fun Time's own
filter, the compilation and funscript jumps, and the playlist Kino reads back
through its library.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from player_core.control_registry import Control, Verb, bind, look_up
from player_core.funestra_controls import SEEK_STEP_MS
from player_core.player_verbs import RELOAD_PLAYLIST, SET_F_MODE

from .loop_verbs import LOOP_CANCEL, RECORD_DOWN, RECORD_TAP, RECORD_UP, SET_LOOP

__all__ = [
    "VERBS",
    "KinoControls",
    "apply_command",
    "longer_than_a_step",
]


def longer_than_a_step(duration_ms: float) -> bool:
    return duration_ms > SEEK_STEP_MS


@dataclass
class KinoControls:
    playback: Any
    loops: Any
    versions: Any
    modes: Any
    jumps: Any
    funscript_jumps: Any
    reload_playlist: Callable[[], None]


Act = Callable[[KinoControls, str], bool]


def _playhead(controls: KinoControls) -> int:
    return int(controls.playback.position_ms)


def _record_down(controls: KinoControls, _value: str) -> bool:
    controls.loops.record_down(_playhead(controls))
    return True


def _record_up(controls: KinoControls, _value: str) -> bool:
    controls.loops.record_up(_playhead(controls))
    return True


def _record_tap(controls: KinoControls, _value: str) -> bool:
    controls.loops.record_tap(_playhead(controls))
    return True


def _loop_cancel(controls: KinoControls, _value: str) -> bool:
    controls.loops.cancel()
    return True


def _set_loop(controls: KinoControls, value: str) -> bool:
    in_part, _, out_part = value.partition(" ")
    try:
        in_ms, out_ms = int(in_part), int(out_part)
    except ValueError:
        return False
    controls.loops.restore(in_ms, out_ms)
    return True


def _cycle_version(step: int) -> Act:
    def act(controls: KinoControls, _value: str) -> bool:
        controls.versions.cycle(step)
        return True
    return act


def _reload_playlist(controls: KinoControls, _value: str) -> bool:
    controls.reload_playlist()
    return True


def _toggle_length_mode(controls: KinoControls, _value: str) -> bool:
    controls.modes.toggle_length()
    return True


def _set_length_mode(controls: KinoControls, value: str) -> bool:
    controls.modes.set_length(value)
    return True


def _end_compilation(controls: KinoControls, _value: str) -> bool:
    controls.modes.end_compilation()
    return True


def _set_scripted_filter(controls: KinoControls, value: str) -> bool:
    controls.modes.set_scripted_filter(value != "0")
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


def _jump_to_funscript(controls: KinoControls, _value: str) -> bool:
    controls.funscript_jumps.jump_to_funscript()
    return True


def _next_funscripted(controls: KinoControls, _value: str) -> bool:
    controls.funscript_jumps.next_funscripted()
    return True


CONTROLS: tuple[Control, ...] = (
    Control(
        name="loop",
        verbs=(
            Verb(RECORD_DOWN, _record_down),
            Verb(RECORD_UP, _record_up),
            Verb(RECORD_TAP, _record_tap),
            Verb(LOOP_CANCEL, _loop_cancel),
            Verb(SET_LOOP, _set_loop, takes_a_value=True),
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
        name="length_mode",
        verbs=(
            Verb("TOGGLE_LENGTH_MODE", _toggle_length_mode),
            Verb("SET_LENGTH_MODE", _set_length_mode, takes_a_value=True),
            Verb("END_COMPILATION", _end_compilation),
        ),
    ),
    Control(
        name="f_mode",
        verbs=(Verb(SET_F_MODE, _set_scripted_filter, takes_a_value=True),),
    ),
    Control(
        name="compilation",
        verbs=(
            Verb("PLAY_COMPILATION", _play_compilation),
            Verb("PLAY_FULL_VID", _play_full_vid),
            Verb("PLAY_CLIP_JUMP", _play_clip_jump),
        ),
    ),
    Control(
        name="funscript_jump",
        verbs=(
            Verb("JUMP_TO_FUNSCRIPT", _jump_to_funscript),
            Verb("NEXT_FUNSCRIPTED", _next_funscripted),
        ),
    ),
)


VERBS = bind(CONTROLS)


def apply_command(command: str, controls: KinoControls) -> bool:
    return look_up(command, VERBS, controls)

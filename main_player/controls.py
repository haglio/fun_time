"""What the main player's controls can reach, and every verb that moves one.

Fun Time speaks to this player by writing verbs into ``main_player_cmd.txt``, one per
line, which the frame loop drains.  Every one of them has to be able to move the
same handful of things: the session under the window, the length filter and the
compilation it is in, the two kinds of jump, the volume chip, whether this
window paints at all.

Passing those one at a time is what made the dispatcher fifteen parameters wide
and its call site fourteen lines long: thirteen optional callbacks, each with
its own ``is None`` guard inside its own branch, and no way to see from any of
them what the whole control was.  They travel together here instead, built once
where the app is wired and handed whole.

Optional means *this build did not wire it* — a standalone main player with no
orchestrator, a test that only cares about the session.  A verb whose
collaborator is absent is refused and logged rather than half-acted-on, which is
the behavior :func:`apply_command` documents.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from player_core.control_registry import Control, Verb, bind, look_up
from player_core.playback_rate import RATE_STEP, parse_rate
from player_core.player_verbs import (
    DISPLAY_OFF,
    DISPLAY_ON,
    LOCK_OFF,
    LOCK_ON,
    NEXT,
    PLAY_FILE,
    PREV,
    QUIT,
    RELOAD_PLAYLIST,
    SEEK_BACK,
    SEEK_FWD,
    SET_F_MODE,
    SET_SPEED,
    SET_TCODE_ENABLED,
    SET_VOLUME,
    SPEED_DOWN,
    SPEED_UP,
    TOGGLE_LOCK,
)
from player_core.playlist import item_from_line

__all__ = [
    "SEEK_STEP_MS",
    "VERBS",
    "MainPlayerControls",
    "apply_command",
]

logger = logging.getLogger(__name__)

SEEK_STEP_MS = 10_000


@dataclass
class MainPlayerControls:
    """Everything one command from the orchestrator may move."""

    session: Any
    stop_event: threading.Event | None = None
    reload_playlist: Callable[[], None] | None = None
    modes: Any = None
    jumps: Any = None
    funscript_jumps: Any = None
    set_volume_hud: Callable[[int, bool], None] | None = None
    set_display: Callable[[bool], None] | None = None


# The acts below all take these controls and the rest of the line, and say
# whether they could.
Act = Callable[[MainPlayerControls, str], bool]


def _stepper(step: int) -> Act:
    def act(controls: MainPlayerControls, _value: str) -> bool:
        controls.session.step(step)
        return True
    return act


def _seeker(delta_ms: int) -> Act:
    def act(controls: MainPlayerControls, _value: str) -> bool:
        controls.session.seek_by(delta_ms)
        return True
    return act


def _speed_step(delta: float) -> Act:
    def act(controls: MainPlayerControls, _value: str) -> bool:
        controls.session.adjust_speed(delta)
        return True
    return act


def _set_speed(controls: MainPlayerControls, value: str) -> bool:
    """``SET_SPEED <min|max|multiplier>`` — an absolute playback rate.

    False on a value it cannot read as a rate, which :func:`apply_command`
    turns into the log line; the rate is left where it was.
    """
    rate = parse_rate(value)
    if rate is None:
        return False
    controls.session.set_speed(rate)
    return True


def _record_down(controls: MainPlayerControls, _value: str) -> bool:
    controls.session.record_down()
    return True


def _record_up(controls: MainPlayerControls, _value: str) -> bool:
    controls.session.record_up()
    return True


def _record_tap(controls: MainPlayerControls, _value: str) -> bool:
    """One-button record cycle: start marking -> finish loop -> cancel."""
    state = controls.session.loop_state
    if state == "normal":
        controls.session.record_down()
    elif state == "recording":
        controls.session.record_up()
    else:
        controls.session.loop_cancel()
    return True


def _loop_cancel(controls: MainPlayerControls, _value: str) -> bool:
    controls.session.loop_cancel()
    return True


def _set_loop(controls: MainPlayerControls, value: str) -> bool:
    """``SET_LOOP <in_ms> <out_ms>`` — a loop this player was left running.

    The one piece of the main player's state an orchestrator has to hand back rather than
    rebuild: a loop is a range inside one video, so it dies with the process
    while everything else rides in on the playlist or a flag file.  The bounds
    come straight off the status file this player published, already snapped, so
    they are asserted rather than marked.  False on anything it cannot read as
    two numbers, which :func:`apply_command` turns into the log line.
    """
    in_part, _, out_part = value.partition(" ")
    try:
        in_ms, out_ms = int(in_part), int(out_part)
    except ValueError:
        return False
    controls.session.restore_loop(in_ms, out_ms)
    return True


def _toggle_lock(controls: MainPlayerControls, _value: str) -> bool:
    controls.session.toggle_lock()
    return True


def _lock_set(locked: bool) -> Act:
    """Named absolutely as well as toggled, because the spoken forms are "main
    lock" and "main unlock": a speaker asks for the state they want, not for the
    other one."""
    def act(controls: MainPlayerControls, _value: str) -> bool:
        controls.session.set_locked(locked)
        return True
    return act


def _cycle_version(controls: MainPlayerControls, _value: str) -> bool:
    controls.session.cycle_version()
    return True


def _play_file(controls: MainPlayerControls, value: str) -> bool:
    """``PLAY_FILE`` carries one playlist line — the one verb whose value is a
    path, and so the reason the keyword alone is upper-cased."""
    item = item_from_line(value)
    if item is None:
        return False
    controls.session.play_file(item.path, item.funscript)
    return True


def _set_tcode_enabled(controls: MainPlayerControls, value: str) -> bool:
    controls.session.set_tcode_enabled(value != "0")
    return True


def _set_volume(controls: MainPlayerControls, value: str) -> bool:
    """``SET_VOLUME <0-100> [muted]`` — the main slot's sound level.

    The mute comes as a flag of its own rather than as a level of zero.  Zero is
    what an audio *sink* needs and all Fun Time used to send, but a control that
    has to be looked at cannot tell silent from turned-all-the-way-down from it —
    and unmuting has to come back to the level the speaker chose.  So the level is
    what is drawn, the mute is drawn over it, and the audible loudness is worked
    out here.  False on a level it cannot read as a number, which
    :func:`apply_command` turns into the log line.
    """
    level, _, muted_arg = value.partition(" ")
    try:
        volume = int(level)
    except ValueError:
        return False
    muted = muted_arg.strip() not in ("", "0")
    controls.session.set_volume(0 if muted else volume)
    controls.set_volume_hud(volume, muted)
    return True


def _reload_playlist(controls: MainPlayerControls, _value: str) -> bool:
    controls.reload_playlist()
    return True


def _toggle_length_mode(controls: MainPlayerControls, _value: str) -> bool:
    controls.modes.toggle_length()
    return True


def _set_length_mode(controls: MainPlayerControls, value: str) -> bool:
    controls.modes.set_length(value)
    return True


def _end_compilation(controls: MainPlayerControls, _value: str) -> bool:
    """Out of a compilation without naming a length: back to the mode that was
    feeding the playlist when it was entered."""
    controls.modes.end_compilation()
    return True


def _set_f_mode(controls: MainPlayerControls, value: str) -> bool:
    """F-mode narrows the playlist Fun Time writes to the scripted videos.  The main player
    receives the result and cannot tell it from any other playlist, so the flag
    has to be said outright for the HUD to be able to show it."""
    controls.modes.set_f_mode(value != "0")
    return True


def _play_compilation(controls: MainPlayerControls, _value: str) -> bool:
    controls.jumps.play_compilation()
    return True


def _play_full_vid(controls: MainPlayerControls, _value: str) -> bool:
    controls.jumps.play_full_vid()
    return True


def _play_clip_jump(controls: MainPlayerControls, _value: str) -> bool:
    controls.jumps.play_clip_jump()
    return True


def _jump_to_funscript(controls: MainPlayerControls, _value: str) -> bool:
    """Past the quiet stretch, to where this video's scripting starts again."""
    controls.funscript_jumps.jump_to_funscript()
    return True


def _next_funscripted(controls: MainPlayerControls, _value: str) -> bool:
    """Give up on this video for the next scripted one, at its action."""
    controls.funscript_jumps.next_funscripted()
    return True


def _display_set(active: bool) -> Act:
    """Whether the main player owns the main slot's rect right now, which is not the same as
    whether it is playing: Fun Time hands that rect to Genau in genau mode and
    minimizes the main player, and a minimized window keeps its taskbar button — so without
    this an alt-tab back lands on the frame it was paused on.  The mirror of the
    DISPLAY_ON/DISPLAY_OFF Genau is sent (see :mod:`main_player.display`)."""
    def act(controls: MainPlayerControls, _value: str) -> bool:
        controls.set_display(active)
        return True
    return act


def _quit(controls: MainPlayerControls, _value: str) -> bool:
    controls.stop_event.set()
    return True


# One entry per thing a person can move.  Add a control by adding a record here;
# nothing else in the app needs to learn its name.
CONTROLS: tuple[Control, ...] = (
    Control(
        name="playlist_position",
        verbs=(Verb(NEXT, _stepper(1)), Verb(PREV, _stepper(-1))),
    ),
    Control(
        name="playhead",
        verbs=(
            Verb(SEEK_FWD, _seeker(SEEK_STEP_MS)),
            Verb(SEEK_BACK, _seeker(-SEEK_STEP_MS)),
        ),
    ),
    Control(
        name="speed",
        verbs=(
            Verb(SPEED_UP, _speed_step(RATE_STEP)),
            Verb(SPEED_DOWN, _speed_step(-RATE_STEP)),
            Verb(SET_SPEED, _set_speed, takes_a_value=True),
        ),
    ),
    # Held rather than tapped, then tapped as well: pressing marks the loop's in
    # point and letting go marks its out point, and RECORD_TAP is the one-button
    # spelling of the same gesture for a speaker or a dashboard button.
    Control(
        name="loop",
        verbs=(
            Verb("RECORD_DOWN", _record_down),
            Verb("RECORD_UP", _record_up),
            Verb("RECORD_TAP", _record_tap),
            Verb("LOOP_CANCEL", _loop_cancel),
            Verb("SET_LOOP", _set_loop, takes_a_value=True),
        ),
    ),
    Control(
        name="lock",
        verbs=(
            Verb(TOGGLE_LOCK, _toggle_lock),
            Verb(LOCK_ON, _lock_set(True)),
            Verb(LOCK_OFF, _lock_set(False)),
        ),
    ),
    Control(name="version", verbs=(Verb("CYCLE_VERSION", _cycle_version),)),
    Control(
        name="playing_file",
        verbs=(Verb(PLAY_FILE, _play_file, takes_a_value=True),),
    ),
    Control(
        name="tcode_output",
        verbs=(Verb(SET_TCODE_ENABLED, _set_tcode_enabled, takes_a_value=True),),
    ),
    Control(
        name="volume",
        needs=("set_volume_hud",),
        verbs=(Verb(SET_VOLUME, _set_volume, takes_a_value=True),),
    ),
    # Fun Time owns the playlist file and rewrites it whenever the room's
    # selection changes; this is how it says so.
    Control(
        name="playlist",
        needs=("reload_playlist",),
        verbs=(Verb(RELOAD_PLAYLIST, _reload_playlist),),
    ),
    Control(
        name="length_mode",
        needs=("modes",),
        verbs=(
            Verb("TOGGLE_LENGTH_MODE", _toggle_length_mode),
            Verb("SET_LENGTH_MODE", _set_length_mode, takes_a_value=True),
            Verb("END_COMPILATION", _end_compilation),
        ),
    ),
    Control(
        name="f_mode",
        needs=("modes",),
        verbs=(Verb(SET_F_MODE, _set_f_mode, takes_a_value=True),),
    ),
    Control(
        name="compilation",
        needs=("jumps",),
        verbs=(
            Verb("PLAY_COMPILATION", _play_compilation),
            Verb("PLAY_FULL_VID", _play_full_vid),
            Verb("PLAY_CLIP_JUMP", _play_clip_jump),
        ),
    ),
    Control(
        name="funscript_jump",
        needs=("funscript_jumps",),
        verbs=(
            Verb("JUMP_TO_FUNSCRIPT", _jump_to_funscript),
            Verb("NEXT_FUNSCRIPTED", _next_funscripted),
        ),
    ),
    Control(
        name="display",
        needs=("set_display",),
        verbs=(
            Verb(DISPLAY_ON, _display_set(True)),
            Verb(DISPLAY_OFF, _display_set(False)),
        ),
    ),
    Control(name="quit", needs=("stop_event",), verbs=(Verb(QUIT, _quit),)),
)


VERBS = bind(CONTROLS)


def apply_command(command: str, controls: MainPlayerControls) -> None:
    """Act on one line of the command file, or say on the log that we cannot.

    The dispatcher reports an unanswered verb itself rather than returning a
    flag for a caller to check: it is the only thing that knows, and there is
    one of it rather than one per call site. Three kinds land here — a verb no
    branch matches, a verb whose collaborator this build did not wire, and a
    verb that came with a value it does not take or without one it needs — and
    all three mean the same thing to whoever sent it, which is that nothing
    happened.

    Fun Time is written against this. ``command_dispatch.py`` routes
    CYCLE_PROJECTION and RECENTER to the main player's channel with the comment "so the
    desktop main player simply logs it as unknown": verbs only FunTimeVR's player
    answers, sent to whoever holds the main slot.
    """
    if not look_up(command, VERBS, controls):
        logger.warning("Unhandled command: %s", command.strip())

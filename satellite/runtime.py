"""The verbs a satellite answers, declared once.

fun_time writes one verb per line to the satellite's command file; the run loop
drains them and looks each one up here.  Pause is NOT a verb — it rides its own
flag file (like the main player), so a paused satellite is a settled state rather
than a verb race.  A satellite is silent and unscripted, so its vocabulary is the
part of the family's (:mod:`player_core.player_verbs`) that is about the list,
the clip on screen and the rate it plays at: no sound, no funscript.
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
    LOCK_OFF,
    LOCK_ON,
    NEXT,
    PLAY_FILE,
    PREV,
    QUIT,
    RELOAD_PLAYLIST,
    SET_PACE,
    SET_SPEED,
    SPEED_DOWN,
    SPEED_UP,
    TRASH,
    pace_seconds,
)
from player_core.playlist import item_from_line

from .versions import NEXT_VERSION, PREV_VERSION, version_files

logger = logging.getLogger(__name__)


@dataclass
class SatelliteControls:
    """Everything one command from fun_time may move.

    Optional means *this build did not wire it*: the headset's satellites have
    no stop event of their own — the session ends as a whole — and a test that
    only cares about the session hands no reload.  A verb whose collaborator is
    absent is refused rather than half-acted-on.
    """

    session: Any
    stop_event: threading.Event | None = None
    reload_playlist: Callable[[], None] | None = None


Act = Callable[[SatelliteControls, str], bool]


def _stepper(step: int) -> Act:
    def act(controls: SatelliteControls, _value: str) -> bool:
        controls.session.step(step)
        return True
    return act


def _lock_set(locked: bool) -> Act:
    def act(controls: SatelliteControls, _value: str) -> bool:
        controls.session.set_locked(locked)
        return True
    return act


def _discard(controls: SatelliteControls, _value: str) -> bool:
    controls.session.discard()
    return True


def _speed_step(delta: float) -> Act:
    def act(controls: SatelliteControls, _value: str) -> bool:
        controls.session.set_speed(controls.session.speed + delta)
        return True
    return act


def _set_speed(controls: SatelliteControls, value: str) -> bool:
    rate = parse_rate(value)
    if rate is None:
        return False
    controls.session.set_speed(rate)
    return True


def _play_file(controls: SatelliteControls, value: str) -> bool:
    """``PLAY_FILE`` carries one playlist line; a satellite drops its funscript."""
    item = item_from_line(value)
    if item is None:
        return False
    controls.session.play_file(item.path)
    return True


def _step_version(delta: int) -> Act:
    def act(controls: SatelliteControls, value: str) -> bool:
        versions = version_files(value)
        if not versions:
            return False
        controls.session.step_version(versions, delta)
        return True
    return act


def _reload_playlist(controls: SatelliteControls, _value: str) -> bool:
    controls.reload_playlist()
    return True


def _quit(controls: SatelliteControls, _value: str) -> bool:
    controls.stop_event.set()
    return True


def _set_pace(controls: SatelliteControls, value: str) -> bool:
    seconds = pace_seconds(value)
    if seconds is None:
        return False
    controls.session.set_pace(seconds)
    return True


CONTROLS: tuple[Control, ...] = (
    Control(
        name="playlist_position",
        verbs=(Verb(NEXT, _stepper(1)), Verb(PREV, _stepper(-1))),
    ),
    Control(
        name="lock",
        verbs=(Verb(LOCK_ON, _lock_set(True)), Verb(LOCK_OFF, _lock_set(False))),
    ),
    Control(name="clip", verbs=(Verb(TRASH, _discard),)),
    Control(
        name="version",
        verbs=(
            Verb(NEXT_VERSION, _step_version(1), takes_a_value=True),
            Verb(PREV_VERSION, _step_version(-1), takes_a_value=True),
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
    Control(
        name="playing_file",
        verbs=(Verb(PLAY_FILE, _play_file, takes_a_value=True),),
    ),
    Control(name="pace", verbs=(Verb(SET_PACE, _set_pace, takes_a_value=True),)),
    Control(
        name="playlist",
        needs=("reload_playlist",),
        verbs=(Verb(RELOAD_PLAYLIST, _reload_playlist),),
    ),
    Control(name="quit", needs=("stop_event",), verbs=(Verb(QUIT, _quit),)),
)


VERBS = bind(CONTROLS)


def apply_command(command: str, controls: SatelliteControls) -> bool:
    """Act on one line of the command file; whether anything answered it.

    An unanswered line — a verb no control declares, one whose collaborator
    this build did not wire, or one with a value it does not take — goes on
    the log as well: the sender cannot tell the three apart, and a log line
    is the only place any of them shows.
    """
    handled = look_up(command, VERBS, controls)
    if not handled:
        logger.warning("Unhandled command: %s", command.strip())
    return handled

from __future__ import annotations

from functools import partial

from funestra.file_channel import append_command
from funestra.hud_placement import HudCorner

from .bridge_records import BridgeConfig, Route, SatelliteHandler, WindowOp
from .command_value import Command
from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import slideshows_enhancement
from .hud_transport import the_hosted_slideshow_is_locked
from .lock import a_hosted_lock_holds
from .mode_plan import EnhancementLevel
from .osr2_commands import PLAYBACK_RATE_ACTS, percent_rate
from .satellite_groups import lock_on_what_was_meant, send_satellite
from .shared_state import BridgeState

LOCK_ON_LED_BY_THE_SESSION = "lock_on_led_by_the_session"


def _lock_as_said(
    locked: bool, funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig,
    target_path: str = "",
) -> tuple[BridgeState, list[WindowOp]]:
    if locked:
        return _lock_and_go_where_it_leads(funestra, state, config, target_path)
    return _let_go(funestra, state, config)


def _toggle_lock(
    funestra: FunTimeFunestra, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    return _lock_as_said(
        not the_hosted_slideshow_is_locked(config.satellite(funestra).origenerator_hud_file),
        funestra, state, config, target_path)


def _lock_and_go_where_it_leads(funestra: FunTimeFunestra, state: BridgeState,
                                config: BridgeConfig,
                                target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    locked_on = lock_on_what_was_meant(funestra, config, target_path)
    where_it_leads = a_hosted_lock_holds(locked_on, state, config)
    _tell_the_hosted_app(config, Command.to(funestra, LOCK_ON_LED_BY_THE_SESSION).text)
    return where_it_leads


def _let_go(funestra: FunTimeFunestra, state: BridgeState,
            config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
    _tell_the_hosted_app(config, Command.to(funestra, "lock_off").text)
    return state, []


def _tell_the_hosted_app(config: BridgeConfig, said: str) -> None:
    append_command(config.origenerator.cmd_file, said)


def _send_to_satellite(verb: str, funestra: FunTimeFunestra, state: BridgeState,
                       config: BridgeConfig, _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    send_satellite(config, funestra, verb)
    return state, []


LOCK_HELD = "lock_held"


def _hosted_lock(command: Command, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return a_hosted_lock_holds(command.argument, state, config)


def _speed(command: Command, state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    verb = percent_rate(command.argument)
    if verb is None:
        return state, []
    send_satellite(config, command.satellite, verb)
    return state, []


def _enhancement(level: EnhancementLevel, funestra: FunTimeFunestra, state: BridgeState,
                 config: BridgeConfig, _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return slideshows_enhancement(funestra, level, state, config)


_PARKING_ITS_WINDOW = ("minimize",)
_ITS_CROWN_AND_THE_OSR2 = ("crown", "take_osr2")
_ITS_LOCK = ("lock", "lock_on", "lock_off", LOCK_HELD)
_THE_RATE_IT_PLAYS_AT = ("speed", *PLAYBACK_RATE_ACTS)
_WHERE_ITS_HUD_SITS = (
    "hud_minimize", "hud_restore", "hud_restore_at", "hud_left", "hud_right", "hud_up", "hud_down",
    *(f"hud_{corner}" for corner in HudCorner),
)
_ITS_SOUND = ("mute", "unmute", "volume_down", "volume_up")
_ITS_ENHANCEMENT = tuple(f"enhancement_{level}" for level in EnhancementLevel)

THE_FUNESTRAS_OWN = frozenset({
    *_PARKING_ITS_WINDOW, *_ITS_CROWN_AND_THE_OSR2, *_ITS_LOCK,
    *_THE_RATE_IT_PLAYS_AT, *_WHERE_ITS_HUD_SITS, *_ITS_SOUND, *_ITS_ENHANCEMENT,
})


SATELLITE_ACTIONS: dict[str, SatelliteHandler] = {
    **{act: partial(_send_to_satellite, verb) for act, verb in PLAYBACK_RATE_ACTS.items()},
    "lock": _toggle_lock,
    "lock_on": partial(_lock_as_said, True),
    "lock_off": partial(_lock_as_said, False),
    **{f"enhancement_{level}": partial(_enhancement, level) for level in EnhancementLevel},
}

SATELLITE_VALUED: dict[str, Route] = {
    LOCK_HELD: _hosted_lock,
    "speed": _speed,
}

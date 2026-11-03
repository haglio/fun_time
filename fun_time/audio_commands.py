from __future__ import annotations

from dataclasses import replace
from functools import partial

from .audio_volume import MAX_VOLUME, MIN_VOLUME, VOLUME_STEP, publish_audio_level
from .bridge_records import BridgeConfig, Handler, WindowOp
from .event_log import SOURCE_MAIN
from .shared_state import BridgeState

_VOLUME_STEPS = {"audio_volume_down": -VOLUME_STEP, "audio_volume_up": VOLUME_STEP}


SET_VOLUME_COMMAND = "audio_set_volume"


def _dispatch_set_volume(
    argument: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    try:
        level = int(argument)
    except ValueError:
        return state, []
    volume = max(MIN_VOLUME, min(MAX_VOLUME, level))
    return _dispatch_audio(replace(state, volume=volume, muted=False), config)


_MUTE_COMMANDS = {"audio_mute": True, "audio_unmute": False}


def _step_volume(state: BridgeState, step: int) -> BridgeState:
    volume = max(MIN_VOLUME, min(MAX_VOLUME, state.volume + step))
    return replace(state, volume=volume, muted=False)


def _dispatch_audio(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    publish_audio_level(
        main_funestra_cmd_file=config.main_funestra_cmd_file,
        genau_cmd_file=config.genau_cmd_file,
        audio_volume_file=config.audio_volume_file,
        volume=state.volume,
        muted=state.muted,
    )
    message = "Muted" if state.muted else f"Volume {state.volume}%"
    return state, [WindowOp(op="message", key=message, source=SOURCE_MAIN)]


def _set_muted(muted: bool, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_audio(replace(state, muted=muted), config)


def _volume_step(step: int, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_audio(_step_volume(state, step), config)


def _parsed_set_volume(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if not command.startswith(f"{SET_VOLUME_COMMAND}|"):
        return None
    return _dispatch_set_volume(command.partition("|")[2], state, config)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_set_muted, muted) for cmd, muted in _MUTE_COMMANDS.items()},
    **{cmd: partial(_volume_step, step) for cmd, step in _VOLUME_STEPS.items()},
}

PARSED_FORMS = (_parsed_set_volume,)

from __future__ import annotations

from dataclasses import replace
from functools import partial

from .audio_volume import MAX_VOLUME, MIN_VOLUME, VOLUME_STEP, publish_audio_level
from .bridge_records import BridgeConfig, Handler, WindowOp
from .event_log import SOURCE_MAIN
from .shared_state import BridgeState

_VOLUME_STEPS = {"audio_volume_down": -VOLUME_STEP, "audio_volume_up": VOLUME_STEP}


# ``audio_set_volume|<0-100>`` — an absolute level, which is what a slider asks
# for where every other audio command asks for a step or a state.  The Main Funestra's volume
# control is the one that sends it.
SET_VOLUME_COMMAND = "audio_set_volume"


def _dispatch_set_volume(
    argument: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Set the sound level outright, lifting any mute.

    The level crosses a text channel from another process, so it is not trusted:
    out of range is clamped and unparseable is ignored, rather than raising into
    the dispatch loop over a dragged slider.  It lifts a mute for the reason a
    step does — reaching for the volume is asking to hear something.
    """
    try:
        level = int(argument)
    except ValueError:
        return state, []
    volume = max(MIN_VOLUME, min(MAX_VOLUME, level))
    return _dispatch_audio(replace(state, volume=volume, muted=False), config)


# Mute and unmute each assert a state rather than toggling one, so a phrase
# misheard twice cannot leave the sound the opposite of what was asked for.
_MUTE_COMMANDS = {"audio_mute": True, "audio_unmute": False}


def _step_volume(state: BridgeState, step: int) -> BridgeState:
    """Move the sound level by *step*, staying within the silent/full bounds.

    Asking for a loudness lifts a mute, as reaching for the volume does in the
    Windows mixer: a "louder" that left the room silent would read as the
    command having been missed.
    """
    volume = max(MIN_VOLUME, min(MAX_VOLUME, state.volume + step))
    return replace(state, volume=volume, muted=False)


def _dispatch_audio(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Publish *state*'s sound level to both of the Main Funestra's audio sinks,
    and say on screen what it now is."""
    publish_audio_level(
        main_funestra_cmd_file=config.main_funestra_cmd_file,
        genau_cmd_file=config.genau_cmd_file,
        audio_volume_file=config.audio_volume_file,
        volume=state.volume,
        muted=state.muted,
    )
    message = "Muted" if state.muted else f"Volume {state.volume}%"
    return state, [WindowOp(op="notice", key=message, source=SOURCE_MAIN)]


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

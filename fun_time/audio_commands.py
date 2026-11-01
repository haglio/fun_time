from __future__ import annotations

from dataclasses import replace
from functools import partial

from .aiming import FUNESTRA_SOURCES
from .audio_volume import MAX_VOLUME, MIN_VOLUME, VOLUME_STEP, publish_audio_level, volume_verb
from .bridge_records import BridgeConfig, Handler, WindowOp
from .event_log import SOURCE_MAIN
from .room_funestras import RoomFunestra
from .satellite_groups import send_satellite
from .shared_state import BridgeState

_STEP_BY = {"volume_down": -VOLUME_STEP, "volume_up": VOLUME_STEP}
# Mute and unmute each assert a state rather than toggling one, so a phrase
# misheard twice cannot leave the sound the opposite of what was asked for.
_MUTED_BY = {"mute": True, "unmute": False}
# The Main Funestra's sound, under the ids its own console posts; each
# satellite's is told to that satellite alone, the way its speed is.
_VOLUME_STEPS = {f"audio_{change}": step for change, step in _STEP_BY.items()}
_MUTE_COMMANDS = {f"audio_{change}": muted for change, muted in _MUTED_BY.items()}
_SATELLITE_SOUND = {f"{funestra.label}_{change}": (funestra, change)
                    for funestra in RoomFunestra.SATELLITES
                    for change in (*_MUTED_BY, *_STEP_BY)}


# ``audio_set_volume|<0-100>`` — an absolute level, which is what a slider asks
# for where every other audio command asks for a step or a state.  The Main Funestra's volume
# control is the one that sends it.
SET_VOLUME_COMMAND = "audio_set_volume"


def _level_moved(volume: int, step: int) -> int:
    return max(MIN_VOLUME, min(MAX_VOLUME, volume + step))


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
    return _dispatch_audio(replace(state, volume=_level_moved(level, 0), muted=False), config)


def _step_volume(state: BridgeState, step: int) -> BridgeState:
    """Move the sound level by *step*, staying within the silent/full bounds.

    Asking for a loudness lifts a mute, as reaching for the volume does in the
    Windows mixer: a "louder" that left the room silent would read as the
    command having been missed.
    """
    return replace(state, volume=_level_moved(state.volume, step), muted=False)


def _sound_notice(volume: int, muted: bool, source: str) -> WindowOp:
    return WindowOp(op="notice", key="Muted" if muted else f"Volume {volume}%", source=source)


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
    return state, [_sound_notice(state.volume, state.muted, SOURCE_MAIN)]


def _set_muted(muted: bool, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_audio(replace(state, muted=muted), config)


def _volume_step(step: int, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_audio(_step_volume(state, step), config)


def _satellite_sound(funestra: RoomFunestra, change: str, state: BridgeState,
                     config: BridgeConfig, _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    side = state.satellite(funestra)
    if change in _MUTED_BY:
        volume, muted = side.volume, _MUTED_BY[change]
    else:
        volume, muted = _level_moved(side.volume, _STEP_BY[change]), False
    send_satellite(config, funestra, volume_verb(volume, muted))
    return (state.with_satellite(funestra, volume=volume, muted=muted),
            [_sound_notice(volume, muted, FUNESTRA_SOURCES[funestra])])


def _parsed_set_volume(command: str, state: BridgeState, config: BridgeConfig,
                       _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    if not command.startswith(f"{SET_VOLUME_COMMAND}|"):
        return None
    return _dispatch_set_volume(command.partition("|")[2], state, config)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_set_muted, muted) for cmd, muted in _MUTE_COMMANDS.items()},
    **{cmd: partial(_volume_step, step) for cmd, step in _VOLUME_STEPS.items()},
    **{cmd: partial(_satellite_sound, funestra, change)
       for cmd, (funestra, change) in _SATELLITE_SOUND.items()},
}

PARSED_FORMS = (_parsed_set_volume,)

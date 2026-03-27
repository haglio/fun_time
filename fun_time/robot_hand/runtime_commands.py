from __future__ import annotations

from .engine import PlaybackEngine


QUARTER_CYCLE_OFFSET_COMMAND = "OFFSET_QUARTER_CYCLE"
LEGACY_QUARTER_CYCLE_OFFSET_COMMAND = "NUDGE25"


def get_engine_estimated_bpm(engine: PlaybackEngine) -> float | None:
    return None if engine.estimated_bpm is None else float(engine.estimated_bpm)


def apply_runtime_command(command, *, engine: PlaybackEngine, rh_paused, step_clip) -> bool:
    if not command:
        return False

    normalized = command.strip().upper()
    if normalized == "PREV":
        step_clip(-1)
    elif normalized == "NEXT":
        step_clip(1)
    elif normalized in {QUARTER_CYCLE_OFFSET_COMMAND, LEGACY_QUARTER_CYCLE_OFFSET_COMMAND}:
        engine.phase = (engine.phase + 0.25) % 1.0
    elif normalized == "PAUSE":
        rh_paused["value"] = True
    elif normalized == "RESUME":
        rh_paused["value"] = False
    else:
        return False
    return True

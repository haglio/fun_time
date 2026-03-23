from __future__ import annotations


QUARTER_CYCLE_OFFSET_COMMAND = "OFFSET_QUARTER_CYCLE"
LEGACY_QUARTER_CYCLE_OFFSET_COMMAND = "NUDGE25"


def get_engine_phase(engine) -> float:
    if isinstance(engine, dict):
        return float(engine["phase"])
    return float(engine.phase)


def set_engine_phase(engine, value: float) -> None:
    if isinstance(engine, dict):
        engine["phase"] = value
    else:
        engine.phase = value


def get_engine_estimated_bpm(engine) -> float | None:
    if isinstance(engine, dict):
        value = engine.get("estimated_bpm")
    else:
        value = engine.estimated_bpm
    return None if value is None else float(value)


def apply_runtime_command(command, *, engine, rh_paused, step_clip) -> bool:
    if not command:
        return False

    normalized = command.strip().upper()
    if normalized == "PREV":
        step_clip(-1)
    elif normalized == "NEXT":
        step_clip(1)
    elif normalized in {QUARTER_CYCLE_OFFSET_COMMAND, LEGACY_QUARTER_CYCLE_OFFSET_COMMAND}:
        set_engine_phase(engine, (get_engine_phase(engine) + 0.25) % 1.0)
    elif normalized == "PAUSE":
        rh_paused["value"] = True
    elif normalized == "RESUME":
        rh_paused["value"] = False
    else:
        return False
    return True

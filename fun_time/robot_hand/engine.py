from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlaybackEngine:
    phase: float = 0.0
    estimated_bpm: float | None = None
    target_bpm: float | None = None
    last_tick: float = 0.0
    seen_sync_pulse_id: int = 0


def update_engine(
    engine: PlaybackEngine,
    *,
    now: float,
    auto_active: bool,
    raw_bpm: float | None,
    sync_pulse_id: int,
    beats_per_loop: float,
    bpm_smoothing: float,
    sync_strength: float,
    paused: bool,
) -> float | None:
    dt = now - engine.last_tick
    engine.last_tick = now
    dt = max(0.0, min(dt, 0.1))

    if raw_bpm is not None:
        engine.target_bpm = float(raw_bpm)
        if engine.estimated_bpm is None:
            engine.estimated_bpm = float(raw_bpm)

    if engine.estimated_bpm is not None and engine.target_bpm is not None:
        alpha = max(0.0, min(1.0, bpm_smoothing))
        engine.estimated_bpm = engine.estimated_bpm + (engine.target_bpm - engine.estimated_bpm) * alpha

    if auto_active and engine.estimated_bpm and engine.estimated_bpm > 0 and not paused:
        loop_duration = (60.0 / engine.estimated_bpm) * beats_per_loop
        engine.phase = (engine.phase + (dt / loop_duration)) % 1.0
    else:
        loop_duration = None

    if sync_pulse_id != engine.seen_sync_pulse_id:
        engine.seen_sync_pulse_id = sync_pulse_id
        phase = engine.phase
        error = -phase if phase <= 0.5 else (1.0 - phase)
        strength = max(0.0, min(1.0, sync_strength))
        engine.phase = (engine.phase + error * strength) % 1.0

    return loop_duration

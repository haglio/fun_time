from __future__ import annotations


def exception_status_text(message: str, *, log_name: str) -> str:
    return f"Error: {message}\nSee {log_name}"


def listener_error_status_text(message: str) -> str:
    return f"Error:\n{message}"


def loading_status_text(*, clip_name: str, clip_index: int, clip_count: int, loading: bool) -> str:
    return (
        f"clip={clip_name}\n"
        f"clip_index={clip_index}/{clip_count}\n"
        f"loading={loading}\n"
        f"keys=[ and ] switch clips"
    )


def active_clip_status_text(
    *,
    clip_name: str,
    clip_index: int,
    clip_count: int,
    frame_index: int,
    frame_count: int,
    visible: bool,
    auto_active: bool,
    phase: float,
    raw_bpm,
    estimated_bpm: float | None,
    beats,
    loop_duration,
    stroke_name: str,
    pattern_duration,
    loading: bool,
    last_msg: str,
) -> str:
    est_bpm_text = f"{estimated_bpm:.2f}" if estimated_bpm is not None else "n/a"
    return (
        f"clip={clip_name}\n"
        f"clip_index={clip_index}/{clip_count}\n"
        f"frame={frame_index}/{frame_count}\n"
        f"visible={visible}\n"
        f"state={'auto-on' if auto_active else 'auto-off'}\n"
        f"phase={phase:.3f}\n"
        f"raw_bpm={raw_bpm}\n"
        f"est_bpm={est_bpm_text}\n"
        f"beats={beats}\n"
        f"loop_duration={loop_duration}\n"
        f"stroke={stroke_name}\n"
        f"pattern_duration={pattern_duration}\n"
        f"loading={loading}\n"
        f"last_msg={last_msg}\n"
        f"keys=[ and ] switch clips"
    )

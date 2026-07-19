"""What a satellite publishes in its status file for the dispatch loop.

fun_time reads its current clip, playhead and pause/lock state from here — the
watch-sampler and the lock HUD's own model both do — which is what the retired
VLC satellites needed an HTTP status.xml poll for.  The throttled writing itself
is :class:`player_core.status.StatusWriter`; this module is only the field set,
because these keys are a satellite's own contract with the dispatch loop (Nau
publishes a different set, and ``locked`` is meaningless to it).
"""
from __future__ import annotations


def status_fields(session) -> dict[str, str]:
    return {
        "video": str(session.current_video),
        "position_ms": str(int(session.position_ms)),
        "duration_ms": str(int(session.duration_ms)),
        "paused": "1" if session.is_paused else "0",
        "locked": "1" if session.is_locked else "0",
    }

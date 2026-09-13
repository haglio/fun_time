"""What a satellite publishes in its status file for the dispatch loop.

fun_time reads its current clip, playhead and pause/lock state from here — the
watch-sampler and the lock HUD's own model both do.  The five lines every player
leads with are :class:`player_core.status.PlayerStatus`; a satellite adds how
many clips its playlist holds, which the dispatch loop reads to know when a loop
is down to one.
"""
from __future__ import annotations

from player_core.status import PlayerStatus
from player_core.status import status_fields as player_status_fields


def status_fields(session) -> dict[str, str]:
    return {
        **player_status_fields(PlayerStatus(
            video=str(session.current_video),
            position_ms=int(session.position_ms),
            duration_ms=int(session.duration_ms),
            paused=session.is_paused,
            locked=session.is_locked,
        )),
        "playlist_length": str(session.playlist_length),
        "speed": f"{session.speed:g}",
    }

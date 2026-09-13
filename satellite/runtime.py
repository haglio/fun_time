"""Command channel: command strings -> SatelliteSession actions.

fun_time writes one command per line to the satellite's command file; the run
loop consumes them and calls :func:`apply_command`.  The keyword is
case-insensitive and PLAY_FILE carries a case-sensitive path argument.  Pause is
NOT a command — it rides its own flag file (like the main player), so a paused satellite is a
settled state rather than a verb race.  A satellite is silent and unscripted, so
there is no volume/speed/funscript/record surface — the verb set is a fraction of
The main player's own command set.
"""
from __future__ import annotations

from player_core.playlist import item_from_line


def apply_command(
    command: str,
    session,
    *,
    stop_event=None,
    reload_playlist,
) -> bool:
    """Dispatch one command line to *session*; return whether it was handled."""
    parts = command.strip().split(None, 1)
    if not parts:
        return False
    keyword = parts[0].upper()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if keyword == "NEXT":
        session.step(1)
    elif keyword == "PREV":
        session.step(-1)
    elif keyword == "LOCK":
        session.set_locked(True)
    elif keyword == "UNLOCK":
        session.set_locked(False)
    elif keyword == "TRASH":
        session.discard()
    elif keyword == "PLAY_FILE" and (item := item_from_line(arg)) is not None:
        session.play_file(item.path)
    elif keyword == "RELOAD_PLAYLIST":
        reload_playlist()
    elif keyword == "QUIT":
        if stop_event is None:
            return False
        stop_event.set()
    else:
        return False
    return True

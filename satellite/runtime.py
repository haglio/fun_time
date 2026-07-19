"""Command channel: command strings -> SatelliteSession actions.

fun_time writes one command per line to the satellite's command file; the run
loop consumes them and calls :func:`apply_command`.  The keyword is
case-insensitive and PLAY_FILE carries a case-sensitive path argument.  Pause is
NOT a command — it rides its own flag file (like Nau), so a paused satellite is a
settled state rather than a verb race.  A satellite is silent and unscripted, so
there is no volume/speed/funscript/record surface — the verb set is a fraction of
Nau's own command set.
"""
from __future__ import annotations

from pathlib import Path


def apply_command(
    command: str,
    session,
    *,
    stop_event=None,
    reload_playlist=None,
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
    elif keyword == "PLAY_FILE" and arg:
        session.play_file(Path(arg))
    elif keyword == "RELOAD_PLAYLIST":
        if reload_playlist is None:
            return False
        reload_playlist()
    elif keyword == "QUIT":
        if stop_event is None:
            return False
        stop_event.set()
    else:
        return False
    return True

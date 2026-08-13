"""What a satellite does when its window is told to close.

A satellite is never an application of its own: the sequencer placed it beside
the other players and the dashboard drives it, so ending it alone leaves the
session running around a hole nothing refills.  Its event loop already answers no
key for that reason (``satellite.app``), but the window itself still has the
close every Windows window has — Alt+F4, the taskbar's Close, the system menu —
and SDL hands that to the loop as ``pygame.QUIT``.

Answered by stopping, it took one player out of a live session, one press at a
time.  Answered here, it means what closing the dashboard's own window means:
quit Fun Time.  The ask goes on the channel the dashboard uses, and the session
comes down as a whole behind its closing cover.

Nau and Genau say the same thing in ``genau.session_quit``, for the same reason.
"""
from __future__ import annotations

from pathlib import Path

from player_core.file_channel import append_command

# The verb the dashboard's own Quit button posts, and the one the dispatch loop
# turns into the teardown of every window in the session.
SESSION_QUIT = "quit"


def quit_gesture(dashboard_cmd_file: Path | None) -> bool:
    """Answer a close on this satellite.  True if this player should stop.

    With a dashboard command file there is a session to ask, so the ask goes out
    and this player keeps playing: it stays on screen until the teardown reaches
    it, which is what puts the closing cover up over every window at once rather
    than letting this one blink out ahead of the rest.

    Without one there is nobody to ask — a satellite launched by hand, or by a
    test — and closing the window ends it, as any window's close does.
    """
    if dashboard_cmd_file is None:
        return True
    append_command(dashboard_cmd_file, SESSION_QUIT)
    return False

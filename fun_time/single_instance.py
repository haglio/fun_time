"""Which mutex says a session is running, and what a second launch is told.

The mutex itself -- claiming it, holding the handle that is the claim, asking
whether someone else holds it -- is ``app_support.win32``'s.  What is Fun Time's
is the name, and the notice.
"""
from __future__ import annotations

# The base of the orchestrator's mutex name; ``app_support.win32.mutex_name``
# adds the session's identity, so one config blocks its own duplicates while a
# session on another config runs beside it.  A branch-verification session
# borrows the live session's identity on purpose, and is refused while that one
# is up, in either order.  The name cannot change without letting a second
# session start beside one already running.
MUTEX_ORCHESTRATOR = "Global\\FunTime.Orchestrator"


def show_already_running_message(text: str, title: str = "Fun Time") -> None:
    """Say another instance holds the mutex, in Fun Time's own colors."""
    from shared_ui.alert import Level, show_alert

    from fun_time.project_paths import PROJECT_ICON

    show_alert(title, text, level=Level.INFO, icon=PROJECT_ICON)

"""Which mutex says a session is running, and what a second launch is told.

The mutex itself is ``app_support.win32``'s; what is Fun Time's is the name,
and the notice.  A crossing waits on it: ``docs/entering-vr.md``.
"""
from __future__ import annotations

# ``app_support.win32.mutex_name`` adds the session's identity, so one config
# blocks its own duplicates while a session on another runs beside it -- and a
# branch session borrows the live one's identity, so the two refuse each other.
# The name cannot change without letting a second session start beside one.
MUTEX_ORCHESTRATOR = "Global\\FunTime.Orchestrator"


def show_already_running_message(text: str, title: str = "Fun Time") -> None:
    """Say another instance holds the mutex, in Fun Time's own colors."""
    from shared_ui.alert import Level, show_alert

    from fun_time.project_paths import PROJECT_ICON

    show_alert(title, text, level=Level.INFO, icon=PROJECT_ICON)

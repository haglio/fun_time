"""Give every process Fun Time launches a name of its own in the task list.

A stranded child leaves no window to close, so the task list is the only way
back to it -- and it offered a column of identical "Python" rows.
:class:`~app_support.process_identity.ProcessNamer` makes the role-named copies
each child is started through, and carries the why; this module is only Fun
Time's answers to it.
"""
from __future__ import annotations

from app_support.process_identity import ProcessNamer

from fun_time.project_paths import PROJECT_ICON

# The one namer this repo's own children are launched through.  The broker gets
# its own (``orchestrator_broker.BROKER_IMAGE_PATTERN``) because it is a
# separate application this one starts, not one of this app's processes.
NAMER = ProcessNamer("Fun Time", icon=PROJECT_ICON)


def prepare_orchestrator_launcher() -> None:
    """Make the copy ``launch.vbs`` runs the orchestrator through next time.

    The console interpreter by name -- not the windowed one the namer would pick
    by itself -- because that is the one ``launch.vbs`` runs.  Why it is one
    launch late, why it is derived beside the running interpreter rather than
    from it, and why it can never cost the launch:
    :meth:`ProcessNamer.name_this_process`.
    """
    NAMER.name_this_process("Orchestrator", interpreter="python.exe")

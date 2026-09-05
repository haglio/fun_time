"""Give every process Fun Time launches a name of its own in the task list.

Windows fixes a process's identity at ``CreateProcess`` time, so every child
started through a venv's ``pythonw.exe`` arrives as one more anonymous Python --
indistinguishable from the user's other Python apps.  That is not a cosmetic
problem.  When a session strands a child (an orchestrator that dies without
reaping leaves its companions running, and no window is left to close), the only
way back is the task list, and the task list offered a column of identical
"Python" rows with no way to tell Fun Time's from the ones that would break
something else if killed.

:class:`~app_support.process_identity.ProcessNamer` makes the described,
role-named copies each child is started through, and its module docstring
carries the three fields Windows shows a process by and the two load-bearing
details of which interpreter is copied where.  This module is only Fun Time's
answers to it: the name the rows lead with, the mark stamped beside them, and
the one copy the launcher itself needs made a session in advance.
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
    launch behind, why it is derived beside the running interpreter rather than
    from it, and why it can never cost the launch:
    :meth:`ProcessNamer.name_this_process`.
    """
    NAMER.name_this_process("Orchestrator", interpreter="python.exe")

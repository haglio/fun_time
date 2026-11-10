from __future__ import annotations

from app_support.process_identity import ProcessNamer

from fun_time.project_paths import PROJECT_ICON

NAMER = ProcessNamer("Fun Time", icon=PROJECT_ICON)
THE_INTERPRETER_LAUNCH_VBS_RUNS_NEVER_ITS_NAMED_COPY = "python.exe"


def prepare_orchestrator_launcher() -> None:
    NAMER.name_this_process("Orchestrator", interpreter=THE_INTERPRETER_LAUNCH_VBS_RUNS_NEVER_ITS_NAMED_COPY)

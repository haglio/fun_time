from __future__ import annotations

from app_support.process_identity import ProcessNamer

from fun_time.project_paths import PROJECT_ICON

NAMER = ProcessNamer("Fun Time", icon=PROJECT_ICON)


def prepare_orchestrator_launcher() -> None:
    NAMER.name_this_process("Orchestrator", interpreter="python.exe")

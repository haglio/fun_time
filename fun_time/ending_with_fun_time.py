from __future__ import annotations

import logging
import subprocess

from app_support.subprocess_utils import tie_to_this_process
from app_support.win32 import JobObject

logger = logging.getLogger(__name__)

_TIES: dict[int, JobObject] = {}


def tie_to_fun_time(part: subprocess.Popen) -> int:
    job = tie_to_this_process(part)
    if job is not None:
        _TIES[part.pid] = job
    return part.pid


def free_from_fun_time(pid: int) -> None:
    job = _TIES.pop(pid, None)
    if job is None:
        return
    try:
        job.let_its_processes_outlive_it()
    except OSError as refused:
        logger.warning("pid %s stays tied to Fun Time, so it will end when Fun Time does: %s", pid, refused)

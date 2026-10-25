from __future__ import annotations

import contextlib
import functools
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from app_support.win32 import JobObject, a_job_ending_with_this_process

from fun_time.win32_process import command_line_of
from tests.child_reports import StartedProcess, all_gone_within, running
from tests.integration.hidden_desktop import INTEGRATION_DIR
from tests.thread_state import TIME_TO_END_S

_THE_INTEGRATION_SUITE = Path(__file__).resolve().parents[1] / INTEGRATION_DIR


class ProcessesLeftRunning(AssertionError):
    def __init__(self, processes: Iterable[StartedProcess]) -> None:
        self.processes = sorted(processes)
        super().__init__("still running: " + "; ".join(
            f"{each.pid} {command_line_of(each.pid)}" for each in self.processes))


@functools.cache
def the_job_this_process_holds() -> JobObject:
    return a_job_ending_with_this_process()


def _started_by_this_process() -> set[StartedProcess]:
    return {StartedProcess.of(pid) for pid in the_job_this_process_holds().process_ids() if pid != os.getpid()}


@contextlib.contextmanager
def no_process_outlives_this(*, within_s: float = TIME_TO_END_S) -> Iterator[None]:
    before = _started_by_this_process()
    yield
    started = _started_by_this_process() - before
    if not all_gone_within(list(started), within_s):
        raise ProcessesLeftRunning(each for each in started if running(each))


def the_guard_for(test_file: Path) -> contextlib.AbstractContextManager[None]:
    if test_file.is_relative_to(_THE_INTEGRATION_SUITE):
        return contextlib.nullcontext()
    return no_process_outlives_this()

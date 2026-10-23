from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import functools
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from fun_time.win32_job import a_job_whose_processes_end_with_it
from fun_time.win32_loader import get_last_error, load_dll
from fun_time.win32_process import command_line_of
from tests.child_reports import StartedProcess, all_gone_within, running
from tests.integration.hidden_desktop import INTEGRATION_DIR
from tests.thread_state import TIME_TO_END_S

_THE_INTEGRATION_SUITE = Path(__file__).resolve().parents[1] / INTEGRATION_DIR

_kernel32 = load_dll("kernel32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wt.HANDLE
_kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
_kernel32.AssignProcessToJobObject.restype = wt.BOOL
_kernel32.QueryInformationJobObject.argtypes = [
    wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD)]
_kernel32.QueryInformationJobObject.restype = wt.BOOL

_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3


class _ProcessIdList(ctypes.Structure):
    _fields_ = [("NumberOfAssignedProcesses", wt.DWORD),
                ("NumberOfProcessIdsInList", wt.DWORD),
                ("ProcessIdList", ctypes.c_size_t * 1024)]


class ProcessesLeftRunning(AssertionError):
    def __init__(self, processes: Iterable[StartedProcess]) -> None:
        self.processes = sorted(processes)
        super().__init__("still running: " + "; ".join(
            f"{each.pid} {command_line_of(each.pid)}" for each in self.processes))


@functools.cache
def the_job_this_process_holds() -> int:
    job = a_job_whose_processes_end_with_it()
    if not _kernel32.AssignProcessToJobObject(job, _kernel32.GetCurrentProcess()):
        raise ctypes.WinError(get_last_error())
    return job


def _started_by_this_process() -> set[StartedProcess]:
    listed = _ProcessIdList()
    if not _kernel32.QueryInformationJobObject(
            the_job_this_process_holds(), _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
            ctypes.byref(listed), ctypes.sizeof(listed), None):
        raise ctypes.WinError(get_last_error())
    pids = listed.ProcessIdList[:listed.NumberOfProcessIdsInList]
    return {StartedProcess.of(pid) for pid in pids if pid != os.getpid()}


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

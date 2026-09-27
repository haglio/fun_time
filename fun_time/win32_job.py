from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import subprocess

from fun_time.win32_loader import get_last_error, load_dll

logger = logging.getLogger(__name__)

_kernel32 = load_dll("kernel32", use_last_error=True)

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong)]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wt.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wt.DWORD),
                ("SchedulingClass", wt.DWORD)]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


_kernel32.CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPCWSTR]
_kernel32.CreateJobObjectW.restype = wt.HANDLE
_kernel32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
_kernel32.SetInformationJobObject.restype = wt.BOOL
_kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
_kernel32.AssignProcessToJobObject.restype = wt.BOOL
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CloseHandle.restype = wt.BOOL

_held_until_this_process_ends: list[int] = []


def a_job_whose_processes_end_with_it(*, more_limits: int = 0, priority_class: int = 0) -> int:
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(get_last_error())
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | more_limits
    info.BasicLimitInformation.PriorityClass = priority_class
    if not _kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
    ):
        error = get_last_error()
        _kernel32.CloseHandle(job)
        raise ctypes.WinError(error)
    return job


def tie_to_this_process(child: subprocess.Popen) -> None:
    try:
        job = a_job_whose_processes_end_with_it()
        if not _kernel32.AssignProcessToJobObject(job, int(child._handle)):
            error = get_last_error()
            _kernel32.CloseHandle(job)
            raise ctypes.WinError(error)
    except OSError as refused:
        logger.warning("pid %s is not tied to this process, so it can outlive it: %s",
                       child.pid, refused)
        return
    _held_until_this_process_ends.append(job)

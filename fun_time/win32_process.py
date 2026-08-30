"""Asking Windows about a process, rather than about a window.

Four queries the session makes of the pids it recorded at launch: what a pid is
running, when the process holding it started, whether it is still alive, and
which pids call it parent.  They share a file with nothing — no window, no
handle, no z-order — and they are what the orchestrator's reap and the
integration runner's cleanup are built on.

Every entry point these call is declared below.  ``argtypes`` matter on 64-bit:
without them ctypes marshals a HANDLE as a 32-bit ``c_int`` and truncates it,
and an out-parameter pointer has to be a real pointer.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes

from fun_time.win32_loader import load_dll

_kernel32 = load_dll("kernel32")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_kernel32.OpenProcess.argtypes = [
    ctypes.wintypes.DWORD,  # dwDesiredAccess
    ctypes.wintypes.BOOL,   # bInheritHandle
    ctypes.wintypes.DWORD,  # dwProcessId
]
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.wintypes.HANDLE,                  # hProcess
    ctypes.wintypes.DWORD,                   # dwFlags
    ctypes.wintypes.LPWSTR,                  # lpExeName
    ctypes.POINTER(ctypes.wintypes.DWORD),   # lpdwSize (in/out)
]
_kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
_kernel32.GetExitCodeProcess.argtypes = [
    ctypes.wintypes.HANDLE,                  # hProcess
    ctypes.POINTER(ctypes.wintypes.DWORD),   # lpExitCode
]
_kernel32.GetExitCodeProcess.restype = ctypes.wintypes.BOOL
_kernel32.GetProcessTimes.argtypes = [
    ctypes.wintypes.HANDLE,                     # hProcess
    ctypes.POINTER(ctypes.wintypes.FILETIME),   # lpCreationTime
    ctypes.POINTER(ctypes.wintypes.FILETIME),   # lpExitTime
    ctypes.POINTER(ctypes.wintypes.FILETIME),   # lpKernelTime
    ctypes.POINTER(ctypes.wintypes.FILETIME),   # lpUserTime
]
_kernel32.GetProcessTimes.restype = ctypes.wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

# The Toolhelp trio.  Undeclared, CreateToolhelp32Snapshot answers as a 32-bit
# c_int: its documented failure value comes back -1, which never equals the
# INVALID_HANDLE_VALUE the guard below compares against, so a failed snapshot
# fell through to a Process32FirstW and a CloseHandle on an invalid handle.
_kernel32.CreateToolhelp32Snapshot.argtypes = [
    ctypes.wintypes.DWORD,  # dwFlags
    ctypes.wintypes.DWORD,  # th32ProcessID
]
_kernel32.CreateToolhelp32Snapshot.restype = ctypes.wintypes.HANDLE

# GetExitCodeProcess reports this while the process is still running.
_STILL_ACTIVE = 259


def get_process_image_name(pid: int) -> str | None:
    """Return the full executable path of the process *pid*.

    Returns None when the process no longer exists (or cannot be opened),
    which callers use to detect that a recorded PID is stale.
    """
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        _kernel32.CloseHandle(handle)


def get_process_creation_time(pid: int) -> int | None:
    """Return the FILETIME at which the process now holding *pid* was created.

    Windows hands a freed PID back out within seconds, so a PID alone does not
    name a process.  ``(pid, creation_time)`` does: a process can only take a
    PID after its previous owner is gone, so the newcomer's creation time is
    strictly later.  Record this alongside a PID and compare it before killing,
    and a recycled PID is recognized rather than shot.

    ``GetProcessTimes`` fills lpCreationTime with a FILETIME (100-nanosecond
    ticks since 1601-01-01 UTC) and accepts a handle opened for
    PROCESS_QUERY_LIMITED_INFORMATION.  Returns None when the process no longer
    exists (or cannot be opened).
    """
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = ctypes.wintypes.FILETIME()
        unused = (ctypes.wintypes.FILETIME(), ctypes.wintypes.FILETIME(), ctypes.wintypes.FILETIME())
        if not _kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), *(ctypes.byref(t) for t in unused)
        ):
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        _kernel32.CloseHandle(handle)


def is_process_alive(pid: int) -> bool:
    """Check whether *pid* refers to a currently running process.

    os.kill(pid, 0) raises WinError 87 for valid PIDs on Python 3.14 /
    Windows 11, and OpenProcess alone still succeeds for exited processes
    whose kernel object is kept alive by an open handle, so liveness comes
    from GetExitCodeProcess reporting STILL_ACTIVE.
    """
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def list_child_pids(parent_pid: int) -> list[int]:
    """The pids whose recorded parent is *parent_pid*, via a Toolhelp snapshot.

    A recorded child pid is not always the pid that owns the windows: a venv's
    ``Scripts`` launcher spawns the real interpreter as a child and keeps the
    recorded pid for itself.  This is the one hop that recovers the family.
    """
    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.wintypes.DWORD),
            ("cntUsage", ctypes.wintypes.DWORD),
            ("th32ProcessID", ctypes.wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.wintypes.ULONG)),
            ("th32ModuleID", ctypes.wintypes.DWORD),
            ("cntThreads", ctypes.wintypes.DWORD),
            ("th32ParentProcessID", ctypes.wintypes.DWORD),
            ("pcPriClassBase", ctypes.wintypes.LONG),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    _kernel32.Process32FirstW.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    _kernel32.Process32FirstW.restype = ctypes.wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    _kernel32.Process32NextW.restype = ctypes.wintypes.BOOL

    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    children: list[int] = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if entry.th32ParentProcessID == parent_pid:
                    children.append(int(entry.th32ProcessID))
                if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        _kernel32.CloseHandle(snapshot)
    return children

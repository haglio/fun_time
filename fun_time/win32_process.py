"""Asking Windows about a process, rather than about a window.

What a pid is running and with which command line, when the process holding it
started, whether it is still alive, and which pids call it parent.  They share a
file with nothing — no window, no handle, no z-order — and they are what the
orchestrator's reap and the integration runner's cleanup are built on.

Every entry point these call is declared below.  ``argtypes`` matter on 64-bit:
without them ctypes marshals a HANDLE as a 32-bit ``c_int`` and truncates it,
and an out-parameter pointer has to be a real pointer.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
from typing import NamedTuple

from fun_time.win32_loader import load_dll

_kernel32 = load_dll("kernel32")
_ntdll = load_dll("ntdll")

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

# Undeclared it answered as a c_int, and its failure never equalled the
# INVALID_HANDLE_VALUE the guard compares against.
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
        return creation_time_of(handle)
    finally:
        _kernel32.CloseHandle(handle)


_FILETIME_AT_THE_UNIX_EPOCH = 116_444_736_000_000_000


def unix_seconds(filetime: int) -> float:
    return (filetime - _FILETIME_AT_THE_UNIX_EPOCH) / 10_000_000


def creation_time_of(handle: int) -> int | None:
    creation = ctypes.wintypes.FILETIME()
    unused = (ctypes.wintypes.FILETIME(), ctypes.wintypes.FILETIME(), ctypes.wintypes.FILETIME())
    if not _kernel32.GetProcessTimes(
        handle, ctypes.byref(creation), *(ctypes.byref(t) for t in unused)
    ):
        return None
    return (creation.dwHighDateTime << 32) | creation.dwLowDateTime


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
    """The pids whose recorded parent is *parent_pid*.

    A recorded child pid is not always the pid that owns the windows: a venv's
    ``Scripts`` launcher spawns the real interpreter as a child and keeps the
    recorded pid for itself.  This is the one hop that recovers the family.
    """
    return [pid for pid, parent in process_parents() if parent == parent_pid]


def process_parents() -> list[tuple[int, int]]:
    return [(entry.pid, entry.parent) for entry in process_table()]


class ProcessEntry(NamedTuple):
    pid: int
    parent: int
    image: str


def process_table() -> list[ProcessEntry]:
    """Every running process, from one Toolhelp snapshot."""
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
    entries: list[ProcessEntry] = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                entries.append(ProcessEntry(
                    int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
                if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        _kernel32.CloseHandle(snapshot)
    return entries


_PROCESS_COMMAND_LINE_INFORMATION = 60


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", ctypes.wintypes.USHORT),
                ("MaximumLength", ctypes.wintypes.USHORT),
                ("Buffer", ctypes.c_void_p)]


_ntdll.NtQueryInformationProcess.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.wintypes.ULONG, ctypes.c_void_p, ctypes.wintypes.ULONG,
    ctypes.POINTER(ctypes.wintypes.ULONG)]
_ntdll.NtQueryInformationProcess.restype = ctypes.wintypes.LONG


def command_line_of(pid: int) -> str | None:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        needed = ctypes.wintypes.ULONG(0)
        _ntdll.NtQueryInformationProcess(
            handle, _PROCESS_COMMAND_LINE_INFORMATION, None, 0, ctypes.byref(needed))
        if not needed.value:
            return None
        answer = ctypes.create_string_buffer(needed.value)
        if _ntdll.NtQueryInformationProcess(
            handle, _PROCESS_COMMAND_LINE_INFORMATION, answer, needed, ctypes.byref(needed)
        ):
            return None
        text = _UNICODE_STRING.from_buffer(answer)
        return ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Length else ""
    finally:
        _kernel32.CloseHandle(handle)

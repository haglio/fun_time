"""Run the integration suite on a hidden Win32 desktop — invisibly, focus-safe.

The suite launches real windows (VLC, Nau/mpv, the dashboard, AHK) and asserts on
real window state, so it must run on the native Qt platform — but that throws those
windows onto the monitors and can grab focus.  A Win32 *desktop* other than the
input desktop fixes this: its windows are real HWNDs (winId != 0, real styles, real
Direct3D11 video output) yet render to nothing and can never hold the input desktop's
foreground.

``CreateDesktopW`` makes the desktop; ``CreateProcessW`` with ``STARTUPINFO.lpDesktop``
binds pytest to it, and every subprocess pytest spawns (orchestrator -> VLC / Nau /
AHK / dashboard) inherits it.  The win32 lookup helpers enumerate the caller's own
desktop, so they see the app because pytest shares the hidden desktop.  There is no
silent fall-back: if the desktop can't be opened, ``CreateProcessW`` fails, so a run
can never leak onto the real screen.

pytest is also placed in a *job object* that this runner alone holds a handle to.
Windows destroys a job when its last handle closes, and a job with
``KILL_ON_JOB_CLOSE`` takes its members down with it — so however the run ends, it
cannot leave a VLC or an AHK behind to poison the next one.  The broker is the sole
exception: it is a service that outlives the session, and it breaks away (see
``fun_time.orchestrator_broker.broker_launch_kwargs``).

Usage (default integration command):

    .venv/Scripts/python.exe -m tests.integration.hidden_desktop
    .venv/Scripts/python.exe -m tests.integration.hidden_desktop -k nau   # extra args pass through
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
from pathlib import Path

HIDDEN_DESKTOP_NAME = "FunTimeIntegration"
INTEGRATION_DIR = "tests/integration/"


def build_pytest_argv(extra_args: list[str]) -> list[str]:
    """The pytest command the hidden desktop runs — the whole integration dir, with
    caller *extra_args* appended last so they win."""
    return [
        sys.executable, "-m", "pytest", INTEGRATION_DIR,
        *extra_args,
    ]


# --- Win32 desktop isolation ---------------------------------------------------
# HANDLE/HWND argtypes/restypes are declared so ctypes passes them as 64-bit
# pointers rather than truncating to c_int (same rule as fun_time/win32.py).
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_ALL = 0x10000000
STARTF_USESTDHANDLES = 0x00000100
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12
INFINITE = 0xFFFFFFFF
CREATE_SUSPENDED = 0x00000004

# Destroying the job terminates every process still in it.  The run's whole
# process tree — pytest, the orchestrator, VLC, Nau, Genau, AHK — is in it,
# because a process created by a job member joins that member's job.
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.POINTER(wt.BYTE)), ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


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


_user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPVOID, wt.DWORD, wt.DWORD, wt.LPVOID]
_user32.CreateDesktopW.restype = wt.HANDLE
_user32.CloseDesktop.argtypes = [wt.HANDLE]
_user32.CloseDesktop.restype = wt.BOOL
_kernel32.GetStdHandle.argtypes = [wt.DWORD]
_kernel32.GetStdHandle.restype = wt.HANDLE
_kernel32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.LPVOID, wt.LPVOID, wt.BOOL,
                                     wt.DWORD, wt.LPVOID, wt.LPCWSTR,
                                     ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION)]
_kernel32.CreateProcessW.restype = wt.BOOL
_kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_kernel32.WaitForSingleObject.restype = wt.DWORD
_kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
_kernel32.GetExitCodeProcess.restype = wt.BOOL
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CloseHandle.restype = wt.BOOL
_kernel32.CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPCWSTR]
_kernel32.CreateJobObjectW.restype = wt.HANDLE
_kernel32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
_kernel32.SetInformationJobObject.restype = wt.BOOL
_kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
_kernel32.AssignProcessToJobObject.restype = wt.BOOL
_kernel32.ResumeThread.argtypes = [wt.HANDLE]
_kernel32.ResumeThread.restype = wt.DWORD
_kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
_kernel32.TerminateProcess.restype = wt.BOOL

UOI_NAME = 2
_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_kernel32.GetCurrentThreadId.restype = wt.DWORD
_user32.GetThreadDesktop.argtypes = [wt.DWORD]
_user32.GetThreadDesktop.restype = wt.HANDLE
_user32.GetUserObjectInformationW.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD)]
_user32.GetUserObjectInformationW.restype = wt.BOOL
_user32.EnumDesktopWindows.argtypes = [wt.HANDLE, _WNDENUMPROC, wt.LPARAM]
_user32.EnumDesktopWindows.restype = wt.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user32.GetWindowThreadProcessId.restype = wt.DWORD


def current_desktop_name() -> str:
    """Name of the desktop the calling thread is on — ``'Default'`` on the normal
    interactive desktop, ``HIDDEN_DESKTOP_NAME`` under the hidden-desktop runner."""
    hdesk = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())
    buf = ctypes.create_unicode_buffer(256)
    needed = wt.DWORD()
    if not _user32.GetUserObjectInformationW(hdesk, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
        return ""
    return buf.value


def pids_with_window_on_current_desktop() -> set[int]:
    """PIDs owning a top-level window on the calling thread's desktop.

    The hidden integration desktop is shared: ``CreateDesktopW`` opens the
    existing one whenever a previous run's processes still hold it, so this
    returns the caller's own windows, a leftover session's, and the pytest of
    any run queued behind this one.  What it never returns is the user's real
    (input-desktop) session — which is what makes a scoped kill safe to fire
    unattended.  Callers must still decide which of these PIDs are theirs to
    kill (see ``_kill_leftover_app_processes``)."""
    hdesk = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())
    pids: set[int] = set()

    def _collect(hwnd, _lparam):
        pid = wt.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            pids.add(pid.value)
        return True

    _user32.EnumDesktopWindows(hdesk, _WNDENUMPROC(_collect), 0)
    return pids


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def create_run_job() -> int:
    """A job object that outlives nothing: destroying it kills its members.

    The runner holds the job's only handle, so whichever way the runner ends —
    a clean exit, a crash, a TerminateProcess — Windows closes that handle,
    destroys the job, and terminates every process the run still had running.
    A run therefore cannot strand a VLC (or an AHK, or an orchestrator) for the
    next run to trip over.

    ``BREAKAWAY_OK`` is what lets the broker opt out with
    CREATE_BREAKAWAY_FROM_JOB: it is a service that outlives the session that
    starts it, so it must not be swept up with the run.
    """
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
    )
    if not _kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
    ):
        error = ctypes.get_last_error()
        _kernel32.CloseHandle(job)
        raise ctypes.WinError(error)
    return job


def close_run_job(job: int) -> None:
    """Drop the job's last handle, terminating whatever the run left running."""
    _kernel32.CloseHandle(job)


def _close_process_handles(pi: _PROCESS_INFORMATION) -> None:
    _kernel32.CloseHandle(pi.hProcess)
    _kernel32.CloseHandle(pi.hThread)


def _launch_on_desktop(cmdline: str, desktop: str | None, cwd: str, job: int) -> _PROCESS_INFORMATION:
    """Start *cmdline* on *desktop*, inside *job*, and let it run.

    Created suspended so the process is in the job before it can execute a
    single instruction: a child spawned between CreateProcess and
    AssignProcessToJobObject would never join the job, and would survive the
    run.  ``desktop`` of None inherits the caller's desktop.
    """
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = desktop
    # Inherit our std handles so the child pytest's output streams to whoever ran
    # the runner (a terminal, or the CI/agent that captured this process's stdout).
    si.dwFlags = STARTF_USESTDHANDLES
    si.hStdInput = _kernel32.GetStdHandle(STD_INPUT_HANDLE)
    si.hStdOutput = _kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    si.hStdError = _kernel32.GetStdHandle(STD_ERROR_HANDLE)
    pi = _PROCESS_INFORMATION()
    ok = _kernel32.CreateProcessW(None, ctypes.create_unicode_buffer(cmdline), None, None,
                                  True, CREATE_SUSPENDED, None, cwd,
                                  ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    if not _kernel32.AssignProcessToJobObject(job, pi.hProcess):
        error = ctypes.get_last_error()
        _kernel32.TerminateProcess(pi.hProcess, 1)
        _close_process_handles(pi)
        raise ctypes.WinError(error)
    _kernel32.ResumeThread(pi.hThread)
    return pi


def run_on_hidden_desktop(extra_args: list[str]) -> int:
    """Create the hidden desktop, run the integration pytest bound to it, and
    return pytest's exit code.  The desktop handle is closed on the way out, and
    the run's job object with it — so nothing the run spawned can survive it."""
    os.environ["FUN_TIME_RUN_INTEGRATION"] = "1"
    hdesk = _user32.CreateDesktopW(HIDDEN_DESKTOP_NAME, None, None, 0, GENERIC_ALL, None)
    if not hdesk:
        raise ctypes.WinError(ctypes.get_last_error())
    print(f"[hidden-desktop] running the integration suite on '{HIDDEN_DESKTOP_NAME}' "
          f"(off-screen, focus-safe)…", file=sys.stderr, flush=True)
    try:
        cmdline = subprocess.list2cmdline(build_pytest_argv(extra_args))
        job = create_run_job()
        try:
            pi = _launch_on_desktop(cmdline, HIDDEN_DESKTOP_NAME, str(_repo_root()), job)
            try:
                _kernel32.WaitForSingleObject(pi.hProcess, INFINITE)
                code = wt.DWORD()
                _kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
                return int(code.value)
            finally:
                _close_process_handles(pi)
        finally:
            close_run_job(job)
    finally:
        _user32.CloseDesktop(hdesk)


def main() -> None:
    sys.exit(run_on_hidden_desktop(sys.argv[1:]))


if __name__ == "__main__":
    main()

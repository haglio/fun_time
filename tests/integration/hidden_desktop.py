from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from itertools import pairwise
from pathlib import Path
from time import monotonic

from app_support.integration_runs import CHANGED_FLAG, REPEAT_CHANGED_FLAG, runs_dir
from app_support.siblings import primary_checkout_of
from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.win32 import JobObject
from app_support.win32_loader import load_dll, win_functype

from .coverage_map import CoverageMap, Picked, changed_paths, tests_to_run, the_machine_s_map
from .flake_gate_install import flake_gate_python
from .line_record import RUN_RECORD_ENV, Visit
from .run_clock import WALL, Budget, RunClock, keep_time_by
from .run_pulse import PULSE_ENV, STOPPED_AFTER_S, PulseWatch
from .session_lock import (
    FULL_RUN_GIVES_WAY_S,
    INTEGRATION_LOCK_NAME,
    Place,
    Waiting,
    hold_integration_lock,
)

HIDDEN_DESKTOP_NAME = "FunTimeIntegration"
INTEGRATION_DIR = "tests/integration/"
MEASURED_PROCESS_STARTUP = Path(__file__).with_name("measured_process_startup")

# Distinct from pytest's own codes (1 failed, 2 interrupted) so a caller can tell
# "the suite was invoked wrongly" from "the tests failed".
REFUSED_EXIT_CODE = 4


STOP_AT_THE_FIRST_FAILURE = "-x"
HELP_FLAG = "--help"
_USAGE = """.venv/Scripts/python.exe -m tests.integration.hidden_desktop -x
    the whole suite, on a hidden desktop
... -x -k main_funestra
    extra arguments pass through to pytest
... -x tests/integration/test_x.py
    that file alone
... --changed
    the tests that run what this branch changed
... --repeat-changed
    each changed file whole, then each changed test ten times"""
_MODES = frozenset({REPEAT_CHANGED_FLAG, CHANGED_FLAG})
REPEAT_RUNS = 10
REPEAT_BUDGET_MINUTES = 45


def build_run_argv(extra_args: list[str]) -> list[str]:
    return [
        sys._base_executable, "-m", "pytest",
        *([] if _names_its_tests(extra_args) else [INTEGRATION_DIR]),
        *extra_args,
    ]


def build_gate_argv(base: str, budget_minutes: float) -> list[str]:
    return [sys._base_executable, "-m", "app_support.flake_gate",
            "--base", base, "--only", INTEGRATION_DIR, "--runs", str(REPEAT_RUNS),
            "--budget-minutes", f"{budget_minutes:g}", "--python", sys.executable]


def _without_a_stop_beside_a_mode(extra_args: list[str]) -> list[str]:
    if _MODES.isdisjoint(extra_args):
        return extra_args
    return [arg for arg in extra_args if arg != STOP_AT_THE_FIRST_FAILURE]


def _is_a_repeat(extra_args: list[str]) -> bool:
    return extra_args[:1] == [REPEAT_CHANGED_FLAG]


def _base_of(repeat_args: list[str]) -> str:
    return repeat_args[1] if len(repeat_args) > 1 else "origin/main"


_LEAVES_OUT_THE_TEST_IT_NAMES = frozenset({"--deselect", "--ignore", "--ignore-glob"})


def _names_its_tests(extra_args: list[str]) -> bool:
    return any(_is_in_the_suite(arg) and option not in _LEAVES_OUT_THE_TEST_IT_NAMES
               for option, arg in pairwise(["", *extra_args]))


def _is_in_the_suite(arg: str) -> bool:
    return _repo_root() / INTEGRATION_DIR in (_repo_root() / arg.partition("::")[0]).parents


def _venv_environment(venv_python: str | Path) -> dict[str, str]:
    return {**os.environ, "__PYVENV_LAUNCHER__": str(venv_python)}


# --- Win32 desktop isolation ---------------------------------------------------
# HANDLE/HWND argtypes/restypes are declared so ctypes passes them as 64-bit
# pointers rather than truncating to c_int (same rule as fun_time/win32.py).
_user32 = load_dll("user32", use_last_error=True)
_kernel32 = load_dll("kernel32", use_last_error=True)

GENERIC_ALL = 0x10000000
STARTF_USESTDHANDLES = 0x00000100
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
WAIT_TIMEOUT = 0x00000102
STILL_ACTIVE = 259


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
_kernel32.ResumeThread.argtypes = [wt.HANDLE]
_kernel32.ResumeThread.restype = wt.DWORD
_kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
_kernel32.TerminateProcess.restype = wt.BOOL

UOI_NAME = 2
_WNDENUMPROC = win_functype(wt.BOOL, wt.HWND, wt.LPARAM)
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
    hdesk = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())
    buf = ctypes.create_unicode_buffer(256)
    needed = wt.DWORD()
    if not _user32.GetUserObjectInformationW(hdesk, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
        return ""
    return buf.value


def pids_with_window_on_current_desktop() -> set[int]:
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


def on_hidden_desktop(desktop_name: Callable[[], str] = current_desktop_name) -> bool:
    return desktop_name() == HIDDEN_DESKTOP_NAME


def require_hidden_desktop(desktop_name: Callable[[], str] = current_desktop_name) -> None:
    current = desktop_name()
    if current != HIDDEN_DESKTOP_NAME:
        raise RuntimeError(
            f"The integration suite must run on the '{HIDDEN_DESKTOP_NAME}' desktop, "
            f"not '{current}'.  Run it with:\n\n"
            f"    .venv/Scripts/python.exe -m tests.integration.hidden_desktop\n\n"
            "Running pytest against tests/integration/ directly puts real windows, a "
            "real AHK bridge and real Funestras on your screen, on top of whatever Fun "
            "Time session you have open.\n\n"
            "A unit run that swept this directory in lands here too — that is a run "
            "whose config lacks the norecursedirs exclusion, which means a -c naming "
            "some file other than pyproject.toml.  The unit suite is:\n\n"
            "    .venv/Scripts/python.exe -m pytest\n"
        )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def create_run_job() -> JobObject:
    return JobObject(processes_end_with_it=True, processes_may_break_away=True,
                     priority_class=subprocess.BELOW_NORMAL_PRIORITY_CLASS)


def _close_process_handles(pi: _PROCESS_INFORMATION) -> None:
    _kernel32.CloseHandle(pi.hProcess)
    _kernel32.CloseHandle(pi.hThread)


def _child_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    resolved = dict(os.environ if environment is None else environment)
    resolved["FUN_TIME_MUTE_AUDIO"] = "1"
    if resolved.get("COVERAGE_PROCESS_START"):
        resolved["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(MEASURED_PROCESS_STARTUP), resolved.get("PYTHONPATH"))))
    return resolved


def _environment_block(environment: Mapping[str, str] | None) -> ctypes.Array | None:
    if environment is None:
        return None
    entries = sorted(environment.items(), key=lambda entry: entry[0].upper())
    return ctypes.create_unicode_buffer("".join(f"{name}={value}\0" for name, value in entries))


def _launch_on_desktop(cmdline: str, desktop: str | None, cwd: str, job: JobObject,
                       environment: Mapping[str, str] | None = None) -> _PROCESS_INFORMATION:
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
                                  True, CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
                                  _environment_block(_child_environment(environment)), cwd,
                                  ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        job.hold(pi.hProcess)
    except OSError:
        _kernel32.TerminateProcess(pi.hProcess, 1)
        _close_process_handles(pi)
        raise
    _kernel32.ResumeThread(pi.hThread)
    return pi


def run_where_nothing_is_focused(argv: list[str], timeout_seconds: float = 20.0) -> int:
    desktop = f"{HIDDEN_DESKTOP_NAME}Empty{os.getpid()}"
    hdesk = _user32.CreateDesktopW(desktop, None, None, 0, GENERIC_ALL, None)
    if not hdesk:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with create_run_job() as job:
            pi = _launch_on_desktop(subprocess.list2cmdline(argv), desktop, str(_repo_root()), job)
            try:
                if (code := _exit_code_within(pi.hProcess, timeout_seconds)) is None:
                    raise TimeoutError(
                        f"{argv[0]} was still running {timeout_seconds:g}s after it was "
                        "started on an empty desktop"
                    )
                return code
            finally:
                _close_process_handles(pi)
    finally:
        _user32.CloseDesktop(hdesk)


RUN_CEILING_S = 45 * 60

# What such a run exits with.  pytest's own codes stop at 5, so this cannot be
# read as a test result.
WEDGED_EXIT_CODE = 9

_POLL_MS = 1000
_TEARDOWN_GRACE_MS = 2000


@contextmanager
def _the_runs_own_time(job: JobObject) -> Iterator[None]:
    keep_time_by(RunClock(run_busy=job.busy_seconds))
    try:
        yield
    finally:
        keep_time_by(WALL)


def _wait_for_the_run(process: int, ceiling_s: float, pulse: PulseWatch | None = None) -> int:
    stopped = pulse.has_stopped if pulse else lambda: False
    if (code := _exit_code_within(process, ceiling_s, stopped)) is None:
        why = (f"pytest stopped running any Python during {pulse.last_test() or 'its startup'}, where no "
               "test's own time limit can reach it" if stopped() else
               f"the run went {ceiling_s / 60:g} minutes it could have run without finishing")
        print(f"[hidden-desktop] {why}, so it is being ended here; the job object takes its "
              "children with it and the queue moves again", file=sys.stderr, flush=True)
        return WEDGED_EXIT_CODE
    if _kernel32.WaitForSingleObject(process, _TEARDOWN_GRACE_MS) == WAIT_TIMEOUT:
        print(f"[hidden-desktop] pytest exited {code}, but Windows has not finished taking "
              "its process down and nothing can end it; the run ends here without it",
              file=sys.stderr, flush=True)
    return code


def _exit_code_within(process: int, seconds: float, stopped: Callable[[], bool] = lambda: False) -> int | None:
    budget = Budget(seconds)
    while (code := _exit_code(process)) == STILL_ACTIVE:
        if budget.expired() or stopped():
            return None
        _kernel32.WaitForSingleObject(process, _POLL_MS)
    return code


def _exit_code(process: int) -> int:
    code = wt.DWORD()
    _kernel32.GetExitCodeProcess(process, ctypes.byref(code))
    return int(code.value)


_WHERE_IT_WAITS = {
    Place.AHEAD_OF_FULL_RUNS: "this short run waits for it, ahead of any full run",
    Place.AFTER_THE_FULL_RUN_GOING_NEXT: "this short run waits for it, then for the full run that goes next",
    Place.BEHIND_OTHER_FULL_RUNS:
        "this full run waits for it, and for the full runs that joined the line before it",
    Place.GIVING_WAY_TO_SHORT_RUNS: (f"this full run waits for it, and lets short runs go first until "
                                     f"they have held the line for {FULL_RUN_GIVES_WAY_S / 60:g} minutes"),
    Place.GOING_NEXT: "this full run waits for it, and short runs now wait for this one",
}


def _announce_waiting(waiting: Waiting) -> None:
    print(f"[integration] another integration run holds {INTEGRATION_LOCK_NAME!r}; "
          f"{_WHERE_IT_WAITS[waiting.place]} ({waiting.seconds:.0f}s elapsed)",
          file=sys.stderr, flush=True)


def run_on_hidden_desktop(extra_args: list[str]) -> int:
    if extra_args[:1] == [HELP_FLAG]:
        print(_USAGE)
        return 0
    extra_args = _without_a_stop_beside_a_mode(extra_args)
    if not _MODES.isdisjoint(extra_args[1:]):
        print(f"[hidden-desktop] {' or '.join(sorted(_MODES))} must come first and takes only a base; "
              f"this run was given {extra_args}", file=sys.stderr, flush=True)
        return REFUSED_EXIT_CODE
    os.environ["FUN_TIME_RUN_INTEGRATION"] = "1"
    if _is_a_repeat(extra_args):
        return _repeat_what_changed(_base_of(extra_args))
    if extra_args[:1] == [CHANGED_FLAG]:
        return _run_what_this_change_needs(_base_of(extra_args))
    with _a_place_in_line(short=_is_short(extra_args), what=extra_args) as visit:
        return visit.ended_with(_run_the_suite(build_run_argv(extra_args), sys.executable, RUN_CEILING_S))


def what_this_change_needs(base: str) -> Picked:
    root = _repo_root()
    return tests_to_run(changed_paths(root, base), CoverageMap.load(the_machine_s_map(primary_checkout_of(root))))


def _run_what_this_change_needs(base: str) -> int:
    picked = what_this_change_needs(base)
    print(f"[hidden-desktop] {picked.reason()}", file=sys.stderr, flush=True)
    if not (picked.everything or picked.files):
        return 0
    files = [] if picked.everything else sorted(picked.files)
    with _a_place_in_line(short=bool(files), what=[CHANGED_FLAG, *files]) as visit:
        return visit.ended_with(_run_the_suite(build_run_argv([*files, STOP_AT_THE_FIRST_FAILURE]),
                                               sys.executable, RUN_CEILING_S))


def _is_short(extra_args: list[str]) -> bool:
    return _names_its_tests(extra_args) or any(arg.startswith("-k") for arg in extra_args)


LINE_RECORDS_ENV = "FUN_TIME_LINE_RECORDS"
CLAUDE_SESSION_ENV = "CLAUDE_CODE_SESSION_ID"


def _line_records() -> Path:
    if elsewhere := os.environ.get(LINE_RECORDS_ENV):
        return Path(elsewhere)
    return runs_dir(primary_checkout_of(_repo_root()))


def _branch_of(checkout: Path) -> str:
    done = subprocess.run(["git", "-C", str(checkout), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                          text=True, check=False, **hidden_subprocess_kwargs())
    return done.stdout.strip()


@contextmanager
def _a_place_in_line(*, short: bool, what: list[str]) -> Iterator[Visit]:
    root = _repo_root()
    visit = Visit.join(_line_records(), checkout=root.name, branch=_branch_of(root),
                       session=os.environ.get(CLAUDE_SESSION_ENV, ""), what=what, short=short)
    try:
        with hold_integration_lock(short=short, notify=_announce_waiting):
            visit.got_the_machine()
            os.environ[RUN_RECORD_ENV] = str(visit.path)
            yield visit
    finally:
        os.environ.pop(RUN_RECORD_ENV, None)
        visit.leave()


def _repeat_what_changed(base: str) -> int:
    gate_python = flake_gate_python(_repo_root() / "state")
    files = files_with_a_changed_test(gate_python, base, _repo_root())
    with _a_place_in_line(short=True, what=[REPEAT_CHANGED_FLAG, base]) as visit:
        minutes_left = REPEAT_BUDGET_MINUTES
        if files:
            started = monotonic()
            if code := _run_each_file_whole(files):
                return visit.ended_with(code)
            minutes_left = max(0.0, minutes_left - (monotonic() - started) / 60)
        return visit.ended_with(_run_the_suite(build_gate_argv(base, minutes_left), gate_python,
                                               minutes_left * 60 + RUN_CEILING_S))


def _run_each_file_whole(files: list[str]) -> int:
    print(f"[hidden-desktop] before repeating the new and changed tests, running each "
          f"file that holds one whole, once: {', '.join(files)}", file=sys.stderr, flush=True)
    code = _run_the_suite(build_run_argv([*files, STOP_AT_THE_FIRST_FAILURE]), sys.executable,
                          RUN_CEILING_S)
    if code:
        print("[hidden-desktop] a test failed with its whole file running, so the "
              "repeats were not started", file=sys.stderr, flush=True)
    return code


_LIST_THE_CHANGED_TESTS = ("import sys; from pathlib import Path; "
                           "from app_support.changed_tests import changed_test_ids; "
                           "print('\\n'.join(changed_test_ids(Path.cwd(), sys.argv[1])))")


def files_with_a_changed_test(gate_python: str | Path, base: str, root: Path) -> list[str]:
    listed = subprocess.run([str(gate_python), "-c", _LIST_THE_CHANGED_TESTS, base], cwd=root,
                            stdout=subprocess.PIPE, encoding="utf-8", check=True,
                            **hidden_subprocess_kwargs())
    return sorted({test.partition("::")[0] for test in listed.stdout.splitlines()
                   if test.startswith(INTEGRATION_DIR)})


def _run_the_suite(argv: list[str], venv_python: str | Path, ceiling_s: float,
                   stopped_after_s: float = STOPPED_AFTER_S) -> int:
    hdesk = _user32.CreateDesktopW(HIDDEN_DESKTOP_NAME, None, None, 0, GENERIC_ALL, None)
    if not hdesk:
        raise ctypes.WinError(ctypes.get_last_error())
    print(f"[hidden-desktop] running the integration suite on '{HIDDEN_DESKTOP_NAME}' "
          f"(off-screen, focus-safe)…", file=sys.stderr, flush=True)
    pulse = Path(tempfile.gettempdir()) / f"fun_time_integration_pulse_{os.getpid()}"
    try:
        cmdline = subprocess.list2cmdline(argv)
        with create_run_job() as job:
            pi = _launch_on_desktop(cmdline, HIDDEN_DESKTOP_NAME, str(_repo_root()), job,
                                    environment={**_venv_environment(venv_python), PULSE_ENV: str(pulse)})
            try:
                with _the_runs_own_time(job):
                    return _wait_for_the_run(pi.hProcess, ceiling_s, PulseWatch(pulse, stopped_after_s))
            finally:
                _close_process_handles(pi)
    finally:
        pulse.unlink(missing_ok=True)
        _user32.CloseDesktop(hdesk)


def main() -> None:
    sys.exit(run_on_hidden_desktop(sys.argv[1:]))


if __name__ == "__main__":
    main()

"""Never let an integration run reach the user's live Fun Time session.

``build_integration_config`` copies the real config and rewrites only its
*paths*, so a run keeps the production VLC HTTP ports (8091/8092).  When the
user's session is up, its two satellite VLCs already own those ports: the
suite's own VLCs cannot bind them, and every ``pl_play`` / ``pl_pause`` the
suite issues lands on the user's VLCs instead.  That is how a background test
run makes the user's satellites start playing in the middle of OmniPause.  The
hidden desktop does not help — a port is machine-wide, not per-desktop.

So a run asks before it creates the hidden desktop:

  * No live session          — run.
  * Live session, playing    — deny outright, without prompting.  The user is
                               watching; a test run must never interrupt.
  * Live session, OmniPaused — the user has stepped away from it, so ask: close
                               Fun Time and run, or deny the run.

Closing goes through the bridge's own quit path (``exit`` in ``ahk_cmd.txt``):
AHK exits, the orchestrator wakes and shuts its children down.  Only a child
that outlives that gets taskkilled, and only while its recorded creation time
still names the process the orchestrator launched.
"""
from __future__ import annotations

import configparser
import ctypes
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fun_time.win32 import get_process_creation_time
from fun_time.windows_bridge_dispatch_loop import read_shared_state
from fun_time.windows_bridge_orchestrator import ChildProcess, kill_recorded_child

# Distinct from pytest's own codes (1 failed, 2 interrupted, 3 internal error is
# pytest's, but the suite never reaches pytest when we deny) so a caller can tell
# "the run was refused" from "the run failed".
DENIED_EXIT_CODE = 4

_PROMPT_TITLE = "Fun Time is open"
_PROMPT_TEXT = (
    "The Fun Time integration test suite wants to run, but Fun Time is open "
    "(currently in OmniPause).\n\n"
    "The suite drives VLC over the same HTTP ports as your session, so it would "
    "take over your satellite VLCs.\n\n"
    "Close Fun Time and run the suite?\n\n"
    "Yes — close Fun Time, then run the tests.\n"
    "No — leave Fun Time alone and deny the test run."
)
# MessageBoxW flags: yes/no, warning icon, and pulled to the foreground of the
# input desktop even though the caller is a background agent shell.
_MB_YESNO = 0x00000004
_MB_ICONWARNING = 0x00000030
_MB_SETFOREGROUND = 0x00010000
_MB_TOPMOST = 0x00040000
_IDYES = 6


@dataclass(frozen=True)
class LiveSession:
    """A Fun Time session that is up right now, and whether it is paused."""

    children: dict[str, ChildProcess]
    omni_paused: bool


def read_recorded_children(state_dir: Path) -> dict[str, ChildProcess]:
    """The children the orchestrator recorded at startup, by role.

    Empty when no session has ever written ``bridge_pids.ini`` — or when startup
    failed before it got that far.
    """
    pids_file = state_dir / "bridge_pids.ini"
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(pids_file), encoding="utf-8")
    if "pids" not in parser or "created_at" not in parser:
        return {}
    created_at = parser["created_at"]
    return {
        role: ChildProcess(pid=int(pid), created_at=int(created_at[role]))
        for role, pid in parser["pids"].items()
    }


def _is_alive(child: ChildProcess) -> bool:
    """Whether *child*'s PID still names the process the orchestrator launched.

    A PID alone is not an identity — Windows hands freed PIDs straight back out
    — so the recorded creation time must still match.  A child that was never
    launched (pid 0) can never match a live process.
    """
    if not child.pid:
        return False
    return get_process_creation_time(child.pid) == child.created_at


def find_live_session(state_dir: Path) -> LiveSession | None:
    """The user's running Fun Time, or None if no recorded child is still alive.

    ``bridge_pids.ini`` and ``shared_bridge_state.ini`` both outlive the session
    that wrote them, so liveness comes from the processes, never the files.
    """
    live = {role: child for role, child in read_recorded_children(state_dir).items() if _is_alive(child)}
    if not live:
        return None
    state = read_shared_state(state_dir / "shared_bridge_state.ini")
    return LiveSession(children=live, omni_paused=bool(state and state.omni_paused))


def close_live_session(
    state_dir: Path,
    session: LiveSession,
    *,
    timeout: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Quit Fun Time the way its own hotkey does, then make sure it is gone.

    Writing ``exit`` to the AHK command file ends AHK, which wakes the
    orchestrator into its graceful ``_shutdown_children``.  Anything still alive
    when *timeout* elapses is taskkilled by recorded identity.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "ahk_cmd.txt").write_text("exit", encoding="utf-8")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_is_alive(child) for child in session.children.values()):
            return
        sleep(0.25)
    for child in session.children.values():
        if _is_alive(child):
            kill_recorded_child(child)


def ask_close_fun_time(_session: LiveSession) -> bool:
    """Prompt the user on the real desktop; True means "close it and run"."""
    answer = ctypes.windll.user32.MessageBoxW(
        0, _PROMPT_TEXT, _PROMPT_TITLE,
        _MB_YESNO | _MB_ICONWARNING | _MB_SETFOREGROUND | _MB_TOPMOST,
    )
    return answer == _IDYES


def announce(message: str) -> None:
    """Write to the real stderr so an agent sees it even under pytest capture."""
    stream = sys.__stderr__ or sys.stderr
    if stream is not None:
        stream.write(f"{message}\n")
        stream.flush()


def allow_integration_run(
    state_dir: Path,
    *,
    ask: Callable[[LiveSession], bool] = ask_close_fun_time,
    close: Callable[[Path, LiveSession], None] = close_live_session,
    announce: Callable[[str], None] = announce,
) -> bool:
    """Whether the integration suite may run right now — closing Fun Time if the
    user says so.  See the module docstring for the three cases."""
    session = find_live_session(state_dir)
    if session is None:
        return True
    if not session.omni_paused:
        announce(
            "[integration] Fun Time is open and playing — integration run DENIED. "
            "The suite would take over your satellite VLCs.  Close Fun Time (or "
            "put it in OmniPause) and re-run."
        )
        return False
    if not ask(session):
        announce("[integration] Fun Time left open; integration run declined by the user.")
        return False
    announce("[integration] closing Fun Time so the integration suite can run…")
    close(state_dir, session)
    return True

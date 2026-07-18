"""Never let an integration run reach the user's live Fun Time session.

``build_integration_config`` copies the real config and rewrites its *paths*, and
the shared UDP port the audio companion binds, so a run is isolated by its state
dir and little more.  What a session does beyond that is machine-wide regardless:
its players compete for the same GPU as the user's, and both sessions stream
T-Code to the one OSR2 broker, and so to the one device.  The hidden desktop does
not help with any of that — none of it is per-desktop.

(It used to be far worse, and neither fix was this guard's.  The startup reap
matched every ``-m satellite`` on the machine, so a run coming up killed both
players in the user's live session; it is now bounded to the state files the
reaping session itself claims.  And startup restarted the broker whenever the
heartbeat under its own state dir looked stale — which outside the primary
checkout it always does, because that is not where the broker writes one — so a
run swept the user's broker and its tray watchdog away; startup now launches the
tray over a stale reading instead of killing on it, which the broker's
single-instance mutexes absorb.)

A session is found through the claim it publishes, not by looking in the state
dir of whoever is asking.  That distinction is the whole guard: agents work in
worktrees, a worktree's config resolves ``state_dir`` to that worktree, and a
guard reading its own state dir therefore read an empty directory and declared
the machine idle *every time* — the user's session was running out of the primary
checkout, where the guard never looked.

So a run asks before it creates the hidden desktop:

  * No live session          — run.
  * Live session, starting up — deny.  It has claimed the machine but not yet
                               recorded its children, so there is nothing to
                               close cleanly.
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
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fun_time.live_session import live_session_state_dir
from fun_time.win32 import get_process_creation_time
from fun_time.windows_bridge_dispatch_loop import read_shared_state
from fun_time.windows_bridge_orchestrator import ChildProcess, kill_recorded_child

# Distinct from pytest's own codes (1 failed, 2 interrupted, 3 internal error is
# pytest's, but the suite never reaches pytest when we deny) so a caller can tell
# "the run was refused" from "the run failed".
DENIED_EXIT_CODE = 4
# Distinct from DENIED: the run had already started and was cut short because Fun
# Time opened underneath it.  pytest's own code for that is a bare "interrupted",
# which says nothing about why, and a caller must be able to tell "the user's
# session took priority" from "the tests failed".
ABORTED_EXIT_CODE = 5

_PROMPT_TITLE = "Fun Time is open"
_PROMPT_TEXT = (
    "The Fun Time integration test suite wants to run, but Fun Time is open "
    "(currently in OmniPause).\n\n"
    "The suite brings up a whole second session: its players compete with yours "
    "for the GPU, and both stream to the OSR2 you share with it.\n\n"
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
    """A Fun Time session that is up right now, and whether it is paused.

    ``children`` is empty for a session still starting up — the orchestrator has
    claimed the machine but has not recorded what it launched yet.
    """

    state_dir: Path
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


def find_live_session() -> LiveSession | None:
    """The user's running Fun Time, wherever on this machine it was started from.

    The session itself says where it is: its orchestrator publishes a claim to one
    fixed machine-global path, carrying its own process identity and its state
    dir.  Asking that, rather than looking in *our* state dir, is what makes this
    work from an agent worktree — whose config resolves ``state_dir`` to the
    worktree, so a run used to see an empty directory and conclude the machine was
    idle while the user was mid-session out of the primary checkout.

    Liveness comes from the claim's process, never from the file existing.  The
    state dir it names is then read for what the session launched and whether it
    is OmniPaused; both those files outlive their session too, so the children are
    liveness-checked in turn.
    """
    state_dir = live_session_state_dir()
    if state_dir is None:
        return None
    live = {
        role: child
        for role, child in read_recorded_children(state_dir).items()
        if _is_alive(child)
    }
    state = read_shared_state(state_dir / "shared_bridge_state.ini")
    return LiveSession(
        state_dir=state_dir,
        children=live,
        omni_paused=bool(state and state.omni_paused),
    )


def close_live_session(
    session: LiveSession,
    *,
    timeout: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Quit Fun Time the way its own hotkey does, then make sure it is gone.

    Writing ``exit`` to the AHK command file ends AHK, which wakes the
    orchestrator into its graceful ``_shutdown_children``.  Anything still alive
    when *timeout* elapses is taskkilled by recorded identity.

    The command goes to the session's *own* state dir, which it published — not
    to ours.  Writing ours would drop the file into an empty worktree directory
    that no AHK is watching, and the session would stay open.
    """
    session.state_dir.mkdir(parents=True, exist_ok=True)
    (session.state_dir / "ahk_cmd.txt").write_text("exit", encoding="utf-8")
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
    """Write to the real stderr, for a caller running outside pytest.

    ``sys.__stderr__`` survives pytest replacing ``sys.stderr``, but not pytest's
    default capture, which redirects file descriptor 2 itself — so a message sent
    from *inside* a run lands in the capture buffer and is dropped on an
    interrupt.  Callers here (the pre-run guard, the runner's supervisor) are
    outside that, which is why the abort notice moved out to join them.
    """
    stream = sys.__stderr__ or sys.stderr
    if stream is not None:
        stream.write(f"{message}\n")
        stream.flush()


def watch_for_live_session(
    *,
    abort: Callable[[], None],
    stop: threading.Event,
    find: Callable[[], LiveSession | None] = find_live_session,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 1.0,
) -> None:
    """Keep asking whether Fun Time has opened, and abort the run the moment it has.

    ``allow_integration_run`` answers once, before the hidden desktop exists, so
    it can only ever see a session that was already up.  A run lasts minutes —
    ample time for the user to open Fun Time — and a run that carries on through
    that is a second session on the machine, which is the thing none of this is
    allowed to become.

    Aborts silently: this runs inside pytest, whose capture is at the
    file-descriptor level, so anything written here goes into the capture buffer
    and is dropped on the way out.  The runner's ``supervise_run`` is watching
    from outside that capture and is what tells the user why the run stopped.

    Returns as soon as it has aborted, so *abort* fires once however long the
    session stays open; otherwise polls until *stop* is set.
    """
    while not stop.is_set():
        if find() is not None:
            abort()
            return
        sleep(poll_seconds)


def allow_integration_run(
    *,
    ask: Callable[[LiveSession], bool] = ask_close_fun_time,
    close: Callable[[LiveSession], None] = close_live_session,
    announce: Callable[[str], None] = announce,
) -> bool:
    """Whether the integration suite may run right now — closing Fun Time if the
    user says so.  See the module docstring for the cases."""
    session = find_live_session()
    if session is None:
        return True
    if not session.children:
        announce(
            "[integration] Fun Time is starting up — integration run DENIED.  Its "
            "orchestrator has claimed the machine but has not recorded what it "
            "launched yet, so there is nothing here that could close it cleanly."
        )
        return False
    if not session.omni_paused:
        announce(
            "[integration] Fun Time is open and playing — integration run DENIED. "
            "The suite brings up a second session that would compete for the GPU "
            "and stream to the OSR2 alongside yours.  Close Fun Time (or put it "
            "in OmniPause) and re-run."
        )
        return False
    if not ask(session):
        announce("[integration] Fun Time left open; integration run declined by the user.")
        return False
    announce("[integration] closing Fun Time so the integration suite can run…")
    close(session)
    return True

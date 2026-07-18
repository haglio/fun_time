"""Where a running Fun Time says it is, for anyone on the machine to find.

Every other cross-process file this app writes lives under the session's own
``state_dir``, which is the right home for anything one session tells itself.
It is the wrong home for the one question another checkout has to be able to
answer — *is a Fun Time running right now, and whose state dir is it using?* —
because ``state_dir`` is resolved relative to the checkout that asks.  An agent's
worktree resolves it to ``<worktree>/state``, looks there, finds nothing, and
concludes the machine is idle while the user is mid-session out of the primary
checkout.

So the claim goes to one fixed machine-global path instead, outside every
checkout.  It carries the session's ``state_dir`` (so a finder can go on to read
that session's recorded children and OmniPause flag) and the orchestrator's own
``(pid, created_at)`` — the same identity pair ``bridge_pids.ini`` uses, because
a PID alone is not an identity on Windows.  That pair is what makes a stale file
harmless: a claim whose process is gone, or whose PID has been recycled onto
some unrelated process, reads as no claim at all, so a crash cannot leave a
phantom session standing.
"""
from __future__ import annotations

import configparser
import os
from pathlib import Path

from .win32 import get_process_creation_time


# Relocates the claim, so a test suite that runs the orchestrator does not
# announce its own pytest as the machine's live Fun Time.
CLAIM_PATH_ENV_VAR = "FUN_TIME_LIVE_SESSION_FILE"


def default_claim_path() -> Path:
    """The fixed machine-global path the claim lives at.

    ``%LOCALAPPDATA%`` is per-user, never synced, and — the point here — outside
    every checkout, so the primary tree and an agent's worktree resolve it to the
    same file.
    """
    override = os.environ.get(CLAIM_PATH_ENV_VAR)
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "FunTime" / "live_session.ini"


def publish_live_session(state_dir: Path, *, claim_file: Path | None = None) -> None:
    """Record that this process is running a Fun Time session out of *state_dir*."""
    path = claim_file or default_claim_path()
    pid = os.getpid()
    parser = configparser.ConfigParser()
    parser["live_session"] = {
        "pid": str(pid),
        "created_at": str(get_process_creation_time(pid) or 0),
        "state_dir": str(state_dir),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        parser.write(fh)


def live_session_state_dir(*, claim_file: Path | None = None) -> Path | None:
    """The state dir of the Fun Time running on this machine, or None if none is.

    The claimed ``(pid, created_at)`` pair is what decides whether there *is* a
    session, and then has nothing left to say — where the session keeps its files
    is the only thing a caller can act on.  So it is spent here rather than
    handed back.
    """
    path = claim_file or default_claim_path()
    parser = configparser.ConfigParser()
    parser.read(str(path), encoding="utf-8")
    if "live_session" not in parser:
        return None
    section = parser["live_session"]
    try:
        pid = int(section["pid"])
        created_at = int(section["created_at"])
        state_dir = Path(section["state_dir"])
    except (KeyError, ValueError):
        return None
    if not pid or get_process_creation_time(pid) != created_at:
        return None
    return state_dir

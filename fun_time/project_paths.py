"""Where this checkout is, and the files that sit at its root.

A leaf on purpose: nothing here imports anything outside the standard library,
so a module that wants a path does not take the config reader and the loopback
server with it to get one -- and the package can put a worktree's sibling
checkouts on ``sys.path`` before any of them is imported.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ICON = PROJECT_DIR / "icon.ico"
PROJECT_VR_ICON = PROJECT_DIR / "vr_icon.ico"

STATE_DIRNAME = "state"

# One absolute path per line; present, the file REPLACES the machine's value, so
# an empty one says "the plain venv install, whatever the machine is pinned to" —
# which is what a branch with nothing to do with genau wants.  Absent, the
# machine's value rides through.
GENAU_DIRS_OVERRIDE_NAME = "genau_project_dirs.txt"


def override_lines(path: Path) -> list[str] | None:
    """*path*'s entries, or None where the file does not exist.  None and [] are
    different answers: absent means the machine's config still decides, empty
    means this checkout has overruled it."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def apply_genau_dirs_to_sys_path() -> list[str]:
    """Put this checkout's genau_project_dirs override on ``sys.path``.

    The override reaches Genau and the Main Funestra as subprocess PYTHONPATH, but the
    orchestrator's own process — and the device arbiter inside it — resolves its
    siblings through the venv, which is the primary checkout's.  A branch that
    leans on an unlanded sibling change therefore imports names the primary does
    not have yet, and the session dies at launch.  Called as the package loads,
    before any sibling is imported.  Returns what it added.
    """
    dirs = [entry for entry in (
        override_lines(PROJECT_DIR / STATE_DIRNAME / GENAU_DIRS_OVERRIDE_NAME) or [])
        if Path(entry).is_dir()]
    if dirs:
        for entry in reversed([str(PROJECT_DIR), *dirs]):
            if entry in sys.path:
                sys.path.remove(entry)
            sys.path.insert(0, entry)
    return dirs

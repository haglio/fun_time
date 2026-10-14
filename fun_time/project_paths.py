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
HOTKEY_SCRIPT_NAME = "windows_bridge_hotkeys.ahk"

STATE_DIRNAME = "state"

# One absolute path per line; present, the file REPLACES the machine's value, so
# an empty one says "the venv's installs, whatever the machine is pinned to".
# Absent, the machine's value rides through.
SIBLING_CHECKOUTS_OVERRIDE_NAME = "sibling_checkouts.txt"
SIBLING_CHECKOUTS_OVERRIDE_NAME_BEFORE_THE_RENAME = "genau_project_dirs.txt"


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


def sibling_checkouts_named_in(state_dir: Path) -> list[str] | None:
    for name in (SIBLING_CHECKOUTS_OVERRIDE_NAME, SIBLING_CHECKOUTS_OVERRIDE_NAME_BEFORE_THE_RENAME):
        named = override_lines(state_dir / name)
        if named is not None:
            return named
    return None


def apply_sibling_checkouts_to_sys_path() -> list[str]:
    """Put this checkout's sibling checkouts on ``sys.path``.

    Every program the session starts is handed them on its PYTHONPATH, but the
    orchestrator's own process -- and the device arbiter inside it -- would
    import the venv's installs, and a branch that leans on an unlanded sibling
    change would die at launch.  Called as the package loads, before any
    sibling is imported.  Returns what it added.
    """
    dirs = [entry for entry in (sibling_checkouts_named_in(PROJECT_DIR / STATE_DIRNAME) or [])
            if Path(entry).is_dir()]
    if dirs:
        for entry in reversed([str(PROJECT_DIR), *dirs]):
            if entry in sys.path:
                sys.path.remove(entry)
            sys.path.insert(0, entry)
    return dirs

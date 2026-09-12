"""Which sibling checkouts THIS session runs, said by the checkout itself.

``paths.genau_project_dirs`` and ``paths.origenerator_dir`` answer a per-SESSION
question that could only be said in the machine's one ``fun_time_config.json``,
which every session reads: a pin written there for one agent's branch reached
every other session, and nothing took it back out.  A file in the checkout's own
state dir answers it per session instead.

Here rather than in :mod:`fun_time.branch_session` because the orchestrator
applies these at launch, and importing them used to bring the whole branch
machinery -- git worktree parsing, PowerShell, a CLI -- with them.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from . import config as config_module

STATE_DIRNAME = "state"

# One absolute path per line; present, the file REPLACES the machine's value, so
# an empty one says "the plain venv install, whatever the machine is pinned to" —
# which is what a branch with nothing to do with genau wants.  Absent, the
# machine's value rides through.
GENAU_DIRS_OVERRIDE_NAME = "genau_project_dirs.txt"

# The same, for the Origenerator checkout a session hosts: one absolute path, or
# empty to host none at all.
ORIGENERATOR_DIR_OVERRIDE_NAME = "origenerator_dir.txt"


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


def _own_override(name: str) -> list[str] | None:
    """This checkout's *name* override, resolved against its own project dir."""
    return override_lines(config_module.PROJECT_DIR / STATE_DIRNAME / name)


def apply_genau_dirs_to_sys_path() -> list[str]:
    """Put this checkout's genau_project_dirs override on ``sys.path``.

    The override reaches Genau and Nau as subprocess PYTHONPATH, but the
    orchestrator's own process — and the device arbiter inside it — resolves
    ``player_core`` through the venv, which is the primary checkout's.  A branch
    that leans on an unlanded player_core change therefore imports names the
    primary does not have yet, and the session dies at launch.  Called at the top
    of every launch entry point, ahead of the bridge imports.  Returns what it
    added.
    """
    dirs = [entry for entry in (_own_override(GENAU_DIRS_OVERRIDE_NAME) or [])
            if Path(entry).is_dir()]
    for entry in reversed(dirs):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return dirs


def apply_origenerator_dir_override(config, *, integration: bool = False):
    """This checkout's origenerator override, applied to a loaded config.

    The branch-config generator runs the PRIMARY checkout's copy of this module
    (see :func:`fun_time.branch_session.build_branch_config`), so a branch that
    INTRODUCES the override cannot rely on the generator applying it — the
    orchestrator calls this at launch instead, against its own checkout.
    """
    lines = None if integration else _own_override(ORIGENERATOR_DIR_OVERRIDE_NAME)
    if lines is None:
        return config
    new_dir = Path(lines[0]) if lines else None
    return replace(config, paths=replace(config.paths, origenerator_dir=new_dir))

"""Which sibling checkouts THIS session runs, said by the checkout itself.

``paths.sibling_checkouts`` and ``paths.origenerator_dir`` answer a per-SESSION
question that could only be said in the machine's one ``fun_time_config.json``,
which every session reads: a pin written there for one agent's branch reached
every other session, and nothing took it back out.  A file in the checkout's own
state dir answers it per session instead.

Here rather than in :mod:`fun_time.branch_session` because the orchestrator
applies these at launch, and importing that module brings the whole branch
machinery -- git worktree parsing, PowerShell, a CLI -- with it.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from .project_paths import PROJECT_DIR, STATE_DIRNAME, override_lines

# The same, for the Origenerator checkout a session hosts: one absolute path, or
# empty to host none at all.
ORIGENERATOR_DIR_OVERRIDE_NAME = "origenerator_dir.txt"


def is_a_worktree(checkout: Path) -> bool:
    return checkout.parent.name == "worktrees" and checkout.parent.parent.name == ".claude"


def primary_of(checkout: Path) -> Path:
    return checkout.parents[2] if is_a_worktree(checkout) else checkout


def _own_override(name: str) -> list[str] | None:
    """This checkout's *name* override, resolved against its own project dir."""
    return override_lines(PROJECT_DIR / STATE_DIRNAME / name)


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


def sibling_checkouts_kwargs(sibling_checkouts: str | Path | None) -> dict:
    """The ``Popen`` environment that starts a program on these checkouts rather
    than on what this repo's venv installs: ``PYTHONPATH``, which Python puts
    ahead of site-packages.  A directory that is not there is dropped rather than
    fatal, because a worktree named in the config outlives the worktree and a
    session must still start.
    """
    paths = [str(Path(part)) for part in str(sibling_checkouts or "").split(os.pathsep)
             if part and Path(part).is_dir()]
    if not paths:
        return {}
    inherited = os.environ.get("PYTHONPATH")
    if inherited:
        paths.append(inherited)
    return {"env": {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}}

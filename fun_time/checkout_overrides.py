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

from .project_paths import PROJECT_DIR, STATE_DIRNAME, override_lines, sibling_checkouts_named_in

ORIGENERATOR_DIR_OVERRIDE_NAME = "origenerator_dir.txt"


def is_a_worktree(checkout: Path) -> bool:
    return checkout.parent.name == "worktrees" and checkout.parent.parent.name == ".claude"


def _own_override(name: str) -> list[str] | None:
    return override_lines(PROJECT_DIR / STATE_DIRNAME / name)


def apply_this_checkouts_overrides(config, *, integration: bool = False):
    if integration:
        return config
    paths = config.paths
    origenerator = _own_override(ORIGENERATOR_DIR_OVERRIDE_NAME)
    if origenerator is not None:
        paths = replace(paths, origenerator_dir=Path(origenerator[0]) if origenerator else None)
    siblings = sibling_checkouts_named_in(PROJECT_DIR / STATE_DIRNAME)
    if siblings is not None:
        paths = replace(paths, sibling_checkouts=tuple(Path(entry) for entry in siblings))
    return replace(config, paths=paths)


def sibling_checkouts_kwargs(sibling_checkouts: str | Path | None) -> dict:
    paths = [str(Path(part)) for part in str(sibling_checkouts or "").split(os.pathsep)
             if part and Path(part).is_dir()]
    if not paths:
        return {}
    inherited = os.environ.get("PYTHONPATH")
    if inherited:
        paths.append(inherited)
    return {"env": {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}}

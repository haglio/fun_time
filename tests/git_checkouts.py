"""A real checkout and a worktree made from it, for tests of what a worktree's primary checkout is."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def a_worktree_of(primary: Path, place: Path) -> Path:
    primary.mkdir(parents=True, exist_ok=True)
    if not (primary / ".git").exists():
        _git(primary, "init", "-q", "-b", "main")
        _git(primary, "-c", "user.email=someone@example.com", "-c", "user.name=Someone",
             "commit", "-q", "--allow-empty", "-m", "the base")
    _git(primary, "worktree", "add", "-q", "--detach", str(place))
    return place

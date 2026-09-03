"""The one step that arms the sanitize guard for a clone.

``tools/githooks/install.py`` writes ``core.hooksPath`` into the checkout's
git config; without it no hook ever runs, so a clone that skips it publishes
unscanned.  Driven here against throwaway git repos — never this checkout —
through the same command line a person runs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALLER = _REPO_ROOT / "tools" / "githooks" / "install.py"


def _fake_repo(tmp_path: Path, *, with_hook: bool = True) -> Path:
    repo = tmp_path / "clone"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if with_hook:
        hook = repo / "tools" / "githooks" / "pre-commit"
        hook.parent.mkdir(parents=True)
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return repo


def _run_installer(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_INSTALLER), *args],
        cwd=repo, capture_output=True, text=True,
    )


def _hooks_path(repo: Path) -> str:
    got = subprocess.run(
        ["git", "config", "--local", "core.hooksPath"],
        cwd=repo, capture_output=True, text=True,
    )
    return got.stdout.strip()


def test_installing_points_git_at_the_tracked_hooks(tmp_path: Path):
    repo = _fake_repo(tmp_path)

    result = _run_installer(repo)

    assert result.returncode == 0, result.stderr
    # Relative on purpose: worktrees share this config, and a relative path
    # resolves against each working tree instead of pinning them all here.
    assert _hooks_path(repo) == "tools/githooks"


def test_a_clone_without_the_hooks_is_refused_not_half_armed(tmp_path: Path):
    repo = _fake_repo(tmp_path, with_hook=False)

    result = _run_installer(repo)

    assert result.returncode == 1
    assert _hooks_path(repo) == ""


def test_uninstall_puts_git_back_on_its_own_hooks(tmp_path: Path):
    repo = _fake_repo(tmp_path)
    _run_installer(repo)

    result = _run_installer(repo, "--uninstall")

    assert result.returncode == 0, result.stderr
    assert _hooks_path(repo) == ""

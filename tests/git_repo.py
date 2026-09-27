"""Git commands for a repository a test makes, committed under an invented author."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def git(repo: Path, *args: str, when: str | None = None) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Example Agent",
        "GIT_AUTHOR_EMAIL": "agent@example.com",
        "GIT_COMMITTER_NAME": "Example Agent",
        "GIT_COMMITTER_EMAIL": "agent@example.com",
    }
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", *args], cwd=str(repo), env=env, check=True, capture_output=True)

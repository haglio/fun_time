from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs


class CommandFailed(Exception):
    pass


def run_command(command: Sequence[str], cwd: Path | None = None) -> str:
    try:
        done = subprocess.run(list(command), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", **hidden_subprocess_kwargs())
    except OSError as error:
        raise CommandFailed(f"{' '.join(command)} could not start: {error}") from None
    if done.returncode:
        raise CommandFailed(f"{' '.join(command)}: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout

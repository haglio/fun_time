"""What the hosted Origenerator says it answers, as it shipped.

Neither repo installs the other, so the document it publishes at its checkout
root is the one place a line this session writes on its command file can be
held to the app that reads it.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from app_support.siblings import sibling_checkout
from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time.checkout_overrides import (
    ORIGENERATOR_DIR_OVERRIDE_NAME,
    STATE_DIRNAME,
    override_lines,
)
from fun_time.hosted_contract import CONTRACT_FILE, published_by
from fun_time.project_paths import PROJECT_DIR


def named_checkout() -> Path | None:
    """The Origenerator this checkout's ``state/origenerator_dir.txt`` names,
    which is the one a branch session of it hosts.

    ``None`` for one that is no longer there: a worktree retired after its
    branch landed leaves the file naming nothing, and the checkout beside this
    one is then the app worth holding a line to.
    """
    named = override_lines(PROJECT_DIR / STATE_DIRNAME / ORIGENERATOR_DIR_OVERRIDE_NAME)
    if not named or not Path(named[0]).exists():
        return None
    return Path(named[0])


def shipped_by(checkout: Path) -> dict:
    shown = subprocess.run(
        ["git", "show", f"origin/HEAD:{CONTRACT_FILE}"], cwd=str(checkout),
        capture_output=True, text=True, encoding="utf-8", check=True,
        **hidden_subprocess_kwargs())
    return json.loads(shown.stdout)


def _the_checkout_beside() -> Path | None:
    try:
        return sibling_checkout("origenerator", near=Path(__file__))
    except RuntimeError:
        return None


def published() -> dict:
    """The document as the checkout this one names stands, else as the
    Origenerator beside this one last shipped it."""
    named = named_checkout()
    if named is not None and (document := published_by(named)) is not None:
        return document
    if (beside := _the_checkout_beside()) is None:
        pytest.skip("no Origenerator checkout beside this one")
    return shipped_by(beside)


def answers(line: str, document: dict) -> bool:
    """Whether the hosted app that published *document* answers *line* on its
    command file, by the pattern it publishes for each line it answers."""
    return any(re.fullmatch(pattern, line) for pattern in document["command_patterns"])


def is_read_by(written: str, reads: dict) -> bool:
    """Whether what one app writes in a file the two hand each other is what the
    other reads there: a line its pattern matches, or a document carrying the
    keys it takes."""
    if "pattern" in reads:
        return re.fullmatch(reads["pattern"], written) is not None
    return sorted(json.loads(written)) == reads["keys"]

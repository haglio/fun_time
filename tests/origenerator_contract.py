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


def _checkouts_beside() -> list[Path]:
    return [parent / "origenerator" for parent in Path(__file__).resolve().parents]


def published() -> dict:
    """The document as the checkout this one names stands, else as the
    Origenerator beside this one last shipped it."""
    named = named_checkout()
    if named is not None and (document := published_by(named)) is not None:
        return document
    for checkout in _checkouts_beside():
        if checkout.is_dir():
            return shipped_by(checkout)
    pytest.skip("no Origenerator checkout beside this one")


def answers(line: str, document: dict) -> bool:
    """Whether the hosted app that published *document* answers *line* on its
    command file.

    It names every line it answers, a name in braces standing for whatever
    that line carries there: a file, a row, the words said.
    """
    case = re.IGNORECASE if document["command_case_blind"] else 0
    return any(re.fullmatch(_as_pattern(template), line, case)
               for template in document["command_lines"])


def _as_pattern(template: str) -> str:
    return "".join(".+" if piece.startswith("{") else re.escape(piece)
                   for piece in re.split(r"(\{\w+\})", template))

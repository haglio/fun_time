"""What the hosted Origenerator says it answers, read from the checkout it runs from.

Neither repo installs the other, so the document it publishes at its checkout
root is the one place a line this session writes on its command file can be
held to the app that reads it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fun_time.checkout_overrides import (
    ORIGENERATOR_DIR_OVERRIDE_NAME,
    STATE_DIRNAME,
    override_lines,
)
from fun_time.project_paths import PROJECT_DIR

CONTRACT_FILE = "origenerator_contract.json"


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


def published_by(checkout: Path) -> dict | None:
    document = Path(checkout) / CONTRACT_FILE
    if not document.is_file():
        return None
    return json.loads(document.read_text(encoding="utf-8"))


def published() -> dict:
    """The document, from the checkout this one names, else the one beside it."""
    named = named_checkout()
    beside = (parent / "origenerator" for parent in Path(__file__).resolve().parents)
    for checkout in (*([named] if named else []), *beside):
        document = published_by(checkout)
        if document is not None:
            return document
    pytest.skip(f"no Origenerator checkout beside this one publishes {CONTRACT_FILE}")


def answers(line: str) -> bool:
    """Whether the hosted app answers *line* on its command file.

    It names every line it answers, a name in braces standing for whatever
    that line carries there: a file, a row, the words said.
    """
    document = published()
    case = re.IGNORECASE if document["command_case_blind"] else 0
    return any(re.fullmatch(_as_pattern(template), line, case)
               for template in document["command_lines"])


def _as_pattern(template: str) -> str:
    return "".join(".+" if piece.startswith("{") else re.escape(piece)
                   for piece in re.split(r"(\{\w+\})", template))

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


def named_checkout_unless_retired() -> Path | None:
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
    named = named_checkout_unless_retired()
    if named is not None and (document := published_by(named)) is not None:
        return document
    if (beside := _the_checkout_beside()) is None:
        pytest.skip("no Origenerator checkout beside this one")
    return shipped_by(beside)


def answers(line: str, document: dict) -> bool:
    return any(re.fullmatch(pattern, line) for pattern in document["command_patterns"])

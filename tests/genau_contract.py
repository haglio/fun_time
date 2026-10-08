from __future__ import annotations

import json
from pathlib import Path

import pytest

from fun_time.checkout_overrides import GENAU_DIRS_OVERRIDE_NAME, STATE_DIRNAME, override_lines
from fun_time.project_paths import PROJECT_DIR

CONTRACT_FILE = "genau_contract.json"


def named_checkouts() -> list[Path]:
    named = override_lines(PROJECT_DIR / STATE_DIRNAME / GENAU_DIRS_OVERRIDE_NAME) or []
    return [Path(entry) for entry in named]


def published() -> dict:
    beside = (parent / "genau" for parent in Path(__file__).resolve().parents)
    for checkout in (*named_checkouts(), *beside):
        document = checkout / CONTRACT_FILE
        if document.is_file():
            return json.loads(document.read_text(encoding="utf-8"))
    pytest.skip(f"no Genau checkout beside this one publishes {CONTRACT_FILE}")

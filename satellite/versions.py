"""The verbs that put another version of a satellite's clip on screen."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

NEXT_VERSION = "NEXT_VERSION"
PREV_VERSION = "PREV_VERSION"

_SEPARATOR = "|"  # illegal in a Windows path, so a family reads back whole


def step_version(delta: int, versions: Sequence[str | Path]) -> str:
    verb = NEXT_VERSION if delta >= 0 else PREV_VERSION
    return f"{verb} {_SEPARATOR.join(str(version) for version in versions)}"


def version_files(value: str) -> list[Path]:
    return [Path(part) for part in value.split(_SEPARATOR) if part.strip()]

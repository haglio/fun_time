from __future__ import annotations

import json
from pathlib import Path

from app_support.config_reader import resolve_path

GENAU_LOG_FILES = ("genau_listener.log", "genau_crash.log")


def read_genau_config(genau_config_path: str | Path | None) -> dict:
    if genau_config_path is None:
        return {}
    try:
        raw = json.loads(Path(genau_config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def genaus_own_logs(genau_config_path: str | Path) -> tuple[Path, ...]:
    state_dir = read_genau_config(genau_config_path).get("state_dir")
    if not isinstance(state_dir, str):
        return ()
    folder = resolve_path(Path(genau_config_path).parent, state_dir)
    return tuple(folder / name for name in GENAU_LOG_FILES)

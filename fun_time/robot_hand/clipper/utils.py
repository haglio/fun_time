from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from ...runtime_support import hidden_subprocess_kwargs


def parse_timestamp(ts: str) -> float:
    parts = ts.strip().split(":")
    if len(parts) != 3:
        raise ValueError("Timestamp must be hh:mm:ss or hh:mm:ss.sss")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def format_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remaining = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{remaining:06.3f}"


def sanitize_name(name: str) -> str:
    name = name.strip()
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip().rstrip(".")


def find_tool(name: str) -> str | None:
    return shutil.which(name)


def subprocess_window_kwargs() -> dict[str, Any]:
    return hidden_subprocess_kwargs()


def safe_atomic_write_json(path: Path, payload: dict[str, Any]) -> tuple[bool, str]:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True, ""
    except PermissionError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False, str(exc)
    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False, str(exc)


def read_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

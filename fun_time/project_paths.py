from __future__ import annotations

import sys
from pathlib import Path

_LEVELS_THIS_MODULE_SITS_BELOW_THE_ROOT = 1
PROJECT_DIR = Path(__file__).resolve().parents[_LEVELS_THIS_MODULE_SITS_BELOW_THE_ROOT]
PROJECT_ICON = PROJECT_DIR / "icon.ico"
PROJECT_VR_ICON = PROJECT_DIR / "vr_icon.ico"
HOTKEY_SCRIPT_NAME = "windows_bridge_hotkeys.ahk"

STATE_DIRNAME = "state"

SIBLING_CHECKOUTS_OVERRIDE_NAME = "sibling_checkouts.txt"
SIBLING_CHECKOUTS_OVERRIDE_NAME_BEFORE_THE_RENAME = "genau_project_dirs.txt"


def override_lines(path: Path) -> list[str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def sibling_checkouts_named_in(state_dir: Path) -> list[str] | None:
    for name in (SIBLING_CHECKOUTS_OVERRIDE_NAME, SIBLING_CHECKOUTS_OVERRIDE_NAME_BEFORE_THE_RENAME):
        named = override_lines(state_dir / name)
        if named is not None:
            return named
    return None


def apply_sibling_checkouts_to_sys_path() -> list[str]:
    dirs = [entry for entry in (sibling_checkouts_named_in(PROJECT_DIR / STATE_DIRNAME) or [])
            if Path(entry).is_dir()]
    if dirs:
        for entry in reversed([str(PROJECT_DIR), *dirs]):
            if entry in sys.path:
                sys.path.remove(entry)
            sys.path.insert(0, entry)
    return dirs

"""Windows .lnk shortcuts, as what they launch."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app_support import win32

logger = logging.getLogger("windows_bridge")


@dataclass(frozen=True)
class Shortcut:
    target: str = ""
    work_dir: str = ""
    arguments: str = ""

    def command_line(self, *extra: str) -> str:
        parts = [subprocess.list2cmdline([self.target])]
        if self.arguments.strip():
            parts.append(self.arguments.strip())
        if extra:
            parts.append(subprocess.list2cmdline(extra))
        return " ".join(parts)


def read_shortcuts(folder: Path, *, pattern: str) -> dict[Path, Shortcut]:
    return {path: resolve_shortcut(str(path)) for path in folder.glob(pattern)}


def resolve_shortcut(shortcut_path: str) -> Shortcut:
    try:
        found = win32.read_shortcut(shortcut_path)
    except OSError:
        logger.debug("Could not read the shortcut %s", shortcut_path, exc_info=True)
        return Shortcut()
    return Shortcut(found.target, found.working_directory, found.arguments)

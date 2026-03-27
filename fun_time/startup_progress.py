"""Progress reporting for the startup sequence.

Used to communicate startup progress to the loading screen subprocess
via a shared progress file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ProgressReporter(Protocol):
    def advance(self, message: str) -> None: ...
    def finish(self) -> None: ...


class StartupProgress:
    """Writes progress updates to a file for the loading screen to read."""

    def __init__(self, progress_file: Path, total_steps: int) -> None:
        self._progress_file = progress_file
        self._total_steps = total_steps
        self._current_step = 0

    def advance(self, message: str) -> None:
        self._current_step += 1
        self._progress_file.write_text(
            f"{self._current_step}/{self._total_steps}|{message}",
            encoding="utf-8",
        )

    def finish(self) -> None:
        self._progress_file.write_text("DONE", encoding="utf-8")


class NullProgress:
    """Silent no-op progress reporter for integration mode."""

    def advance(self, message: str) -> None:
        pass

    def finish(self) -> None:
        pass

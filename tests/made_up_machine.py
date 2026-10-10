"""A machine's processes, made up for a test, as a module's sweeps would find them."""
from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from functools import partial
from unittest.mock import patch

from fun_time.process_sweep import processes_matching
from fun_time.win32_process import ProcessEntry

_FILETIME_AT_THE_UNIX_EPOCH = 116_444_736_000_000_000


def filetime(unix_seconds: float) -> int:
    return int(unix_seconds * 10_000_000) + _FILETIME_AT_THE_UNIX_EPOCH


@contextmanager
def running(module: str, machine: list[tuple[int, str, str, float]]) -> Iterator[list[int]]:
    """*module* sweeping a machine of (pid, image, command line, unix start) rows, read
    afresh on every sweep; yields the pids it ends, in the order it ends them."""
    def table() -> list[ProcessEntry]:
        return [ProcessEntry(pid, 1, image) for pid, image, _line, _at in machine]

    def command_line(pid: int) -> str | None:
        return next((line for each, _image, line, _at in machine if each == pid), None)

    def started(pid: int) -> int | None:
        return next((filetime(at) for each, _image, _line, at in machine if each == pid), None)

    ended: list[int] = []
    with ExitStack() as stack:
        stack.enter_context(patch(f"{module}.processes_matching", partial(
            processes_matching, table=table, read=command_line)))
        stack.enter_context(patch(f"{module}.kill_process_tree", ended.append))
        if hasattr(importlib.import_module(module), "get_process_creation_time"):
            stack.enter_context(patch(f"{module}.get_process_creation_time", started))
        yield ended

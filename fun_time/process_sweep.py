"""Which processes on the machine run an image and a command line, asked of Windows in-process."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .win32_process import command_line_of, process_table


@dataclass(frozen=True)
class Running:
    pid: int
    image: str
    command_line: str


def processes_matching(image: str, command_line: str, *, table=process_table,
                       read=command_line_of) -> list[Running]:
    found = []
    for entry in table():
        if not re.search(image, entry.image, re.IGNORECASE):
            continue
        started_with = read(entry.pid)
        if started_with is not None and re.search(command_line, started_with, re.IGNORECASE):
            found.append(Running(entry.pid, entry.image, started_with))
    return found

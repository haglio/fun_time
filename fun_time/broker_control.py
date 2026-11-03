from __future__ import annotations

from pathlib import Path

from app_support.file_channel import append_command


def write_broker_command(cmd_file: str | Path, verb: str) -> None:
    append_command(Path(cmd_file), verb)

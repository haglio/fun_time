"""fun_time's side of the OSR2 broker's command channel: a queue of
app_support.osr2_hold's verbs, which the broker drains whole each tick."""
from __future__ import annotations

from pathlib import Path

from app_support.file_channel import append_command


def write_broker_command(cmd_file: str | Path, verb: str) -> None:
    """Give the broker *verb* to act on at its next tick, after any still waiting."""
    append_command(Path(cmd_file), verb)

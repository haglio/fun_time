from __future__ import annotations

from pathlib import Path

from funestra.file_channel import append_command

_TRASH_FILE = "TRASH_FILE"


def trash_file_line(video: str | Path, already_at: str | Path | None = None) -> str:
    taken = "" if already_at is None else f"|{already_at}"
    return f"{_TRASH_FILE}|{video}{taken}"


def tell_origenerator_it_went(commands: Path | None):
    def told(video: Path, landed: Path) -> None:
        if commands is not None:
            append_command(commands, trash_file_line(video, landed))
    return told

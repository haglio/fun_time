from __future__ import annotations

from pathlib import Path

from funestra.file_channel import append_command
from funestra.funestra_verbs import SET_VOLUME

MIN_VOLUME = 0
MAX_VOLUME = 100

VOLUME_STEP = 10


def write_volume(path: Path, volume: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(volume), encoding="utf-8")


def read_volume(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").replace("﻿", "").strip())
    except (OSError, ValueError):
        return MAX_VOLUME


def publish_audio_level(
    *, main_funestra_cmd_file: Path, genau_cmd_file: Path, audio_volume_file: Path,
    volume: int, muted: bool,
) -> None:
    verb = f"{SET_VOLUME} {volume} {int(muted)}"
    append_command(main_funestra_cmd_file, verb)
    append_command(genau_cmd_file, verb)
    write_volume(audio_volume_file, MIN_VOLUME if muted else volume)

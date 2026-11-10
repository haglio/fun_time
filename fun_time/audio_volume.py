from __future__ import annotations

from pathlib import Path

from funestra.file_channel import append_command
from funestra.funestra_verbs import SET_VOLUME

MIN_VOLUME = 0
MAX_VOLUME = 100

VOLUME_STEP = 10

_NEVER_SILENT_FOR_WANT_OF_A_READABLE_LEVEL = MAX_VOLUME


def write_volume(path: Path, volume: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(volume), encoding="utf-8")


def read_volume(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").replace("﻿", "").strip())
    except (OSError, ValueError):
        return _NEVER_SILENT_FOR_WANT_OF_A_READABLE_LEVEL


def volume_verb(volume: int, muted: bool) -> str:
    return f"{SET_VOLUME} {volume} {int(muted)}"


def _the_level_for_a_companion_that_draws_no_control(volume: int, muted: bool) -> int:
    return MIN_VOLUME if muted else volume


def publish_audio_level(
    *, main_funestra_cmd_file: Path, genau_cmd_file: Path, audio_volume_file: Path,
    volume: int, muted: bool,
) -> None:
    for funestra_that_draws_the_level in (main_funestra_cmd_file, genau_cmd_file):
        append_command(funestra_that_draws_the_level, volume_verb(volume, muted))
    write_volume(audio_volume_file, _the_level_for_a_companion_that_draws_no_control(volume, muted))

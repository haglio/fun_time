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
    """Put *volume* / *muted* on the Main Funestra's audio sinks and on both Funestras.

    The Main Funestra's mpv carries the video's sound; the Genau audio companion carries the
    video music.  Which one is audible depends on the mode, so both are told the
    same level every time and the bridge alone holds the authoritative value.

    The companion is only ever asked to be quiet, so a mute reaches it as a level
    of zero.  The two *Funestras* also draw the level, and zero cannot tell muted
    from turned all the way down, nor what unmuting should return to -- so each
    gets the level and the mute and works the audible loudness out itself.  Genau
    is told in every mode, so the chip it draws is right when it takes the screen.

    One function, because sinks with different spellings of the same state are
    exactly the pair that drifts when each caller writes them itself.  A Funestra's
    verb *joins* its queue rather than replacing it: startup seeds more than one
    thing on the Main Funestra's channel before it is up to drain any, and a
    whole-file write would land the last and drop the rest.
    """
    verb = f"{SET_VOLUME} {volume} {int(muted)}"
    append_command(main_funestra_cmd_file, verb)
    append_command(genau_cmd_file, verb)
    write_volume(audio_volume_file, MIN_VOLUME if muted else volume)

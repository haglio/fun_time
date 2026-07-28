"""The primary display's sound level, and the file the bridge publishes it in.

The level is a percentage of each source's own volume: 100 leaves the media as
it was mastered, 0 is silence.  The dispatch loop holds the authoritative value
and writes it here; the Genau audio companion polls the file, and Nau is told
the same number over its own command channel.  Neither audio process may import
the dispatcher (it drags in the whole media library), so the one-integer wire
format lives in this leaf module that all three share.
"""
from __future__ import annotations

from pathlib import Path

from player_core.file_channel import append_command

MIN_VOLUME = 0
MAX_VOLUME = 100

# What one spoken "quieter" or "louder" moves the level by.
VOLUME_STEP = 10


def write_volume(path: Path, volume: int) -> None:
    """Publish *volume* as the current sound level."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(volume), encoding="utf-8")


def read_volume(path: Path) -> int:
    """The published sound level, or full volume when it has never been set."""
    try:
        return int(path.read_text(encoding="utf-8").replace("﻿", "").strip())
    except (OSError, ValueError):
        return MAX_VOLUME


def publish_audio_level(
    *, nau_cmd_file: Path, audio_volume_file: Path, volume: int, muted: bool
) -> None:
    """Put *volume* / *muted* on both of the primary display's audio sinks.

    Nau's mpv carries the video's sound; the Genau audio companion carries the
    clip music.  Which one is audible depends on the mode, so both are told the
    same level every time and the bridge alone holds the authoritative value.

    The companion is only ever asked to be quiet, so a mute reaches it as a level
    of zero and it stays dumb.  Nau also *draws* the level, and zero cannot tell
    it muted from turned all the way down, nor what unmuting should return to —
    so it gets the level and the mute, and works the audible loudness out itself.

    One function because startup seeds the session's opening level through it and
    every spoken "quieter" goes through it after: two sinks with different
    spellings of the same state is exactly the pair that drifts when each caller
    writes them itself.

    Nau's verb *joins* its queue rather than replacing it.  Startup seeds more
    than one thing on that channel — the level and whether F-mode is on — before
    Nau is up to drain any of them, and a whole-file write would land whichever
    went last and silently drop the other.
    """
    append_command(nau_cmd_file, f"SET_VOLUME {volume} {int(muted)}")
    write_volume(audio_volume_file, MIN_VOLUME if muted else volume)

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

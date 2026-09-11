"""Cached video-duration lookup for the main player's length-mode filtering.

Videos carry no reliable duration without probing, and probing every file on
startup is slow, so durations are cached in a small JSON file keyed by path.
An entry is trusted only while the file's mtime and size are unchanged; edit
or replace the file and it is re-probed. The prober is injected so tests never
shell out to ffprobe.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs

logger = logging.getLogger(__name__)


def ffprobe_duration(path: Path) -> float:
    """Duration of *path* in seconds via ffprobe (0.0 if it cannot be read)."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(
            cmd, text=True, **hidden_subprocess_kwargs()
        ).strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        logger.debug("ffprobe duration failed for %s: %s", path, exc)
        return 0.0


class DurationCache:
    """Path -> duration_s, persisted to JSON and invalidated on file change."""

    def __init__(self, cache_path: Path, *, prober=ffprobe_duration) -> None:
        self._cache_path = cache_path
        self._prober = prober
        self._entries: dict[str, dict] = self._load()
        self._dirty = False

    def _load(self) -> dict[str, dict]:
        try:
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def duration_for(self, path: Path) -> float:
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            return self._prober(path)
        cached = self._entries.get(key)
        if (
            cached is not None
            and cached.get("mtime") == stat.st_mtime
            and cached.get("size") == stat.st_size
        ):
            return cached["duration_s"]
        duration = self._prober(path)
        self._entries[key] = {
            "mtime": stat.st_mtime,
            "size": stat.st_size,
            "duration_s": duration,
        }
        self._dirty = True
        return duration

    def save(self) -> None:
        """Write the cache to disk if anything changed since load."""
        if not self._dirty:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._entries), encoding="utf-8")
        self._dirty = False

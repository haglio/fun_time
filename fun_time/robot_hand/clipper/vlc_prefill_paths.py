from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

from ...config import load_config
from .paths import MODULE_DIR

_VLC_TITLE_SUFFIXES = (
    " - VLC media player",
    " - VLC media player (Direct3D11 output)",
)
_VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v")
_TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\b")


def looks_like_vlc_title(title: str) -> bool:
    lower = title.lower()
    return any(suffix.lower() in lower for suffix in _VLC_TITLE_SUFFIXES)


def timestamp_seconds_from_title(title: str) -> float | None:
    match = _TIMESTAMP_RE.search(title)
    if match is None:
        return None
    parts = match.group(0).split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def strip_vlc_title_suffix(title: str) -> str:
    result = title.strip()
    for suffix in _VLC_TITLE_SUFFIXES:
        if result.endswith(suffix):
            return result[: -len(suffix)].strip()
    return result


def resolve_media_path_from_title(title: str) -> Path | None:
    cleaned = strip_vlc_title_suffix(title)
    if not cleaned:
        return None
    cleaned = _TIMESTAMP_RE.sub("", cleaned).strip(" -\u2013")
    return resolve_media_path(cleaned)


def resolve_media_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    candidate = raw.strip().strip('"')
    if not candidate:
        return None
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme == "file":
        candidate = urllib.request.url2pathname(parsed.path)
    path = Path(candidate)
    if path.is_file():
        return path
    filename = path.name or candidate
    if not filename:
        return None
    return search_roots_for_filename(filename)


@lru_cache(maxsize=1)
def search_roots() -> tuple[Path, ...]:
    roots: list[Path] = [MODULE_DIR / "raw_clips"]
    try:
        config = load_config()
    except Exception:
        config = None
    if config is not None:
        roots.extend(config.paths.primary_vlc_dirs)
        roots.extend(config.paths.portrait_dirs)
        roots.extend(config.paths.landscape_dirs)
        roots.append(config.paths.weird_dir)
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return tuple(deduped)


def search_roots_for_filename(filename: str) -> Path | None:
    candidates = [filename]
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    if not suffix:
        candidates.extend(f"{stem}{ext}" for ext in _VIDEO_EXTENSIONS)
    for root in search_roots():
        for name in candidates:
            direct = root / name
            if direct.is_file():
                return direct
        for name in candidates:
            match = next(root.rglob(name), None)
            if match is not None and match.is_file():
                return match
    return None


_resolve_media_path = resolve_media_path
_resolve_media_path_from_title = resolve_media_path_from_title
_search_roots = search_roots
_search_roots_for_filename = search_roots_for_filename
_strip_vlc_title_suffix = strip_vlc_title_suffix
_timestamp_seconds_from_title = timestamp_seconds_from_title
_looks_like_vlc_title = looks_like_vlc_title

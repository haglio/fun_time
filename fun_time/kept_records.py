# pCloud lists a folder at once and can take hours over one file in it, so each record is
# read again only when its folder's listing shows a new date or size for it.
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from app_support.file_channel import publish_whole

from .folder_listings import FolderListings
from .media_metadata import load_metadata, metadata_path_for


def kept_file_for(folder: Path, sources: str) -> Path:
    return folder / f"kept_records_{hashlib.sha256(sources.encode('utf-8')).hexdigest()[:12]}.json"


def records_of(videos: Iterable[str], metadata_root: Path | None, *,
               kept: Path | None) -> dict[str, dict]:
    videos = list(videos)
    if metadata_root is None:
        return {video: {} for video in videos}
    remembered = _remembered(kept)
    listings = FolderListings()
    records: dict[str, dict] = {}
    still: dict[str, list] = {}
    for video in videos:
        sidecar = metadata_path_for(video, metadata_root)
        if sidecar is None or not listings.holds(sidecar):
            records[video] = {}
            continue
        stamp = [listings.modified(sidecar), listings.size(sidecar)]
        hit = remembered.get(str(sidecar))
        record = hit[2] if hit is not None and hit[:2] == stamp else load_metadata(sidecar)
        still[str(sidecar)] = [*stamp, record]
        records[video] = record
    if kept is not None and still != remembered:
        publish_whole(kept, json.dumps(still))
    return records


def _remembered(kept: Path | None) -> dict[str, list]:
    if kept is None:
        return {}
    try:
        payload = json.loads(kept.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}

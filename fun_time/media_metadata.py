from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from functools import partial
from pathlib import Path

from app_support import favorites
from app_support.mirrored_tree import LIBRARY_DIR_NAME, library_roots_beside, record_path
from app_support.video_names import clip_title, version_group_of
from funestra import video_groups
from funestra.flick_folder import SUPPORTED_VIDEO_EXTS
from funestra.satellite_hud import actions_are_filtered
from funestra.video_families import actions_text, normalized_text
from funestra.video_groups import GroupIndex
from shared_ui.orientation import LANDSCAPE, ORIENTATIONS


def _library_holding(folder: Path) -> Path:
    for ancestor in (folder, *folder.parents):
        if ancestor.name.lower() == LIBRARY_DIR_NAME:
            return ancestor
    return folder.parent


def metadata_path_for(
    video_path: str | Path,
    metadata_root: str | Path | None,
    *,
    outlying_dirs: Iterable[str | Path] = (),
) -> Path | None:
    if metadata_root is None:
        return None
    return record_path(
        video_path, Path(metadata_root),
        more_roots=tuple(_library_holding(Path(folder)) for folder in outlying_dirs))


EXCERPT = "excerpt"
SHORT = "short"
FULL_LENGTH = "full_length"


def video_type_of(payload: dict) -> str:
    video = payload.get("video")
    if isinstance(video, dict) and video.get("type"):
        return str(video["type"])
    return EXCERPT if _carved_before_kinds_were_recorded(payload) else ""


def _carved_before_kinds_were_recorded(payload: dict) -> bool:
    return isinstance(payload.get("clip"), dict)


ORIENTATION_FIELD = "orientation"


def recorded_orientation(payload: dict) -> str:
    video = payload.get("video")
    said = video.get(ORIENTATION_FIELD) if isinstance(video, dict) else None
    return said if said in ORIENTATIONS else LANDSCAPE


DURATION_FIELD = "duration_seconds"


def recorded_duration_seconds(payload: dict) -> float | None:
    video = payload.get("video")
    seconds = video.get(DURATION_FIELD) if isinstance(video, dict) else None
    return float(seconds) if isinstance(seconds, (int, float)) else None


NUMBER_IN_A_DOWNLOADED_SET = re.compile(r"\(\d+\)")


def downloaded_set_number_of(video: str | Path) -> str:
    found = NUMBER_IN_A_DOWNLOADED_SET.findall(Path(video).stem)
    return found[-1] if found else ""


def recorded_group(payload: dict, video: str | Path) -> str:
    family_evolver_gave_the_whole_set = version_group_of(payload)
    own_number = downloaded_set_number_of(video)
    if family_evolver_gave_the_whole_set and own_number:
        return f"{family_evolver_gave_the_whole_set} {own_number}"
    return family_evolver_gave_the_whole_set


TITLE_FIELD = "title"


def video_title(payload: dict, video: str | Path) -> str:
    return (
        str(payload.get(TITLE_FIELD, "") or "").strip()
        or clip_title(payload)
        or recorded_group(payload, video)
        or Path(video).stem
    )


def load_metadata(json_path: str | Path) -> dict:
    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def recorded_for(
    video_path: str | Path,
    metadata_root: str | Path | None,
    *,
    outlying_dirs: Iterable[str | Path] = (),
) -> dict:
    record = metadata_path_for(video_path, metadata_root, outlying_dirs=outlying_dirs)
    return {} if record is None else load_metadata(record)


def is_favorite(video_path: str | Path, metadata_root: str | Path | None) -> bool:
    return favorites.is_favorite(recorded_for(video_path, metadata_root))


def mark_favorite(video_path: str | Path, metadata_root: str | Path | None, favorite: bool, *,
                  page: str = "") -> bool:
    record = metadata_path_for(video_path, metadata_root)
    return record is not None and favorites.mark(record, favorite, page=page)


def favorites_in(metadata_root: str | Path) -> list[favorites.Favorite]:
    return favorites.favorites_under(
        Path(metadata_root), video_roots=library_roots_beside(Path(metadata_root)),
        video_suffixes=sorted(SUPPORTED_VIDEO_EXTS))


def matches_query(metadata: dict, query: str) -> bool:
    return not normalized_text(query) or actions_are_filtered(actions_text(metadata), query)


def path_matches_query(
    video_path: str,
    metadata_root: str | Path | None,
    query: str,
) -> bool:
    if not normalized_text(query):
        return True
    return matches_query(recorded_for(video_path, metadata_root), query)


def _records_under(metadata_root: str | Path | None) -> Callable[[str], dict]:
    return partial(recorded_for, metadata_root=metadata_root)


def build_group_index(
    video_paths: Iterable[str],
    metadata_root: str | Path | None,
) -> GroupIndex:
    return video_groups.build_group_index(video_paths, _records_under(metadata_root))


class GroupIndexCache:
    def __init__(self) -> None:
        self._indexes: dict[str, GroupIndex] = {}

    def index_for(
        self,
        cache_key: str,
        *,
        paths_supplier,
        metadata_root: str | Path | None,
        must_contain: str | None = None,
    ) -> GroupIndex:
        index = self._indexes.get(cache_key)
        if index is None or _was_built_before_this_arrived(index, must_contain):
            index = build_group_index(paths_supplier(), metadata_root)
            self._indexes[cache_key] = index
        return index

    def learn(self, cache_key: str, path: str, metadata_root: str | Path | None) -> None:
        index = self._indexes.get(cache_key)
        if index is not None:
            index.learn(path, _records_under(metadata_root))

    def forget(self, path: str) -> None:
        for index in self._indexes.values():
            index.forget(path)

    def clear(self) -> None:
        self._indexes.clear()


def _was_built_before_this_arrived(index: GroupIndex, video: str | None) -> bool:
    return video is not None and not index.contains(video)


SESSION_INDEXES = GroupIndexCache()

cached_group_index = SESSION_INDEXES.index_for
learn_indexed_video = SESSION_INDEXES.learn
forget_indexed_video = SESSION_INDEXES.forget

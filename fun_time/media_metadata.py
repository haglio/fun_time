from __future__ import annotations

import json
import re
from bisect import insort
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app_support import favorites
from app_support.json_store import locked_update
from app_support.mirrored_tree import LIBRARY_DIR_NAME, library_roots_beside, mirrored_path
from app_support.video_names import clip_title, version_group_of
from funestra.flick_folder import SUPPORTED_VIDEO_EXTS
from funestra.video_families import action_group_key, filter_keeps, normalized_text, seed_group_key
from shared_ui.orientation import LANDSCAPE, ORIENTATIONS


def normalize_path_key(path: str) -> str:
    return path.strip().lower()


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
    metadata_root = Path(metadata_root)
    return mirrored_path(
        video_path,
        roots=(*library_roots_beside(metadata_root),
               *(_library_holding(Path(folder)) for folder in outlying_dirs)),
        mirror_root=metadata_root,
        suffix=".json",
    )


EXCERPT = "excerpt"
SHORT = "short"
FULL_LENGTH = "full_length"


def video_type_of(payload: dict) -> str:
    video = payload.get("video")
    if isinstance(video, dict) and video.get("type"):
        return str(video["type"])
    return EXCERPT if isinstance(payload.get("clip"), dict) else ""


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


WATCH_BLOCK = "watch"


_COPY_INDEX = re.compile(r"\(\d+\)")


def recorded_group(payload: dict, video: str | Path) -> str:
    group = version_group_of(payload)
    if not group:
        return ""
    found = _COPY_INDEX.findall(Path(video).stem)
    return f"{group} {found[-1]}" if found else group


TITLE_FIELD = "title"


def video_title(payload: dict, video: str | Path) -> str:
    return (
        str(payload.get(TITLE_FIELD, "") or "").strip()
        or clip_title(payload)
        or recorded_group(payload, video)
        or Path(video).stem
    )


def watch_weight_of(payload: dict) -> float:
    block = payload.get(WATCH_BLOCK)
    if not isinstance(block, dict):
        return 1.0
    try:
        return float(block.get("weight", 1.0))
    except (TypeError, ValueError):
        return 1.0


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


WRONG_ACTION_FIELD = "wrong_action"


def reject_action(video_path: str | Path, metadata_root: str | Path | None) -> str:
    json_path = metadata_path_for(video_path, metadata_root)
    if json_path is None:
        return ""
    struck = ""

    def strike_the_act_out(payload: dict) -> dict | None:
        nonlocal struck
        video = payload.get("video")
        if not isinstance(video, dict):
            return None
        struck = str(video.pop("action", "") or "").strip()
        if not struck:
            return None
        video[WRONG_ACTION_FIELD] = struck
        return payload

    try:
        locked_update(json_path, strike_the_act_out)
    except ValueError:
        return ""
    return struck


def filter_haystack(metadata: dict) -> str:
    return normalized_text((metadata.get("video") or {}).get("action"))


def matches_query(metadata: dict, query: str) -> bool:
    return filter_keeps(filter_haystack(metadata), query)


def path_matches_query(
    video_path: str,
    metadata_root: str | Path | None,
    query: str,
) -> bool:
    if not normalized_text(query):
        return True
    return matches_query(recorded_for(video_path, metadata_root), query)


def scene_tags(metadata: dict) -> frozenset[str]:
    source = metadata.get("source_image") or {}
    text = source.get("positive_prompt") or (metadata.get("video") or {}).get("prompt") or ""
    return frozenset(tag for tag in (normalized_text(part) for part in str(text).split(",")) if tag)


def _tag_overlap(one: frozenset[str], other: frozenset[str]) -> float:
    if not one or not other:
        return 0.0
    return len(one & other) / len(one | other)


@dataclass(frozen=True)
class VideoEntry:
    path: str
    action: str = ""
    action_key: str | None = None
    seed_key: tuple[str, str] | None = None
    scene_tags: frozenset[str] = frozenset()
    from_image: bool = False
    weight: float = 1.0


@dataclass(frozen=True)
class GroupIndex:
    entries: dict[str, VideoEntry]
    action_items: dict[str, list[str]]
    seed_items: dict[str, list[str]]

    def entry(self, path: str) -> VideoEntry:
        return self.entries.get(normalize_path_key(path)) or _UNKNOWN_CLIP

    def contains(self, path: str) -> bool:
        return normalize_path_key(path) in self.entries

    def weight_of(self, path: str) -> float:
        return self.entry(path).weight

    def act_of(self, path: str) -> str:
        return self.entry(path).action

    def learn(self, path: str, metadata_root: str | Path | None) -> None:
        if self.contains(path):
            return
        entry = _entry_for(path, recorded_for(path, metadata_root))
        self.entries[normalize_path_key(path)] = entry
        if entry.action_key is not None:
            insort(self.action_items.setdefault(entry.action_key, []), path)
        if entry.seed_key is not None:
            insort(self.seed_items.setdefault(entry.seed_key[0], []), path)

    def forget(self, path: str) -> None:
        key = normalize_path_key(path)
        if self.entries.pop(key, None) is None:
            return
        for items in (*self.action_items.values(), *self.seed_items.values()):
            items[:] = [item for item in items if normalize_path_key(item) != key]

    def indexed_path(self, key: str, fallback: str = "") -> str:
        entry = self.entries.get(key)
        return entry.path if entry is not None else fallback


_UNKNOWN_CLIP = VideoEntry(path="")


def action_group_items(index: GroupIndex, path: str) -> list[str]:
    key = index.entry(path).action_key
    if key is None:
        return []
    return list(index.action_items[key])


def indexed_act(index: GroupIndex, path: str) -> str:
    return normalized_text(index.act_of(path))


def seed_family_items(index: GroupIndex, path: str) -> list[str]:
    seed_key = index.entry(path).seed_key
    if seed_key is None:
        return []
    family, _seed = seed_key
    action = indexed_act(index, path)
    return [
        item
        for item in index.seed_items[family]
        if indexed_act(index, item) == action
    ]


WIDEN_ADDITIONS = 6


def widened_seed_items(
    index: GroupIndex, path: str, additions: int = WIDEN_ADDITIONS
) -> list[str]:
    key = normalize_path_key(path)
    items = list(seed_family_items(index, path))
    if not any(normalize_path_key(item) == key for item in items):
        items.insert(0, index.indexed_path(key, path))
    seen = {normalize_path_key(item) for item in items} | {key}
    action = indexed_act(index, path)
    mine_entry = index.entry(path)
    ranked = sorted(
        (
            (
                other.from_image == mine_entry.from_image,
                _tag_overlap(mine_entry.scene_tags, other.scene_tags),
                other_key,
            )
            for other_key, other in index.entries.items()
            if other_key not in seen and normalized_text(other.action) == action
        ),
        key=lambda scored: (-scored[0], -scored[1], scored[2]),
    )
    items.extend(index.entries[scored[-1]].path for scored in ranked[:max(additions, 0)])
    return items


def action_label(index: GroupIndex, path: str) -> str:
    key = normalize_path_key(path)
    entry = index.entry(path)
    action, group = entry.action, entry.action_key
    if not action or group is None:
        return action
    twins = [
        item
        for item in index.action_items[group]
        if index.act_of(item) == action
    ]
    if len(twins) <= 1:
        return action
    position = next(
        (number for number, item in enumerate(twins) if normalize_path_key(item) == key), 0
    )
    return f"{action} {position + 1}"


def _entry_for(path: str, metadata: dict) -> VideoEntry:
    return VideoEntry(
        path=path,
        action=str((metadata.get("video") or {}).get("action") or "").strip(),
        action_key=action_group_key(metadata),
        seed_key=seed_group_key(metadata),
        scene_tags=scene_tags(metadata),
        from_image=bool(metadata.get("source_image")),
        weight=watch_weight_of(metadata),
    )


def build_group_index(
    video_paths: Iterable[str],
    metadata_root: str | Path | None,
) -> GroupIndex:
    index = GroupIndex(entries={}, action_items={}, seed_items={})
    for path in video_paths:
        index.learn(path, metadata_root)
    return index


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
        if index is None or (must_contain is not None and not index.contains(must_contain)):
            index = build_group_index(paths_supplier(), metadata_root)
            self._indexes[cache_key] = index
        return index

    def learn(self, cache_key: str, path: str, metadata_root: str | Path | None) -> None:
        index = self._indexes.get(cache_key)
        if index is not None:
            index.learn(path, metadata_root)

    def forget(self, path: str) -> None:
        for index in self._indexes.values():
            index.forget(path)

    def clear(self) -> None:
        self._indexes.clear()


SESSION_INDEXES = GroupIndexCache()

cached_group_index = SESSION_INDEXES.index_for
reset_group_index_cache = SESSION_INDEXES.clear
learn_indexed_video = SESSION_INDEXES.learn
forget_indexed_video = SESSION_INDEXES.forget

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app_support.video_names import normalize_title
from funestra.playlist import PlaylistItem

from fun_time.media_metadata import NUMBER_IN_A_DOWNLOADED_SET, downloaded_set_number_of


@dataclass(frozen=True)
class LibraryEntry:
    video: Path
    funscript: Path | None
    size: int


@dataclass(frozen=True)
class VersionGroup:
    entries: tuple[LibraryEntry, ...]

    @property
    def canonical(self) -> LibraryEntry:
        return self.entries[0]

    @property
    def alternates(self) -> list[LibraryEntry]:
        return list(self.entries[1:])


def _title_tokens(stem: str) -> tuple[str, ...]:
    return tuple(normalize_title(stem).split())


def _begins_the_same_scene(shorter: tuple[str, ...], longer: tuple[str, ...]) -> bool:
    if len(shorter) > len(longer) or longer[: len(shorter)] != shorter:
        return False
    extra = longer[len(shorter):]
    return not (extra and NUMBER_IN_A_DOWNLOADED_SET.fullmatch(extra[0]))


def _group_whose_title_begins_this_one(
    groups: list[list[LibraryEntry]],
    group_tokens: list[tuple[str, ...]],
    toks: tuple[str, ...],
) -> list[LibraryEntry] | None:
    if not toks:
        return None
    for group, gtoks in zip(groups, group_tokens, strict=True):
        if gtoks and _begins_the_same_scene(gtoks, toks):
            return group
    return None


def group_versions(
    entries: list[LibraryEntry],
    group_id_of: Callable[[Path], str | None] | None = None,
) -> list[VersionGroup]:
    if group_id_of is not None:
        return _group_by_recorded_id(entries, group_id_of)
    return _group_by_name(entries)


def _group_by_recorded_id(
    entries: list[LibraryEntry],
    group_id_of: Callable[[Path], str | None],
) -> list[VersionGroup]:
    by_id: dict[str, list[LibraryEntry]] = {}
    order: list[str] = []
    unrecorded: list[LibraryEntry] = []
    for entry in entries:
        gid = group_id_of(entry.video)
        if gid is None:
            unrecorded.append(entry)
            continue
        gid = f"{gid}{downloaded_set_number_of(entry.video)}"
        if gid not in by_id:
            by_id[gid] = []
            order.append(gid)
        by_id[gid].append(entry)
    groups = [
        VersionGroup(entries=tuple(sorted(by_id[gid], key=lambda e: (-e.size, str(e.video)))))
        for gid in order
    ]
    groups.extend(_group_by_name(unrecorded))
    return groups


def _group_by_name(entries: list[LibraryEntry]) -> list[VersionGroup]:
    order = {entry: i for i, entry in enumerate(entries)}
    tokens = {entry: _title_tokens(entry.video.stem) for entry in entries}
    groups: list[list[LibraryEntry]] = []
    group_tokens: list[tuple[str, ...]] = []
    for entry in sorted(entries, key=lambda e: (len(tokens[e]), order[e])):
        toks = tokens[entry]
        anchor = _group_whose_title_begins_this_one(groups, group_tokens, toks)
        if anchor is None:
            groups.append([entry])
            group_tokens.append(toks)
        else:
            anchor.append(entry)
    groups.sort(key=lambda group: min(order[m] for m in group))
    return [
        VersionGroup(entries=tuple(sorted(group, key=lambda e: (-e.size, str(e.video)))))
        for group in groups
    ]


def collapse_playlist_versions(
    items: list[PlaylistItem],
    version_index: dict[Path, list[PlaylistItem]],
) -> list[PlaylistItem]:
    funscript_by_video = {item.path: item.funscript for item in items}
    collapsed: list[PlaylistItem] = []
    seen: set[Path] = set()
    for item in items:
        versions = version_index.get(item.path)
        if versions:
            group_id = versions[0].path
            keep = next((version.path for version in versions
                         if version.path in funscript_by_video), item.path)
        else:
            group_id = keep = item.path
        if group_id in seen:
            continue
        seen.add(group_id)
        collapsed.append(PlaylistItem(keep, funscript_by_video.get(keep)))
    return collapsed


def entries_to_items(entries: list[LibraryEntry]) -> list[PlaylistItem]:
    return [PlaylistItem(e.video, e.funscript) for e in entries]


def version_index_from_groups(
    groups: list[VersionGroup],
) -> dict[Path, list[PlaylistItem]]:
    index: dict[Path, list[PlaylistItem]] = {}
    for group in groups:
        versions = [group.canonical, *group.alternates]
        family = entries_to_items(versions)
        for entry in versions:
            index[entry.video] = family
    return index

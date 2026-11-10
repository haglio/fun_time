from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from funestra.flick_folder import flat_flicks_in, vr_flicks_in
from funestra.video_groups import normalize_path_key

from .folder_listings import FolderListings
from .media_metadata import (
    EXCERPT,
    recorded_for,
    recorded_group,
    video_title,
    video_type_of,
)
from .modes import collect_video_files, source_roots

_LARGEST = 0
_SMALLEST = -1


@dataclass(frozen=True)
class LibraryHandle:
    title: str
    versions: tuple[str, ...]
    section: str = ""

    @property
    def video(self) -> str:
        return self.versions[_LARGEST]

    @property
    def display_name(self) -> str:
        return self.title

    @property
    def previews(self) -> tuple[str, ...]:
        return (self.preview,)

    @property
    def preview(self) -> str:
        return self.quickest_version_to_decode

    @property
    def quickest_version_to_decode(self) -> str:
        return self.versions[_SMALLEST]


def handle_for(handles: Sequence[LibraryHandle], video: str) -> LibraryHandle | None:
    key = normalize_path_key(video)
    if not key:
        return None
    return next(
        (
            handle
            for handle in handles
            if any(normalize_path_key(version) == key for version in handle.versions)
        ),
        None,
    )


FULL_FOLDER = "full"
CLIPS_FOLDER = "clips"


def source_path(video: str, sources: str) -> tuple[str, ...]:
    path = Path(video)
    for source in sources.split("|"):
        root = source.strip()
        if not root:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        return relative.parts[:-1]
    return ()


def source_folder(video: str, sources: str) -> str:
    library_division, *_pipeline_stages = source_path(video, sources) or ("",)
    return library_division


def band_names(bands: dict[tuple[str, bool], list[tuple[str, ...]]]) -> dict[tuple[str, bool], str]:
    names = {}
    for (folder, is_clip), paths in bands.items():
        mate = bands.get((folder, not is_clip), [])
        mine = _own_folder(paths, mate)
        if mine:
            names[(folder, is_clip)] = f"{folder}/{mine}"
        elif is_clip:
            names[(folder, is_clip)] = f"{folder}/{CLIPS_FOLDER}"
        elif mate:
            names[(folder, is_clip)] = f"{folder}/{FULL_FOLDER}"
        else:
            names[(folder, is_clip)] = folder
    return names


def _own_folder(band: list[tuple[str, ...]], mate: list[tuple[str, ...]]) -> str:
    mine, theirs = _dominant_subfolder(band), _dominant_subfolder(mate)
    if not mine or not theirs or mine == theirs:
        return ""
    return mine if _holds_mostly(mine, band, mate) and _holds_mostly(theirs, mate, band) else ""


def _holds_mostly(subfolder: str, band: list[tuple[str, ...]], mate: list[tuple[str, ...]]) -> bool:
    return _count_under(subfolder, band) > _count_under(subfolder, mate)


def _count_under(subfolder: str, paths: list[tuple[str, ...]]) -> int:
    return sum(1 for parts in paths if len(parts) > 1 and parts[1] == subfolder)


def _dominant_subfolder(paths: list[tuple[str, ...]]) -> str:
    seconds = Counter(parts[1] for parts in paths if len(parts) > 1)
    if not seconds:
        return ""
    name, count = seconds.most_common(1)[0]
    return name if count * 2 > sum(seconds.values()) else ""


def cut_folders(
    recorded_excerpts: list[tuple[str, ...]], everything_else: list[tuple[str, ...]]
) -> dict[str, str]:
    def by_source(paths: list[tuple[str, ...]]) -> dict[str, list[tuple[str, ...]]]:
        grouped: dict[str, list[tuple[str, ...]]] = {}
        for path in paths:
            if path:
                grouped.setdefault(path[0], []).append(path)
        return grouped

    cuts, rest = by_source(recorded_excerpts), by_source(everything_else)
    folders = {}
    for folder, paths in cuts.items():
        mine = _own_folder(paths, rest.get(folder, []))
        if mine:
            folders[folder] = mine
    return folders


def is_an_excerpt(path: tuple[str, ...], kind: str, cuts: dict[str, str]) -> bool:
    if kind:
        return kind == EXCERPT
    return len(path) > 1 and cuts.get(path[0]) == path[1]


def _largest_first(versions: list[str], sizes: FolderListings) -> tuple[str, ...]:
    return tuple(sorted(versions, key=lambda video: (-sizes.size(video), video)))


def build_library_handles(
    sources: str, metadata_root: Path | None, *, listing=collect_video_files,
) -> list[LibraryHandle]:
    videos = listing(sources)
    sizes = FolderListings()
    payloads = {video: recorded_for(video, metadata_root) for video in videos}
    paths = {video: source_path(video, sources) for video in videos}
    kinds = {video: video_type_of(payloads[video]) for video in videos}
    cuts = cut_folders(
        [paths[video] for video in videos if kinds[video] == EXCERPT],
        [paths[video] for video in videos if kinds[video] != EXCERPT],
    )
    groups = {
        video: recorded_group(payloads[video], video) or Path(video).stem
        for video in videos
    }

    families: dict[tuple[str, bool], list[str]] = {}
    for video in videos:
        families.setdefault(
            (groups[video], is_an_excerpt(paths[video], kinds[video], cuts)), []
        ).append(video)

    played = {
        family: _largest_first(videos, sizes) for family, videos in families.items()
    }
    keys = {
        family: (source_folder(versions[0], sources), family[1])
        for family, versions in played.items()
    }
    bands: dict[tuple[str, bool], list[tuple[str, ...]]] = {}
    for family, key in keys.items():
        bands.setdefault(key, []).append(source_path(played[family][0], sources))
    names = band_names(bands)

    weight = Counter(folder for folder, _clip in keys.values())
    shown = {
        family: video_title(payloads[played[family][0]], played[family][0])
        for family in played
    }
    return [
        LibraryHandle(
            title=shown[family], versions=played[family], section=names[keys[family]]
        )
        for family in sorted(
            played,
            key=lambda family: (
                -weight[keys[family][0]], keys[family][0], keys[family][1],
                shown[family].casefold(), shown[family],
            ),
        )
    ]


_VR_FOLDER = "VR"
_FLAT_FOLDER = "2D"


def handles_by_shape(
    sources: str, vr_sources: str, metadata_root: Path | None, *, listing=collect_video_files,
) -> list[LibraryHandle]:
    vr_roots = source_roots(vr_sources)
    flat_sources = "|".join(str(root) for root in source_roots(sources) if root not in vr_roots)
    shelves = [
        (name, handles)
        for name, spec in ((_VR_FOLDER, vr_sources), (_FLAT_FOLDER, flat_sources))
        if (handles := build_library_handles(spec, metadata_root, listing=listing))
    ]
    if len(shelves) <= 1:
        return [handle for _name, handles in shelves for handle in handles]
    return [
        replace(handle, section="/".join(part for part in (name, handle.section) if part))
        for name, handles in shelves
        for handle in handles
    ]


def genau_vr_flicks(flicks_folder: str) -> str:
    return str(vr_flicks_in(Path(flicks_folder))) if flicks_folder else ""


def genau_flick_sources(flicks_folder: str) -> str:
    if not flicks_folder:
        return ""
    return "|".join((genau_vr_flicks(flicks_folder), str(flat_flicks_in(Path(flicks_folder)))))

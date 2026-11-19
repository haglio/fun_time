from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from app_support import funscript
from app_support.mirrored_tree import LIBRARY_DIR_NAME
from funestra.flick_folder import SUPPORTED_VIDEO_EXTS
from funestra.playlist import PlaylistItem, write_playlist
from funestra.video_groups import (
    normalize_path_key,
)

from .folder_listings import FolderListings
from .vr_videos import keep_shapes

PLAYLIST_PORTRAIT = "portrait_playlist"
PLAYLIST_LANDSCAPE = "landscape_playlist"
PLAYLIST_MAIN_FUNESTRA = "main_player_playlist"


def is_supported_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_VIDEO_EXTS


def source_roots(source_spec: str) -> list[Path]:
    return [Path(part.strip()) for part in source_spec.split("|") if part.strip()]


def collect_video_files(source_spec: str) -> list[str]:
    return videos_in(source_roots(source_spec))


def videos_in(roots: Iterable[Path]) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for root_path in roots:
        for video in _videos_under(root_path):
            key = normalize_path_key(video)
            if key in seen:
                continue
            seen.add(key)
            files.append(video)
    return files


def _videos_under(root: Path) -> Iterator[str]:
    try:
        with os.scandir(root) as listing:
            entries = list(listing)
    except NotADirectoryError:
        spelled = str(root)
        if is_supported_video_path(spelled):
            yield spelled
        return
    except OSError:
        return
    for entry in entries:
        if entry.is_dir(follow_symlinks=False):
            yield from _videos_under(Path(entry.path))
        elif entry.is_file(follow_symlinks=False) and is_supported_video_path(entry.name):
            yield entry.path


def _library_folder_of(video: Path) -> Path | None:
    return next((folder for folder in reversed(video.parents)
                 if folder.name.lower() == folder.parent.name.lower() == LIBRARY_DIR_NAME), None)


def build_mirrored_funscript_path(video_path: str, metadata_root: Path | None = None) -> str:
    video = Path(video_path)
    library = _library_folder_of(video)
    if library is None:
        return ""
    trees_beside = metadata_root.parent if metadata_root is not None else library.parent
    script = funscript.script_of(video, library=library,
                                 scripts_tree=funscript.scripts_tree_in(trees_beside))
    return "" if script is None else str(script)


def matching_funscript(video_path: str, metadata_root: Path | None = None, *,
                       listings: FolderListings | None = None) -> str | None:
    mirrored = build_mirrored_funscript_path(video_path, metadata_root)
    there = listings.holds if listings is not None else os.path.exists
    return mirrored if mirrored and there(mirrored) else None


def sort_paths_by_recency(
    paths: list[str], listings: FolderListings | None = None
) -> list[str]:
    dates = listings or FolderListings()
    return sorted(paths, key=dates.modified, reverse=True)


@dataclass(frozen=True)
class VideoShapes:
    vr_dirs: str = ""
    plays_vr: bool = True
    plays_flat: bool = True

    @property
    def offered(self) -> bool:
        return bool(source_roots(self.vr_dirs))

    @property
    def narrows(self) -> bool:
        return self.offered and not (self.plays_vr and self.plays_flat)

    def keep(self, paths: list[str]) -> list[str]:
        if not self.narrows:
            return list(paths)
        return keep_shapes(paths, vr_dirs=source_roots(self.vr_dirs),
                           plays_vr=self.plays_vr, plays_flat=self.plays_flat)


def build_playlist_file_path(state_dir: Path, name: str) -> Path:
    return state_dir / f"{name}.tsv"


def same_file_to_windows(left: str, right: str) -> bool:
    return normalize_path_key(left) == normalize_path_key(right)


def rotated_onto[T](entries: list[T], last_video: str, *,
                    path_of: Callable[[T], object] = lambda item: item.path) -> list[T]:
    for position, item in enumerate(entries):
        if same_file_to_windows(str(path_of(item)), last_video):
            return entries[position:] + entries[:position]
    return entries


def write_playlist_file(path: Path, video_paths: list[str], *,
                        metadata_root: Path | None = None) -> None:
    write_playlist(path, scripted_items(video_paths, metadata_root))


def scripted_items(video_paths: Iterable[str],
                   metadata_root: Path | None = None) -> list[PlaylistItem]:
    scripts = FolderListings()
    return [scripted_item(video_path, metadata_root, listings=scripts) for video_path in video_paths]


def scripted_item(video_path: str, metadata_root: Path | None = None, *,
                  listings: FolderListings | None = None) -> PlaylistItem:
    funscript = matching_funscript(video_path, metadata_root, listings=listings)
    return PlaylistItem(Path(video_path), Path(funscript) if funscript else None)



from __future__ import annotations

import os
import random
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from app_support import funscript
from app_support.media_renditions import renditions
from app_support.mirrored_tree import LIBRARY_DIR_NAME
from app_support.watch_weights import dealt_by_weight, newest_of_each_group
from funestra.flick_folder import SUPPORTED_VIDEO_EXTS
from funestra.funscript import load as load_funscript
from funestra.playlist import PlaylistItem, write_playlist
from funestra.video_groups import (
    GroupIndex,
    normalize_path_key,
)

from .folder_listings import FolderListings
from .media_metadata import (
    build_group_index,
    is_favorite,
    path_matches_query,
    recorded_duration_seconds,
    recorded_for,
)
from .mode_plan import EnhancementLevel
from .vr_videos import keep_shapes

PLAYLIST_PORTRAIT = "portrait_playlist"
PLAYLIST_LANDSCAPE = "landscape_playlist"
PLAYLIST_MAIN_FUNESTRA = "main_player_playlist"


def is_supported_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_VIDEO_EXTS


def has_an_upscale(media_root: str | Path | None) -> Callable[[str], bool]:
    return lambda video_path: bool(renditions(video_path, media_root))


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


@dataclass(frozen=True)
class _ScriptTrees:
    script: Path
    scripts_tree: Path
    marks_tree: Path


def _script_trees_of(video_path: str, metadata_root: Path | None) -> _ScriptTrees | None:
    video = Path(video_path)
    library = _library_folder_of(video)
    if library is None:
        return None
    trees_beside = metadata_root.parent if metadata_root is not None else library.parent
    scripts_tree = funscript.scripts_tree_in(trees_beside)
    script = funscript.script_of(video, library=library, scripts_tree=scripts_tree)
    if script is None:
        return None
    return _ScriptTrees(script, scripts_tree, funscript.generated_marks_tree_in(trees_beside))


def build_mirrored_funscript_path(video_path: str, metadata_root: Path | None = None) -> str:
    trees = _script_trees_of(video_path, metadata_root)
    return "" if trees is None else str(trees.script)


def build_generated_marker_path(video_path: str, metadata_root: Path | None = None) -> str:
    trees = _script_trees_of(video_path, metadata_root)
    mark = None if trees is None else funscript.generated_mark_of(
        trees.script, scripts_tree=trees.scripts_tree, marks_tree=trees.marks_tree)
    return "" if mark is None else str(mark)


def matching_funscript(video_path: str, metadata_root: Path | None = None) -> str | None:
    mirrored = build_mirrored_funscript_path(video_path, metadata_root)
    return mirrored if mirrored and Path(mirrored).exists() else None


def has_matching_funscript(video_path: str, metadata_root: Path | None = None) -> bool:
    return matching_funscript(video_path, metadata_root) is not None


def has_handcrafted_funscript(video_path: str, metadata_root: Path | None = None) -> bool:
    return (has_matching_funscript(video_path, metadata_root)
            and not _is_marked_out_of_f_mode_alone(video_path, metadata_root))


def _is_marked_out_of_f_mode_alone(video_path: str, metadata_root: Path | None) -> bool:
    marker = build_generated_marker_path(video_path, metadata_root)
    return bool(marker and Path(marker).exists())


LEAST_SCRIPTED_SHARE = 0.30


def plays_in_f_mode(video_path: str, metadata_root: Path | None = None, *,
                    outlying_dirs: Iterable[Path] = ()) -> bool:
    if not has_handcrafted_funscript(video_path, metadata_root):
        return False
    duration_seconds = recorded_duration_seconds(
        recorded_for(video_path, metadata_root, outlying_dirs=outlying_dirs))
    if duration_seconds is None:
        return True
    duration_ms = round(duration_seconds * 1000)
    script = load_funscript(Path(build_mirrored_funscript_path(video_path, metadata_root)))
    return script.active_ms(duration_ms) >= LEAST_SCRIPTED_SHARE * duration_ms


def keep_favorites[P: (str, Path)](paths: Iterable[P], metadata_root: Path | None) -> list[P]:
    return [path for path in paths if is_favorite(path, metadata_root)]


def shuffle_paths(paths: list[str], *, rng: random.Random | None = None) -> list[str]:
    result = list(paths)
    if len(result) <= 1:
        return result
    randomizer = rng or random.Random()
    randomizer.shuffle(result)
    return result


def sort_paths_by_recency(
    paths: list[str], listings: FolderListings | None = None
) -> list[str]:
    dates = listings or FolderListings()
    return sorted(paths, key=dates.modified, reverse=True)


def order_paths(paths: list[str], *, recent: bool, rng: random.Random | None = None) -> list[str]:
    if recent:
        return sort_paths_by_recency(paths)
    return shuffle_paths(paths, rng=rng)


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


def _group_of(index: GroupIndex, by_seed_group: bool) -> Callable[[str], str | None]:
    if by_seed_group:
        def the_videos_only_a_seed_tells_apart(path: str) -> str | None:
            seed_key = index.entry(path).seed_key
            return seed_key[0] if seed_key is not None else None

        return the_videos_only_a_seed_tells_apart

    def the_videos_only_an_action_tells_apart(path: str) -> str | None:
        return index.entry(path).action_key

    return the_videos_only_an_action_tells_apart


def _collapse_and_weigh(
    paths: list[str],
    metadata_root: Path | None,
    rng: random.Random | None,
    *,
    by_seed_group: bool = False,
) -> list[str]:
    index = build_group_index(paths, metadata_root)
    return dealt_by_weight(paths, weight_of=index.weight_of,
                           group_of=_group_of(index, by_seed_group), rng=rng or random.Random())


def _collapse_by_arrival_alone(
    paths: list[str],
    metadata_root: Path | None,
    *,
    by_seed_group: bool = False,
) -> list[str]:
    dates = FolderListings()
    ordered = sort_paths_by_recency(paths, dates)
    index = build_group_index(ordered, metadata_root)
    return newest_of_each_group(
        ordered, _group_of(index, by_seed_group),
        newest=lambda group: max(sorted(group), key=dates.modified))


def satellite_narrowing(
    files: list[str],
    favorites_filter: bool,
    *,
    filter_query: str = "",
    metadata_root: Path | None = None,
) -> list[str]:
    if favorites_filter:
        files = keep_favorites(files, metadata_root)
    if filter_query and metadata_root is not None:
        files = [path for path in files if path_matches_query(path, metadata_root, filter_query)]
    return files


@dataclass(frozen=True)
class SatelliteBuild:
    sources: str
    favorites_filter: bool = False
    recent: bool = False
    filter_query: str = ""
    enhancement: EnhancementLevel = EnhancementLevel.BOTH
    media_root: Path | None = None


def build_satellite_playlist_paths(
    build: SatelliteBuild,
    *,
    rng: random.Random | None = None,
    metadata_root: Path | None = None,
) -> list[str]:
    files = satellite_narrowing(collect_video_files(build.sources), build.favorites_filter,
                                filter_query=build.filter_query, metadata_root=metadata_root)
    files = build.enhancement.keep(files, has_an_upscale(build.media_root))
    the_filter_already_pinned_the_act = bool(build.filter_query) and metadata_root is not None
    if metadata_root is None:
        return order_paths(files, recent=build.recent, rng=rng)
    if build.recent:
        return _collapse_by_arrival_alone(files, metadata_root, by_seed_group=the_filter_already_pinned_the_act)
    return _collapse_and_weigh(files, metadata_root, rng, by_seed_group=the_filter_already_pinned_the_act)


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
    write_playlist(path, [scripted_item(video_path, metadata_root) for video_path in video_paths])


def scripted_item(video_path: str, metadata_root: Path | None = None) -> PlaylistItem:
    funscript = matching_funscript(video_path, metadata_root)
    return PlaylistItem(Path(video_path), Path(funscript) if funscript else None)


def build_one_satellite_playlist(
    build: SatelliteBuild,
    playlist_file: Path,
    *,
    rng: random.Random | None = None,
    metadata_root: Path | None = None,
) -> None:
    paths = build_satellite_playlist_paths(build, rng=rng, metadata_root=metadata_root)
    write_playlist_file(playlist_file, paths, metadata_root=metadata_root)


def build_satellite_playlists(
    *,
    portrait: SatelliteBuild,
    landscape: SatelliteBuild,
    state_dir: Path,
    rng: random.Random | None = None,
    metadata_root: Path | None = None,
) -> None:
    for satellite, name in ((portrait, PLAYLIST_PORTRAIT), (landscape, PLAYLIST_LANDSCAPE)):
        build_one_satellite_playlist(
            satellite, build_playlist_file_path(state_dir, name),
            rng=rng, metadata_root=metadata_root,
        )

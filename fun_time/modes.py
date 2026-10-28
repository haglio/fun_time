from __future__ import annotations

import os
import random
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from app_support.mirrored_tree import LIBRARY_DIR_NAME, mirrored_path
from funestra.flick_folder import SUPPORTED_VIDEO_EXTS
from funestra.funscript import load as load_funscript
from funestra.playlist import PlaylistItem, write_playlist

from .folder_listings import FolderListings
from .media_metadata import (
    GroupIndex,
    build_group_index,
    normalize_path_key,
    path_matches_query,
    recorded_duration_seconds,
    recorded_for,
)
from .vr_videos import keep_shapes
from .watch_stats import passes_inclusion, weighted_shuffle

PLAYLIST_PORTRAIT = "portrait_playlist"
PLAYLIST_LANDSCAPE = "landscape_playlist"
PLAYLIST_MAIN_FUNESTRA = "main_player_playlist"


def is_supported_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_VIDEO_EXTS


def source_roots(source_spec: str) -> list[Path]:
    """The dirs (and single files) a pipe-joined source spec names, in order.

    The one reader of the spec's shape, so a caller that needs only to know
    WHERE a library is does not have to walk every file in it to find out.
    """
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
    """Every video below *root*, off the folder listings alone -- see
    :mod:`fun_time.folder_listings` for why no video is asked about itself."""
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


SCRIPTS_TREE = Path("scripts", "scripts")
GENERATED_TREE = Path("scripts", "generated")


def _library_folder_of(video: Path) -> Path | None:
    return next((folder for folder in reversed(video.parents)
                 if folder.name.lower() == folder.parent.name.lower() == LIBRARY_DIR_NAME), None)


def _mirrored_path(video_path: str, tree: Path, suffix: str, metadata_root: Path | None) -> str:
    video = Path(video_path)
    library = _library_folder_of(video)
    if library is None:
        return ""
    trees_beside = metadata_root.parent if metadata_root is not None else library.parent
    return str(mirrored_path(video, roots=(library,), mirror_root=trees_beside / tree, suffix=suffix))


def build_mirrored_funscript_path(video_path: str, metadata_root: Path | None = None) -> str:
    return _mirrored_path(video_path, SCRIPTS_TREE, ".funscript", metadata_root)


def build_generated_marker_path(video_path: str, metadata_root: Path | None = None) -> str:
    return _mirrored_path(video_path, GENERATED_TREE, ".generated", metadata_root)


def matching_funscript(video_path: str, metadata_root: Path | None = None) -> str | None:
    """The funscript mirrored beside *video_path*, or None when there is none."""
    mirrored = build_mirrored_funscript_path(video_path, metadata_root)
    return mirrored if mirrored and Path(mirrored).exists() else None


def has_matching_funscript(video_path: str, metadata_root: Path | None = None) -> bool:
    return matching_funscript(video_path, metadata_root) is not None


def has_handcrafted_funscript(video_path: str, metadata_root: Path | None = None) -> bool:
    """Whether a person wrote *video_path*'s funscript."""
    if not has_matching_funscript(video_path, metadata_root):
        return False
    marker = build_generated_marker_path(video_path, metadata_root)
    return not (marker and Path(marker).exists())


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


def read_favs_content(favs_file: Path) -> str:
    try:
        if not favs_file.exists():
            return ""
        return favs_file.read_text(encoding="utf-8")
    except OSError:
        return ""


def is_favorite_path(video_path: str, favs_content: str) -> bool:
    if not video_path or not favs_content:
        return False
    return video_path in favs_content


def keep_favorites[P: (str, Path)](paths: Iterable[P], favs_file: Path) -> list[P]:
    favs_content = read_favs_content(favs_file)
    return [path for path in paths if is_favorite_path(str(path), favs_content)]


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
    """Order paths most-recently-modified first; unlisted files sort last."""
    dates = listings or FolderListings()
    return sorted(paths, key=dates.modified, reverse=True)


def order_paths(paths: list[str], *, recent: bool, rng: random.Random | None = None) -> list[str]:
    """Order a playlist: newest-first when ``recent`` else shuffled."""
    if recent:
        return sort_paths_by_recency(paths)
    return shuffle_paths(paths, rng=rng)


@dataclass(frozen=True)
class VideoShapes:
    """Which shapes of video the Main Funestra's browse may reach, and where the
    VR masters live.  No VR dirs is every session outside the headset, which
    narrows nothing whatever shapes the headset's flags were left on.
    """

    vr_dirs: str = ""
    plays_vr: bool = True
    plays_flat: bool = True

    @property
    def offered(self) -> bool:
        """Whether this session has both shapes to choose between — which is what
        puts the pair of buttons on the console."""
        return bool(source_roots(self.vr_dirs))

    @property
    def narrows(self) -> bool:
        return self.offered and not (self.plays_vr and self.plays_flat)

    def keep(self, paths: list[str]) -> list[str]:
        if not self.narrows:
            return list(paths)
        return keep_shapes(paths, vr_dirs=source_roots(self.vr_dirs),
                           plays_vr=self.plays_vr, plays_flat=self.plays_flat)


def _collapse_axis(
    index: GroupIndex,
    by_seed_family: bool,
) -> tuple[Callable[[str], str | None], Callable[[str], list[str]]]:
    """The (group-of-path, items-of-group) accessors for a collapse axis.

    Unfiltered browsing collapses **action groups** — one clip per subject — so
    the playlist shows variety and "cycle action" explores a subject's other
    acts.  A filtered view has already pinned the act, so it collapses **seed
    families** instead — one clip per parameter set — and the
    same-params-different-seed siblings are reached through "cycle seed" rather than
    repeating back-to-back in the playlist.
    """
    if by_seed_family:
        def seed_family_of(path: str) -> str | None:
            seed_key = index.entry(path).seed_key
            return seed_key[0] if seed_key is not None else None

        return seed_family_of, lambda family: index.seed_items[family]
    return (
        lambda path: index.entry(path).action_key,
        lambda key: index.action_items[key],
    )


def _collapse_groups(
    paths: list[str],
    group_key_of: Callable[[str], str | None],
    items_of: Callable[[str], list[str]],
    pick: Callable[[list[str]], str],
) -> list[str]:
    """One slot per group, in first-seen order; *pick* chooses each group's
    representative from its items.  Ungrouped paths pass through."""
    slots: list[str] = []
    seen_groups: set[str] = set()
    for path in paths:
        group_key = group_key_of(path)
        if group_key is None:
            slots.append(path)
            continue
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        slots.append(pick(items_of(group_key)))
    return slots


def _collapse_and_weigh(
    paths: list[str],
    metadata_root: Path | None,
    rng: random.Random | None,
    *,
    by_seed_family: bool = False,
) -> list[str]:
    """Shuffle *paths* by the weights stamped on their sidecars, one slot per group.

    Chronically-skipped videos sit the build out proportionally to their
    weight; each group contributes a single item, drawn weighted so preferred
    clips surface more.  The final order is a weighted shuffle: loved videos
    land early, and with nothing stamped every step degenerates to a uniform
    shuffle.  See :func:`_collapse_axis` for which axis groups.
    """
    randomizer = rng or random.Random()
    index = build_group_index(paths, metadata_root)
    survivors = [path for path in paths if passes_inclusion(index.weight_of(path), randomizer)]
    surviving = {normalize_path_key(path) for path in survivors}
    group_key_of, items_of = _collapse_axis(index, by_seed_family)

    def pick(items: list[str]) -> str:
        candidates = [item for item in items if normalize_path_key(item) in surviving]
        weights = [index.weight_of(item) for item in candidates]
        return randomizer.choices(candidates, weights=weights, k=1)[0]

    collapsed = _collapse_groups(survivors, group_key_of, items_of, pick)
    return weighted_shuffle(collapsed, index.weight_of, randomizer)


def _collapse_recent(
    paths: list[str],
    metadata_root: Path | None,
    *,
    by_seed_family: bool = False,
) -> list[str]:
    """Newest-first, one slot per group — the Latest review order.

    New arrivals stay the focus: each group is represented by its most recent
    item and sits at that item's position, so the freshest clip of a group
    surfaces once, near the top.  Watch weighting is deliberately not applied —
    a chronically-skipped clip still appears; recency alone ranks.
    """
    dates = FolderListings()
    ordered = sort_paths_by_recency(paths, dates)
    index = build_group_index(ordered, metadata_root)
    group_key_of, items_of = _collapse_axis(index, by_seed_family)
    return _collapse_groups(
        ordered, group_key_of, items_of, lambda items: max(items, key=dates.modified))


def satellite_narrowing(
    files: list[str],
    favorites_filter: bool,
    favs_file: Path,
    *,
    filter_query: str = "",
    metadata_root: Path | None = None,
) -> list[str]:
    if favorites_filter:
        files = keep_favorites(files, favs_file)
    # An act filter narrows to videos whose recorded act matches; it needs the
    # metadata root to reach each sidecar, so without it the filter is a no-op.
    if filter_query and metadata_root is not None:
        files = [path for path in files if path_matches_query(path, metadata_root, filter_query)]
    return files


def build_satellite_playlist_paths(
    source_spec: str,
    favorites_filter: bool,
    favs_file: Path,
    *,
    filter_query: str = "",
    recent: bool = False,
    rng: random.Random | None = None,
    metadata_root: Path | None = None,
) -> list[str]:
    files = satellite_narrowing(collect_video_files(source_spec), favorites_filter, favs_file,
                                filter_query=filter_query, metadata_root=metadata_root)
    filtered = bool(filter_query) and metadata_root is not None
    # With a metadata root, both orders collapse to one slot per group: Latest
    # (recent) keeps newest-first, the shuffle build weighted-randomizes.  A
    # filtered view collapses seed families (one per param-set) rather than
    # action groups.  Without one there is nothing to group by, so just order
    # the raw files.
    if metadata_root is None:
        return order_paths(files, recent=recent, rng=rng)
    if recent:
        return _collapse_recent(files, metadata_root, by_seed_family=filtered)
    return _collapse_and_weigh(files, metadata_root, rng, by_seed_family=filtered)


def build_playlist_file_path(state_dir: Path, name: str) -> Path:
    return state_dir / f"{name}.tsv"


def rotated_onto[T](entries: list[T], last_video: str, *,
                    path_of: Callable[[T], object] = lambda item: item.path) -> list[T]:
    """*entries* rotated so *last_video* leads them.

    Unchanged when that video is not among them — it was deleted since, or the
    Funestra published no status at all; the queue comes back from its top rather
    than being thrown away.  A Funestra handed a list it is not playing opens at
    the top, which is what makes this a way back to the clip it left.
    """
    key = normalize_path_key(last_video)
    for position, item in enumerate(entries):
        if normalize_path_key(str(path_of(item))) == key:
            return entries[position:] + entries[:position]
    return entries


def write_playlist_file(path: Path, video_paths: list[str], *,
                        metadata_root: Path | None = None) -> None:
    write_playlist(path, [scripted_item(video_path, metadata_root) for video_path in video_paths])


def scripted_item(video_path: str, metadata_root: Path | None = None) -> PlaylistItem:
    """*video_path* paired with the funscript mirrored beside it, when it has one."""
    funscript = matching_funscript(video_path, metadata_root)
    return PlaylistItem(Path(video_path), Path(funscript) if funscript else None)


def build_one_satellite_playlist(
    *,
    sources: str,
    name: str,
    favs_file: Path,
    state_dir: Path,
    favorites_filter: bool,
    recent: bool,
    filter_query: str = "",
    rng: random.Random | None = None,
    metadata_root: Path | None = None,
) -> None:
    """Build and write the playlist file a single satellite plays from."""
    paths = build_satellite_playlist_paths(
        sources, favorites_filter, favs_file, filter_query=filter_query, recent=recent, rng=rng,
        metadata_root=metadata_root,
    )
    write_playlist_file(build_playlist_file_path(state_dir, name), paths,
                        metadata_root=metadata_root)


@dataclass(frozen=True)
class SatelliteBuild:
    """One satellite's playlist-build inputs: what it browses, and how it is
    narrowed — its own filter, F-mode and ordering, since every one of the
    three is a sided command and the two satellites can be in different states."""

    sources: str
    favorites_filter: bool = False
    recent: bool = False
    filter_query: str = ""


def build_satellite_playlists(
    *,
    portrait: SatelliteBuild,
    landscape: SatelliteBuild,
    favs_file: Path,
    state_dir: Path,
    rng: random.Random | None = None,
    metadata_root: Path | None = None,
) -> None:
    """Build and write both satellite playlists, each honoring its own
    :class:`SatelliteBuild` (action-group collapse and watch weighting apply
    when *metadata_root* is given)."""
    build_one_satellite_playlist(
        sources=portrait.sources, name=PLAYLIST_PORTRAIT, favs_file=favs_file,
        state_dir=state_dir, favorites_filter=portrait.favorites_filter, recent=portrait.recent,
        filter_query=portrait.filter_query, rng=rng, metadata_root=metadata_root,
    )
    build_one_satellite_playlist(
        sources=landscape.sources, name=PLAYLIST_LANDSCAPE, favs_file=favs_file,
        state_dir=state_dir, favorites_filter=landscape.favorites_filter, recent=landscape.recent,
        filter_query=landscape.filter_query, rng=rng, metadata_root=metadata_root,
    )

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .media_metadata import GroupIndex, build_group_index, normalize_path_key, path_matches_query
from .watch_stats import load_watch_stats, passes_inclusion, weight_for, weighted_shuffle

PLAYLIST_PORTRAIT = "portrait_playlist"
PLAYLIST_LANDSCAPE = "landscape_playlist"
PLAYLIST_NAU = "nau_playlist"


@dataclass(frozen=True)
class FModePlaylistPlan:
    success: bool
    primary_count: int
    portrait_count: int
    landscape_count: int
    portrait_playlist_path: Path
    landscape_playlist_path: Path
    nau_playlist_path: Path


@dataclass(frozen=True)
class SatellitePlaylistPlan:
    portrait_count: int
    landscape_count: int
    portrait_playlist_path: Path
    landscape_playlist_path: Path


@dataclass(frozen=True)
class SatelliteLibraryContext:
    """What a satellite build needs beyond its source dirs: the metadata
    root for action-group collapsing and the watch-stats file for
    frequency weighting.  Any None simply disables that refinement."""

    metadata_root: Path | None
    watch_stats_file: Path | None


def is_supported_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def collect_video_files(source_spec: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for source_part in source_spec.split("|"):
        root = source_part.strip()
        if not root:
            continue
        root_path = Path(root)
        if root_path.is_dir():
            for candidate in root_path.rglob("*"):
                if not candidate.is_file() or not is_supported_video_path(str(candidate)):
                    continue
                key = normalize_path_key(str(candidate))
                if key in seen:
                    continue
                seen.add(key)
                files.append(str(candidate))
            continue
        if root_path.is_file() and is_supported_video_path(str(root_path)):
            key = normalize_path_key(str(root_path))
            if key not in seen:
                seen.add(key)
                files.append(str(root_path))
    return files


def build_mirrored_funscript_path(video_path: str) -> str:
    normalized = str(Path(video_path))
    marker = "\\videos\\videos\\"
    if marker not in normalized:
        return ""
    mirrored = normalized.replace(marker, "\\videos\\scripts\\scripts\\", 1)
    return str(Path(mirrored).with_suffix(".funscript"))


def has_matching_funscript(video_path: str) -> bool:
    mirrored = build_mirrored_funscript_path(video_path)
    return bool(mirrored) and Path(mirrored).exists()


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


def shuffle_paths(paths: list[str], *, rng: random.Random | None = None) -> list[str]:
    result = list(paths)
    if len(result) <= 1:
        return result
    randomizer = rng or random.Random()
    randomizer.shuffle(result)
    return result


def _path_mtime(path: str) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def sort_paths_by_recency(paths: list[str]) -> list[str]:
    """Order paths most-recently-modified first; unreadable files sort last."""
    return sorted(paths, key=_path_mtime, reverse=True)


def order_paths(paths: list[str], *, recent: bool, rng: random.Random | None = None) -> list[str]:
    """Order a playlist: newest-first when ``recent`` else shuffled."""
    if recent:
        return sort_paths_by_recency(paths)
    return shuffle_paths(paths, rng=rng)


def build_primary_playlist_paths(primary_sources: str, f_mode: bool, *, rng: random.Random | None = None) -> list[str]:
    files = collect_video_files(primary_sources)
    if not f_mode:
        return shuffle_paths(files, rng=rng)
    filtered = [full_path for full_path in files if has_matching_funscript(full_path)]
    return shuffle_paths(filtered, rng=rng)


def _collapse_axis(
    index: GroupIndex,
    by_seed_family: bool,
) -> tuple[Callable[[str], str | None], Callable[[str], list[str]]]:
    """The (group-of-path, members-of-group) accessors for a collapse axis.

    Unfiltered browsing collapses **action groups** — one clip per subject — so
    the playlist shows variety and "cycle action" explores a subject's other
    acts.  A filtered view has already pinned the act, so it collapses **seed
    families** instead — one clip per parameter set — and the
    same-params-different-seed siblings hide behind "cycle seed" rather than
    repeating back-to-back in the playlist.
    """
    if by_seed_family:
        def seed_family_of(path: str) -> str | None:
            entry = index.seed_key_by_path.get(normalize_path_key(path))
            return entry[0] if entry is not None else None

        return seed_family_of, lambda family: index.seed_members[family]
    return (
        lambda path: index.action_key_by_path.get(normalize_path_key(path)),
        lambda key: index.action_members[key],
    )


def _collapse_groups(
    paths: list[str],
    group_key_of: Callable[[str], str | None],
    members_of: Callable[[str], list[str]],
    pick: Callable[[list[str]], str],
) -> list[str]:
    """One slot per group, in first-seen order; *pick* chooses each group's
    representative from its members.  Ungrouped paths pass through."""
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
        slots.append(pick(members_of(group_key)))
    return slots


def _collapse_and_weigh(
    paths: list[str],
    library: SatelliteLibraryContext,
    rng: random.Random | None,
    *,
    by_seed_family: bool = False,
) -> list[str]:
    """Shuffle *paths* with watch-stats weighting, one slot per group.

    Chronically-skipped videos sit the build out proportionally to their
    weight; each group contributes a single member, drawn weighted so preferred
    clips surface more.  The final order is a weighted shuffle: loved videos
    land early, and with no stats on record every step degenerates to today's
    uniform shuffle.  See :func:`_collapse_axis` for which axis groups.
    """
    randomizer = rng or random.Random()
    stats = (
        load_watch_stats(library.watch_stats_file)
        if library.watch_stats_file is not None
        else {}
    )

    def weight(path: str) -> float:
        return weight_for(stats.get(normalize_path_key(path)))

    survivors = [path for path in paths if passes_inclusion(weight(path), randomizer)]
    index = build_group_index(survivors, library.metadata_root)
    group_key_of, members_of = _collapse_axis(index, by_seed_family)

    def pick(members: list[str]) -> str:
        return randomizer.choices(members, weights=[weight(m) for m in members], k=1)[0]

    collapsed = _collapse_groups(survivors, group_key_of, members_of, pick)
    return weighted_shuffle(collapsed, weight, randomizer)


def _collapse_recent(
    paths: list[str],
    library: SatelliteLibraryContext,
    *,
    by_seed_family: bool = False,
) -> list[str]:
    """Newest-first, one slot per group — the premiere review order.

    New arrivals stay the focus: each group is represented by its most recent
    member and sits at that member's position, so the freshest clip of a group
    surfaces once, near the top.  Watch weighting is deliberately not applied —
    a chronically-skipped clip still appears; recency alone ranks.
    """
    ordered = sort_paths_by_recency(paths)
    index = build_group_index(ordered, library.metadata_root)
    group_key_of, members_of = _collapse_axis(index, by_seed_family)
    return _collapse_groups(ordered, group_key_of, members_of, lambda members: max(members, key=_path_mtime))


def build_satellite_playlist_paths(
    source_spec: str,
    f_mode: bool,
    favs_file: Path,
    *,
    filter_query: str = "",
    recent: bool = False,
    rng: random.Random | None = None,
    library: SatelliteLibraryContext | None = None,
) -> list[str]:
    files = collect_video_files(source_spec)
    if f_mode:
        favs_content = read_favs_content(favs_file)
        files = [full_path for full_path in files if is_favorite_path(full_path, favs_content)]
    # An attribute filter narrows to videos whose metadata matches; it needs the
    # metadata root to reach each sidecar, so without it the filter is a no-op.
    # Applied before ordering, so it holds under both premiere and shuffle.
    filtered = bool(filter_query) and (
        library is not None and library.metadata_root is not None
    )
    if filtered:
        files = [
            full_path
            for full_path in files
            if path_matches_query(full_path, library.metadata_root, filter_query)
        ]
    # With a library, both orders collapse to one slot per group: premiere
    # (recent) keeps newest-first, the shuffle build weighted-randomizes.  A
    # filtered view collapses seed families (one per param-set) rather than
    # action groups.  With no library there is nothing to group by, so just
    # order the raw files.
    if library is None:
        return order_paths(files, recent=recent, rng=rng)
    if recent:
        return _collapse_recent(files, library, by_seed_family=filtered)
    return _collapse_and_weigh(files, library, rng, by_seed_family=filtered)


def build_playlist_file_path(state_dir: Path, name: str) -> Path:
    return state_dir / f"{name}.tsv"


def write_playlist_file(path: Path, paths: list[str]) -> None:
    """Write a satellite playlist: one video path per line.

    The native satellite player (:mod:`satellite`) reads this with
    ``nau.playlist.read_playlist`` — one path per line, an optional TAB-separated
    funscript column it ignores for a silent satellite — so the file is plain
    lines, with no header of any kind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{full_path}\n" for full_path in paths)
    path.write_text(content, encoding="utf-8")


def write_nau_playlist_file(path: Path, video_paths: list[str]) -> None:
    """Write Nau's playlist: one video per line, TAB + funscript when it exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for video_path in video_paths:
        mirrored = build_mirrored_funscript_path(video_path)
        if mirrored and Path(mirrored).exists():
            lines.append(f"{video_path}\t{mirrored}")
        else:
            lines.append(video_path)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def build_one_satellite_playlist(
    *,
    sources: str,
    name: str,
    favs_file: Path,
    state_dir: Path,
    f_mode: bool,
    recent: bool,
    filter_query: str = "",
    rng: random.Random | None = None,
    library: SatelliteLibraryContext | None = None,
) -> tuple[Path, int]:
    """Build, write, and return the playlist file for a single satellite."""
    paths = build_satellite_playlist_paths(
        sources, f_mode, favs_file, filter_query=filter_query, recent=recent, rng=rng, library=library
    )
    playlist_path = build_playlist_file_path(state_dir, name)
    write_playlist_file(playlist_path, paths)
    return playlist_path, len(paths)


def build_satellite_playlists(
    *,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: Path,
    state_dir: Path,
    f_mode: bool,
    recent: bool,
    portrait_filter: str = "",
    landscape_filter: str = "",
    rng: random.Random | None = None,
    library: SatelliteLibraryContext | None = None,
) -> SatellitePlaylistPlan:
    """Build and write the Portrait/Landscape satellite playlists (the two satellites).

    Ordering follows ``recent``: newest-first when set, otherwise shuffled
    (with action-group collapse and watch weighting when *library* is given).
    Each satellite honours its own ``*_filter`` independently.
    """
    portrait_playlist_path, portrait_count = build_one_satellite_playlist(
        sources=portrait_sources, name=PLAYLIST_PORTRAIT, favs_file=favs_file,
        state_dir=state_dir, f_mode=f_mode, recent=recent,
        filter_query=portrait_filter, rng=rng, library=library,
    )
    landscape_playlist_path, landscape_count = build_one_satellite_playlist(
        sources=landscape_sources, name=PLAYLIST_LANDSCAPE, favs_file=favs_file,
        state_dir=state_dir, f_mode=f_mode, recent=recent,
        filter_query=landscape_filter, rng=rng, library=library,
    )
    return SatellitePlaylistPlan(
        portrait_count=portrait_count,
        landscape_count=landscape_count,
        portrait_playlist_path=portrait_playlist_path,
        landscape_playlist_path=landscape_playlist_path,
    )


def build_fmode_playlists(
    *,
    primary_sources: str,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: Path,
    state_dir: Path,
    enabled: bool,
    recent: bool = False,
    portrait_filter: str = "",
    landscape_filter: str = "",
    rng: random.Random | None = None,
    library: SatelliteLibraryContext | None = None,
) -> FModePlaylistPlan:
    primary_paths = build_primary_playlist_paths(primary_sources, enabled, rng=rng)
    satellites = build_satellite_playlists(
        portrait_sources=portrait_sources,
        landscape_sources=landscape_sources,
        favs_file=favs_file,
        state_dir=state_dir,
        f_mode=enabled,
        recent=recent,
        portrait_filter=portrait_filter,
        landscape_filter=landscape_filter,
        rng=rng,
        library=library,
    )

    nau_playlist_path = state_dir / f"{PLAYLIST_NAU}.tsv"
    write_nau_playlist_file(nau_playlist_path, primary_paths)
    return FModePlaylistPlan(
        success=True,
        primary_count=len(primary_paths),
        portrait_count=satellites.portrait_count,
        landscape_count=satellites.landscape_count,
        portrait_playlist_path=satellites.portrait_playlist_path,
        landscape_playlist_path=satellites.landscape_playlist_path,
        nau_playlist_path=nau_playlist_path,
    )

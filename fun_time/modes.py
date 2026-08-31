from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .media_metadata import GroupIndex, build_group_index, normalize_path_key, path_matches_query
from .watch_stats import load_watch_stats, passes_inclusion, weight_for, weighted_shuffle

PLAYLIST_PORTRAIT = "portrait_playlist"
PLAYLIST_LANDSCAPE = "landscape_playlist"
PLAYLIST_NAU = "nau_playlist"


@dataclass(frozen=True)
class SatelliteLibraryContext:
    """What a satellite build needs beyond its source dirs: the metadata
    root for action-group collapsing and the watch-stats file for
    frequency weighting.  Any None simply disables that refinement."""

    metadata_root: Path | None
    watch_stats_file: Path | None


def is_supported_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def source_roots(source_spec: str) -> list[Path]:
    """The dirs (and single files) a pipe-joined source spec names, in order.

    The one reader of the spec's shape, so a caller that needs only to know
    WHERE a library is does not have to walk every file in it to find out.
    """
    return [Path(part.strip()) for part in source_spec.split("|") if part.strip()]


def collect_video_files(source_spec: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for root_path in source_roots(source_spec):
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


def matching_funscript(video_path: str) -> str | None:
    """The funscript mirrored beside *video_path*, or None when there is none."""
    mirrored = build_mirrored_funscript_path(video_path)
    return mirrored if mirrored and Path(mirrored).exists() else None


def has_matching_funscript(video_path: str) -> bool:
    return matching_funscript(video_path) is not None


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


def build_main_playlist_paths(main_sources: str, f_mode: bool, *,
                              recent: bool = False,
                              rng: random.Random | None = None) -> list[str]:
    """The main player's playlist, in the browse order it is in.

    *recent* is Latest — newest-first — and its absence is Shuffle, the same two
    orders a satellite browses in and the same words on the HUD.  The main player
    had only the shuffle: a video that arrived an hour ago was somewhere in a
    thousand-clip rotation with no way to ask for it, while either satellite could
    be told "latest".
    """
    files = collect_video_files(main_sources)
    if f_mode:
        files = [full_path for full_path in files if has_matching_funscript(full_path)]
    return order_paths(files, recent=recent, rng=rng)


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
    """Newest-first, one slot per group — the Latest review order.

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
    # An act filter narrows to videos whose recorded act matches; it needs the
    # metadata root to reach each sidecar, so without it the filter is a no-op.
    # Applied before ordering, so it holds under both Latest and Shuffle.
    filtered = bool(filter_query) and (
        library is not None and library.metadata_root is not None
    )
    if filtered:
        files = [
            full_path
            for full_path in files
            if path_matches_query(full_path, library.metadata_root, filter_query)
        ]
    # With a library, both orders collapse to one slot per group: Latest
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


def playlist_entry_line(video: str | Path, funscript: str | Path | None) -> str:
    """One playlist entry: the video, TAB + its funscript where there is one.

    Also the shape a ``PLAY_FILE`` argument takes, so naming a video to jump to
    says it exactly as the playlist does — the two cannot drift apart.
    """
    return f"{video}\t{funscript}" if funscript else f"{video}"


def write_playlist_entries(
    path: Path, entries: Sequence[tuple[str | Path, str | Path | None]]
) -> None:
    """Write a playlist file: one video per line, TAB + funscript where there is one.

    The single shape both players read back with ``player_core.playlist.read_playlist``
    — Nau drives the OSR2 from the funscript column, a silent satellite drops it —
    so every playlist fun_time writes is emitted here and can never drift apart.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (playlist_entry_line(video, funscript) for video, funscript in entries)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def write_playlist_file(path: Path, paths: list[str]) -> None:
    """Write a satellite playlist: one video path per line, no funscript column.

    A satellite is silent and unscripted, so it drops that column anyway and
    there is nothing to look up.
    """
    write_playlist_entries(path, [(video_path, None) for video_path in paths])


def write_nau_playlist_file(path: Path, video_paths: list[str]) -> None:
    """Write Nau's playlist, pairing each video with its funscript when it has one."""
    write_playlist_entries(
        path, [(video_path, matching_funscript(video_path)) for video_path in video_paths]
    )


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
) -> None:
    """Build and write the playlist file a single satellite plays from."""
    paths = build_satellite_playlist_paths(
        sources, f_mode, favs_file, filter_query=filter_query, recent=recent, rng=rng, library=library
    )
    write_playlist_file(build_playlist_file_path(state_dir, name), paths)


@dataclass(frozen=True)
class SatelliteBuild:
    """One satellite's playlist-build inputs: what it browses, and how it is
    narrowed — its own filter, F-mode and ordering, since every one of the
    three is a sided command and the two satellites can be in different states."""

    sources: str
    f_mode: bool = False
    recent: bool = False
    filter_query: str = ""


def build_satellite_playlists(
    *,
    portrait: SatelliteBuild,
    landscape: SatelliteBuild,
    favs_file: Path,
    state_dir: Path,
    rng: random.Random | None = None,
    library: SatelliteLibraryContext | None = None,
) -> None:
    """Build and write both satellite playlists, each honoring its own
    :class:`SatelliteBuild` (action-group collapse and watch weighting apply
    when *library* is given)."""
    build_one_satellite_playlist(
        sources=portrait.sources, name=PLAYLIST_PORTRAIT, favs_file=favs_file,
        state_dir=state_dir, f_mode=portrait.f_mode, recent=portrait.recent,
        filter_query=portrait.filter_query, rng=rng, library=library,
    )
    build_one_satellite_playlist(
        sources=landscape.sources, name=PLAYLIST_LANDSCAPE, favs_file=favs_file,
        state_dir=state_dir, f_mode=landscape.f_mode, recent=landscape.recent,
        filter_query=landscape.filter_query, rng=rng, library=library,
    )


def build_main_playlist(playlist_file: Path, main_sources: str, *, f_mode: bool,
                        recent: bool = False) -> None:
    """Build and write the main player's playlist alone.

    The one-player counterpart to :func:`build_all_playlists`, for a startup
    that keeps the satellites' resumed playlists and needs only the main player's
    rebuilt — the satellites' library is the same whichever app is running,
    while the main player's is what the two apps disagree about.

    *f_mode* is the session's, not off: the satellites' playlists came back
    built under it, and one player quietly holding the whole library while the
    HUDs say F-mode is what this rebuild would otherwise leave behind.
    """
    write_nau_playlist_file(
        playlist_file, build_main_playlist_paths(main_sources, f_mode, recent=recent))


def build_all_playlists(
    *,
    main_sources: str,
    portrait: SatelliteBuild,
    landscape: SatelliteBuild,
    favs_file: Path,
    state_dir: Path,
    main_f_mode: bool = False,
    main_recent: bool = False,
    rng: random.Random | None = None,
    library: SatelliteLibraryContext | None = None,
) -> None:
    """Build and write all three playlists — both satellites' and Nau's.

    F-mode is per player, so each build carries its own flag: a session where
    only the landscape satellite is narrowed to favorites builds the other two
    whole.  The one caller that wants all three at once is a fresh start with
    nothing to resume, which is why every flag defaults off.
    """
    build_satellite_playlists(
        portrait=portrait,
        landscape=landscape,
        favs_file=favs_file,
        state_dir=state_dir,
        rng=rng,
        library=library,
    )
    write_nau_playlist_file(
        build_playlist_file_path(state_dir, PLAYLIST_NAU),
        build_main_playlist_paths(main_sources, main_f_mode, recent=main_recent, rng=rng),
    )

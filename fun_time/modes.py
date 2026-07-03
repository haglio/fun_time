from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path


PLAYLIST_PRIMARY = "primary_vlc_playlist"
PLAYLIST_PORTRAIT = "portrait_vlc_playlist"
PLAYLIST_LANDSCAPE = "landscape_vlc_playlist"
PLAYLIST_NAU = "nau_playlist"


@dataclass(frozen=True)
class FModePlaylistPlan:
    success: bool
    primary_count: int
    portrait_count: int
    landscape_count: int
    primary_playlist_path: Path
    portrait_playlist_path: Path
    landscape_playlist_path: Path
    nau_playlist_path: Path


def is_supported_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def normalize_path_key(path: str) -> str:
    return path.strip().lower()


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


def build_primary_playlist_paths(primary_sources: str, f_mode: bool, *, rng: random.Random | None = None) -> list[str]:
    files = collect_video_files(primary_sources)
    if not f_mode:
        return shuffle_paths(files, rng=rng)
    filtered = [full_path for full_path in files if has_matching_funscript(full_path)]
    return shuffle_paths(filtered, rng=rng)


def build_satellite_playlist_paths(source_spec: str, f_mode: bool, favs_file: Path, *, rng: random.Random | None = None) -> list[str]:
    files = collect_video_files(source_spec)
    if not f_mode:
        return shuffle_paths(files, rng=rng)
    favs_content = read_favs_content(favs_file)
    filtered = [full_path for full_path in files if is_favorite_path(full_path, favs_content)]
    return shuffle_paths(filtered, rng=rng)


def build_playlist_file_path(state_dir: Path, name: str) -> Path:
    return state_dir / f"{name}.m3u"


def write_playlist_file(path: Path, paths: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "#EXTM3U\r\n" + "".join(f"{full_path}\r\n" for full_path in paths)
    path.write_text(content, encoding="utf-8", newline="")


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


def build_fmode_playlists(
    *,
    primary_sources: str,
    portrait_sources: str,
    landscape_sources: str,
    favs_file: Path,
    state_dir: Path,
    enabled: bool,
    rng: random.Random | None = None,
) -> FModePlaylistPlan:
    primary_paths = build_primary_playlist_paths(primary_sources, enabled, rng=rng)
    portrait_paths = build_satellite_playlist_paths(portrait_sources, enabled, favs_file, rng=rng)
    landscape_paths = build_satellite_playlist_paths(landscape_sources, enabled, favs_file, rng=rng)

    primary_playlist_path = build_playlist_file_path(state_dir, PLAYLIST_PRIMARY)
    portrait_playlist_path = build_playlist_file_path(state_dir, PLAYLIST_PORTRAIT)
    landscape_playlist_path = build_playlist_file_path(state_dir, PLAYLIST_LANDSCAPE)
    nau_playlist_path = state_dir / f"{PLAYLIST_NAU}.tsv"

    write_playlist_file(primary_playlist_path, primary_paths)
    write_playlist_file(portrait_playlist_path, portrait_paths)
    write_playlist_file(landscape_playlist_path, landscape_paths)
    write_nau_playlist_file(nau_playlist_path, primary_paths)
    return FModePlaylistPlan(
        success=True,
        primary_count=len(primary_paths),
        portrait_count=len(portrait_paths),
        landscape_count=len(landscape_paths),
        primary_playlist_path=primary_playlist_path,
        portrait_playlist_path=portrait_playlist_path,
        landscape_playlist_path=landscape_playlist_path,
        nau_playlist_path=nau_playlist_path,
    )

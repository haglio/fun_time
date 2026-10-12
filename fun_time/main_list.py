"""The Main player's one list: the library's videos its buttons let through, in
the order they ask for, written where the Main Funestra reads it."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

from .media_metadata import normalize_path_key
from .modes import (
    PLAYLIST_MAIN_FUNESTRA,
    VideoShapes,
    build_playlist_file_path,
    collect_video_files,
    has_handcrafted_funscript,
    rotated_onto,
    sort_paths_by_recency,
    write_playlist_file,
)


@dataclass(frozen=True)
class MainListSettings:
    plays_vr: bool = True
    plays_flat: bool = True
    scripted_filter: bool = False
    latest: bool = False
    shuffle_seed: int = 0

    @classmethod
    def of(cls, state) -> MainListSettings:
        return cls(plays_vr=state.main_plays_vr, plays_flat=state.main_plays_flat,
                   scripted_filter=state.main_scripted_filter, latest=state.main_latest,
                   shuffle_seed=state.main_shuffle_seed)


def new_shuffle_seed() -> int:
    return random.getrandbits(32)


def _shuffled(videos: list[str], seed: int) -> list[str]:
    def place(video: str) -> bytes:
        return hashlib.blake2b(f"{seed}:{normalize_path_key(video)}".encode(),
                               digest_size=8).digest()

    return sorted(videos, key=place)


@dataclass(frozen=True)
class MainList:
    sources: str
    state_dir: Path
    vr_dirs: str = ""
    metadata_root: Path | None = None

    @property
    def file(self) -> Path:
        return build_playlist_file_path(Path(self.state_dir), PLAYLIST_MAIN_FUNESTRA)

    def shapes(self, settings: MainListSettings) -> VideoShapes:
        return VideoShapes(vr_dirs=self.vr_dirs, plays_vr=settings.plays_vr,
                           plays_flat=settings.plays_flat)

    def videos(self, settings: MainListSettings, *, start_from: str = "") -> list[str]:
        every = collect_video_files(self.sources)
        let_through = set(self.shapes(settings).keep(every))
        if settings.scripted_filter:
            let_through = {video for video in let_through
                           if has_handcrafted_funscript(video, self.metadata_root)}
        ordered = (sort_paths_by_recency(every) if settings.latest
                   else _shuffled(every, settings.shuffle_seed))
        return [video for video in rotated_onto(ordered, start_from, path_of=str)
                if video in let_through]

    def write(self, settings: MainListSettings, *, start_from: str = "") -> list[str]:
        videos = self.videos(settings, start_from=start_from)
        write_playlist_file(self.file, videos, metadata_root=self.metadata_root)
        return videos

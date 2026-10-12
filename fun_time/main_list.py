"""The Main player's one list: the library's videos its buttons let through, in
the order they ask for, written where the Main Funestra reads it."""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from .modes import (
    PLAYLIST_MAIN_FUNESTRA,
    VideoShapes,
    build_playlist_file_path,
    collect_video_files,
    has_handcrafted_funscript,
    order_paths,
    write_playlist_file,
)


@dataclass(frozen=True)
class MainListSettings:
    plays_vr: bool = True
    plays_flat: bool = True
    scripted_filter: bool = False
    latest: bool = False

    @classmethod
    def of(cls, state) -> MainListSettings:
        return cls(plays_vr=state.main_plays_vr, plays_flat=state.main_plays_flat,
                   scripted_filter=state.main_scripted_filter, latest=state.main_latest)


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

    def videos(self, settings: MainListSettings, *,
               rng: random.Random | None = None) -> list[str]:
        files = self.shapes(settings).keep(collect_video_files(self.sources))
        if settings.scripted_filter:
            files = [video for video in files
                     if has_handcrafted_funscript(video, self.metadata_root)]
        return order_paths(files, recent=settings.latest, rng=rng)

    def write(self, settings: MainListSettings, *,
              rng: random.Random | None = None) -> list[str]:
        videos = self.videos(settings, rng=rng)
        write_playlist_file(self.file, videos, metadata_root=self.metadata_root)
        return videos

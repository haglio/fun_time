"""The Main player's one list: the library's videos its buttons let through, in
the order they ask for, written where the Main Funestra reads it."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, replace
from pathlib import Path

from funestra.funestra_verbs import SET_F_MODE
from funestra.modes import LengthMode

from main_funestra.controls import SET_LENGTH_MODE

from .media_metadata import (
    EXCERPT,
    FULL_LENGTH,
    SHORT,
    load_metadata,
    metadata_path_for,
    normalize_path_key,
    video_type_of,
)
from .modes import (
    PLAYLIST_MAIN_FUNESTRA,
    VideoShapes,
    build_playlist_file_path,
    collect_video_files,
    has_handcrafted_funscript,
    rotated_onto,
    sort_paths_by_recency,
    source_roots,
    write_playlist_file,
)
from .vr_videos import in_a_vr_folder


@dataclass(frozen=True)
class MainListSettings:
    plays_vr: bool = True
    plays_flat: bool = True
    scripted_filter: bool = False
    length: LengthMode = LengthMode.MIXED
    latest: bool = False
    shuffle_seed: int = 0

    @classmethod
    def of(cls, state) -> MainListSettings:
        return cls(plays_vr=state.main_plays_vr, plays_flat=state.main_plays_flat,
                   scripted_filter=state.main_scripted_filter, length=state.main_length_mode,
                   latest=state.main_latest, shuffle_seed=state.main_shuffle_seed)

    def into(self, state):
        return replace(state, main_plays_vr=self.plays_vr, main_plays_flat=self.plays_flat,
                       main_scripted_filter=self.scripted_filter, main_length_mode=self.length,
                       main_latest=self.latest, main_shuffle_seed=self.shuffle_seed)

    @property
    def labels(self) -> list[str]:
        return [f"{SET_F_MODE} {int(self.scripted_filter)}", f"{SET_LENGTH_MODE} {self.length}"]


_KINDS_OF_LENGTH = {
    LengthMode.CLIPS: frozenset({SHORT, EXCERPT}),
    LengthMode.FULL: frozenset({FULL_LENGTH}),
}

_LENGTHS_THE_KEY_WALKS = (LengthMode.MIXED, LengthMode.CLIPS, LengthMode.FULL)


def next_length_mode(length: LengthMode) -> LengthMode:
    if length not in _LENGTHS_THE_KEY_WALKS:
        return LengthMode.MIXED
    position = _LENGTHS_THE_KEY_WALKS.index(length)
    return _LENGTHS_THE_KEY_WALKS[(position + 1) % len(_LENGTHS_THE_KEY_WALKS)]


def new_shuffle_seed() -> int:
    return random.getrandbits(32)


def _shuffled(videos: list[str], seed: int) -> list[str]:
    def place(video: str) -> bytes:
        return hashlib.blake2b(f"{seed}:{normalize_path_key(video)}".encode(),
                               digest_size=8).digest()

    return sorted(videos, key=place)


def _the_video_on_screen_alone(ordered: list[str], on_screen: str) -> list[str]:
    leading = ordered[:1]
    if leading and normalize_path_key(leading[0]) == normalize_path_key(on_screen):
        return leading
    return []


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

    def _kind_of(self, video: str) -> str:
        if self.metadata_root is None or in_a_vr_folder(video, source_roots(self.vr_dirs)):
            return FULL_LENGTH
        record = metadata_path_for(video, self.metadata_root)
        return (video_type_of(load_metadata(record)) if record is not None else "") or FULL_LENGTH

    def _lets_through(self, video: str, length: LengthMode) -> bool:
        if length is LengthMode.MIXED:
            return True
        return self._kind_of(video) in _KINDS_OF_LENGTH.get(length, frozenset())

    def videos(self, settings: MainListSettings, *, start_from: str = "") -> list[str]:
        every = collect_video_files(self.sources)
        let_through = {video for video in self.shapes(settings).keep(every)
                       if self._lets_through(video, settings.length)}
        if settings.scripted_filter:
            let_through = {video for video in let_through
                           if has_handcrafted_funscript(video, self.metadata_root)}
        ordered = rotated_onto(sort_paths_by_recency(every) if settings.latest
                               else _shuffled(every, settings.shuffle_seed),
                               start_from, path_of=str)
        return ([video for video in ordered if video in let_through]
                or _the_video_on_screen_alone(ordered, start_from))

    def write(self, settings: MainListSettings, *, start_from: str = "") -> list[str]:
        videos = self.videos(settings, start_from=start_from)
        write_playlist_file(self.file, videos, metadata_root=self.metadata_root)
        return videos

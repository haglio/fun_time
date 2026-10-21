"""The Main player's one list: the library's videos its buttons let through, in
the order they ask for, written where the Main Funestra reads it."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, replace
from pathlib import Path

from funestra.funestra_verbs import SET_F_MODE
from funestra.modes import LengthMode
from shared_ui.orientation import PORTRAIT

from .media_metadata import (
    EXCERPT,
    FULL_LENGTH,
    SHORT,
    normalize_path_key,
    recorded_for,
    recorded_orientation,
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
    plays_portrait: bool = True
    plays_landscape: bool = True
    scripted_filter: bool = False
    length: LengthMode = LengthMode.MIXED
    latest: bool = False
    shuffle_seed: int = 0

    @classmethod
    def of(cls, state) -> MainListSettings:
        return cls(plays_vr=state.main_plays_vr, plays_flat=state.main_plays_flat,
                   plays_portrait=state.main_plays_portrait,
                   plays_landscape=state.main_plays_landscape,
                   scripted_filter=state.main_scripted_filter, length=state.main_length_mode,
                   latest=state.main_latest, shuffle_seed=state.main_shuffle_seed)

    def into(self, state):
        return replace(state, main_plays_vr=self.plays_vr, main_plays_flat=self.plays_flat,
                       main_plays_portrait=self.plays_portrait,
                       main_plays_landscape=self.plays_landscape,
                       main_scripted_filter=self.scripted_filter, main_length_mode=self.length,
                       main_latest=self.latest, main_shuffle_seed=self.shuffle_seed)

    @property
    def labels(self) -> list[str]:
        return [f"{SET_F_MODE} {int(self.scripted_filter)}"]


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


def _the_one_on_screen(every: list[str], on_screen: str) -> list[str]:
    if not on_screen:
        return []
    key = normalize_path_key(on_screen)
    return [video for video in every if normalize_path_key(video) == key][:1]


@dataclass(frozen=True)
class WrittenList:
    videos: list[str]
    nothing_left: bool


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

    def _lets_through(self, video: str, settings: MainListSettings) -> bool:
        narrows_length = settings.length is not LengthMode.MIXED
        if not narrows_length and settings.plays_portrait and settings.plays_landscape:
            return True
        if in_a_vr_folder(video, source_roots(self.vr_dirs)):
            kind, record = FULL_LENGTH, {}
        else:
            record = recorded_for(video, self.metadata_root)
            kind = video_type_of(record) or FULL_LENGTH
        if narrows_length and kind not in _KINDS_OF_LENGTH.get(settings.length, frozenset()):
            return False
        portrait = recorded_orientation(record) == PORTRAIT
        return settings.plays_portrait if portrait else settings.plays_landscape

    def lets_through(self, videos: list[str], settings: MainListSettings) -> list[str]:
        kept = [video for video in self.shapes(settings).keep(videos)
                if self._lets_through(video, settings)]
        if settings.scripted_filter:
            kept = [video for video in kept if has_handcrafted_funscript(video, self.metadata_root)]
        return kept

    def videos(self, settings: MainListSettings, *, on_screen: str = "",
               from_the_top: bool = False) -> list[str]:
        return self._listed(settings, on_screen, from_the_top)[1]

    def _listed(self, settings: MainListSettings, on_screen: str,
                from_the_top: bool) -> tuple[list[str], list[str]]:
        every = collect_video_files(self.sources)
        let_through = set(self.lets_through(every, settings))
        ordered = rotated_onto(sort_paths_by_recency(every) if settings.latest
                               else _shuffled(every, settings.shuffle_seed),
                               "" if from_the_top else on_screen, path_of=str)
        return every, [video for video in ordered if video in let_through]

    def write(self, settings: MainListSettings, *, on_screen: str = "",
              from_the_top: bool = False) -> WrittenList:
        every, videos = self._listed(settings, on_screen, from_the_top)
        written = (WrittenList(videos, nothing_left=False) if videos
                   else WrittenList(_the_one_on_screen(every, on_screen), nothing_left=True))
        write_playlist_file(self.file, written.videos, metadata_root=self.metadata_root)
        return written

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app_support.video_names import clip_title, stable_title, version_group_of

from fun_time.media_metadata import recorded_for

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _size(video: Path) -> int:
    try:
        return video.stat().st_size
    except OSError:
        return 0


_STOPWORDS = frozenset({"the", "and", "for", "of", "a", "an", "in", "to", "my", "it", "on"})
_LONGEST_FRAGMENT = 2


def _distinctive(source: str, performer: str) -> set[str]:
    perf = _tokens(performer)
    return {t for t in _tokens(source) - perf if len(t) > _LONGEST_FRAGMENT and t not in _STOPWORDS}


def _matches(meta: dict, text: set[str]) -> bool:
    perf = _tokens(str(meta.get("performer", "")))
    if not perf or not perf <= text:
        return False
    distinctive = _distinctive(str(meta.get("source", "")), str(meta.get("performer", "")))
    return bool(distinctive) and bool(distinctive & text)


@dataclass(frozen=True)
class ClipNav:
    _clips: dict[Path, dict]
    _non_clips: tuple[Path, ...]
    _groups: dict[Path, str]
    _titles: dict[Path, str]

    @classmethod
    def build(cls, videos: Iterable[Path], metadata_root: Path | None) -> ClipNav:
        clips: dict[Path, dict] = {}
        non_clips: list[Path] = []
        groups: dict[Path, str] = {}
        titles: dict[Path, str] = {}
        for video in videos:
            payload = recorded_for(video, metadata_root)
            titles[video] = str(payload.get("title", "") or "").strip()
            meta = payload.get("clip")
            if isinstance(meta, dict):
                clips[video] = meta
            else:
                non_clips.append(video)
                groups[video] = version_group_of(payload)
        return cls(clips, tuple(non_clips), groups, titles)

    def _family(self, scene: Path) -> str:
        return self._groups.get(scene) or stable_title(scene.stem)

    def title_of(self, video: Path) -> str:
        meta = self._clips.get(video)
        return (
            self._titles.get(video)
            or (clip_title({"clip": meta}) if meta is not None else "")
            or self._groups.get(video)
            or video.stem
        )

    def compilation_of(self, video: Path) -> str:
        meta = self._clips.get(video)
        return str(meta.get("compilation", "") or "") if meta is not None else ""

    def compilation_playlist(self, video: Path) -> list[Path]:
        comp = self.compilation_of(video)
        if not comp:
            return []
        best: dict[int, Path] = {}
        for sibling, m in self._clips.items():
            if self.compilation_of(sibling) != comp:
                continue
            index = m.get("index", 0)
            current = best.get(index)
            if current is None or _size(sibling) > _size(current):
                best[index] = sibling
        return [best[i] for i in sorted(best)]

    def full_vid_of(self, video: Path) -> Path | None:
        meta = self._clips.get(video)
        if meta is None:
            return None
        family = self._recorded_family(meta)
        recorded = _largest([s for s in self._non_clips if self._family(s) == family])
        if recorded is not None:
            return recorded
        return _resolve(meta, [c for c in self._non_clips if c != video])

    def _recorded_family(self, meta: dict) -> str | None:
        recorded = meta.get("full_video")
        return self._family(Path(str(recorded))) if recorded else None

    def clip_of(self, video: Path) -> Path | None:
        if video in self._clips:
            return None
        family = self._family(video)
        recorded = _largest([
            v for v, m in self._clips.items() if self._recorded_family(m) == family
        ])
        if recorded is not None:
            return recorded
        text = _tokens(video.stem)
        named = _largest([v for v, m in self._clips.items() if v != video and _matches(m, text)])
        if named is not None:
            return named
        candidates = [
            v for v, m in self._clips.items()
            if v != video and could_be_cut_from(m, video)
        ]
        only_clip = _only_candidate(candidates)
        if only_clip is None:
            return None
        performer = _performer_of(self._clips[only_clip])
        scenes = [s for s in self._non_clips if performer <= _tokens(s.stem)]
        return only_clip if _only_candidate(scenes) is not None else None


def could_be_cut_from(meta: dict, scene: Path) -> bool:
    performer = _performer_of(meta)
    return bool(performer) and performer <= _tokens(scene.stem)


def _performer_of(meta: dict) -> set[str]:
    return _tokens(str(meta.get("performer", "")))


def _largest(candidates: list[Path]) -> Path | None:
    return max(candidates, key=_size) if candidates else None


def _only_candidate(candidates: list[Path]) -> Path | None:
    if not candidates:
        return None
    by_title: dict[str, list[Path]] = {}
    for candidate in candidates:
        by_title.setdefault(stable_title(candidate.stem), []).append(candidate)
    if len(by_title) != 1:
        return None
    return _largest(next(iter(by_title.values())))


def _resolve(meta: dict, candidates: list[Path]) -> Path | None:
    named = _largest([c for c in candidates if _matches(meta, _tokens(c.stem))])
    if named is not None:
        return named
    return _only_candidate([c for c in candidates if could_be_cut_from(meta, c)])

"""Clip navigation: the `clip` metadata Evolver records, and the jumps it powers.

A clip carved out of a compilation gets a sidecar ``clip`` object recording its
parent compilation, its running order within it, and the source movie + performer
it was taken from (see Evolver). This module reads that field for the name a
video is shown under, and for the three navigations Fun Time exposes:

* ``compilation`` — the clip's siblings, in original order (for a playlist);
* ``full vid``    — the library video the clip's scene was taken from;
* ``clip jump``  — the reverse, a full scene's clip.

``full vid``/``clip jump`` answer from a ``full_video`` the sidecar records when
one is there: Evolver's clip-match batch found the clip's own frames inside that
scene, so it beats any reading of the names. The answer is held against the scene's
*version family* rather than the one file matched, so it survives whichever
version is on screen and hands back the same best-of-family the playlist shows.
Without a recording they fall back to matching the source/performer against the
filename — deliberately loose (token containment) because a library file names
its scene however the user happened to save it, and often enough carries no movie
title at all. Most sources are not in the library, so a miss (``None``) is the
common, expected case either way.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .library import stable_title
from .sidecar import read_clip, read_version_group

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def clip_title(meta: dict) -> str:
    """What a clip record calls its scene -- "performer - movie", or "" for neither."""
    performer = str(meta.get("performer", "") or "").strip()
    source = str(meta.get("source", "") or "").strip()
    return " - ".join(part for part in (performer, source) if part)


def _size(video: Path) -> int:
    try:
        return video.stat().st_size
    except OSError:
        return 0


# Words too common to identify a movie ("Best of Times", "A for Ambrose").
_STOPWORDS = frozenset({"the", "and", "for", "of", "a", "an", "in", "to", "my", "it", "on"})


def _distinctive(source: str, performer: str) -> set[str]:
    """Source-title words that actually identify the movie.

    The performer's own name is stripped out: a title like
    *Nora Quill To the Brink* would otherwise match every Nora Quill file in the
    library on her name alone. Stopwords and one/two-letter fragments go too.
    """
    perf = _tokens(performer)
    return {t for t in _tokens(source) - perf if len(t) > 2 and t not in _STOPWORDS}


def _matches(meta: dict, text: set[str]) -> bool:
    """Whether *text* (a filename's tokens) names this clip's scene.

    Requires every performer token to be present *and* at least one distinctive
    source-title word — enough to pair ``Ann Bly - Alpha Scene 2``
    with a ``Ann-Bly---Alpha-Scene-Deluxe-2-(2009)`` scene, while rejecting
    both a same-source scene starring someone else and an unrelated file that
    merely shares the performer.
    """
    perf = _tokens(str(meta.get("performer", "")))
    if not perf or not perf <= text:
        return False
    distinctive = _distinctive(str(meta.get("source", "")), str(meta.get("performer", "")))
    return bool(distinctive) and bool(distinctive & text)


@dataclass(frozen=True)
class ClipNav:
    _clips: dict[Path, dict]
    _non_clips: tuple[Path, ...]
    # The version family Evolver recorded for each scene ("" for none), so a
    # match recorded against one version resolves from any of them. Keyed by
    # path because the family is Evolver's to declare, not a name's to be read for.
    _groups: dict[Path, str]

    @classmethod
    def build(cls, videos: Iterable[Path], metadata_root: Path | None) -> ClipNav:
        clips: dict[Path, dict] = {}
        non_clips: list[Path] = []
        groups: dict[Path, str] = {}
        for video in videos:
            meta = read_clip(video, metadata_root) if metadata_root is not None else None
            if meta is not None:
                clips[video] = meta
            else:
                non_clips.append(video)
                groups[video] = (
                    read_version_group(video, metadata_root) or ""
                ) if metadata_root is not None else ""
        return cls(clips, tuple(non_clips), groups)

    def _family(self, scene: Path) -> str:
        """*scene*'s version family, for a scene the library may not hold.

        A recorded ``full_video`` can name a file since renamed or moved out;
        reading its name is then all that is left of it.
        """
        return self._groups.get(scene) or stable_title(scene.stem)

    def title_of(self, video: Path) -> str:
        """What to call *video* on screen: its own clip record, else the record
        of the clip carved from it, else the family it was grouped under, else
        the filename."""
        meta = self._clips.get(video)
        if meta is None:
            clip = self.clip_of(video)
            meta = self._clips.get(clip) if clip is not None else None
        recorded = clip_title(meta) if meta is not None else ""
        return recorded or self._groups.get(video) or video.stem

    def compilation_of(self, video: Path) -> str:
        """The title of the compilation *video* was carved from, or "" for a
        non-clip.  The main player's HUD names it, so the player can say what is holding the
        playlist rather than leaving it to be inferred from the clips."""
        meta = self._clips.get(video)
        return str(meta.get("compilation", "") or "") if meta is not None else ""

    def compilation_playlist(self, video: Path) -> list[Path]:
        """The clips of *video*'s compilation in original order, self included.

        Empty when *video* is not a clip.
        """
        meta = self._clips.get(video)
        if meta is None:
            return []
        comp = meta.get("compilation")
        # One slot per scene: an upscaled variant carries the same clip object as
        # its original, so keep only the largest file for each running index —
        # the same "canonical is biggest" rule the version grouping uses.
        best: dict[int, Path] = {}
        for sibling, m in self._clips.items():
            if m.get("compilation") != comp:
                continue
            index = m.get("index", 0)
            current = best.get(index)
            if current is None or _size(sibling) > _size(current):
                best[index] = sibling
        return [best[i] for i in sorted(best)]

    def full_vid_of(self, video: Path) -> Path | None:
        """The non-clip library video this clip's scene was taken from, or None.

        A recorded ``full_video`` answers outright, and answers with the best
        version of that scene rather than the one the match was made against —
        the same file the playlist would be showing. Otherwise the names decide.
        """
        meta = self._clips.get(video)
        if meta is None:
            return None
        family = self._recorded_family(meta)
        recorded = _largest([s for s in self._non_clips if self._family(s) == family])
        if recorded is not None:
            return recorded
        return _resolve(meta, [c for c in self._non_clips if c != video])

    def _recorded_family(self, meta: dict) -> str | None:
        """The version family of the scene Evolver recorded a match to, if any."""
        recorded = meta.get("full_video")
        return self._family(Path(str(recorded))) if recorded else None

    def clip_of(self, video: Path) -> Path | None:
        """The clip carved from *video*'s scene, or None.

        The reverse of :meth:`full_vid_of`, and recorded matches win here too:
        the clip that recorded *this* scene is the answer, whichever version of
        either is on screen.

        Never the file you are already on: a clip matches its own name, which
        would just replay it. Being on a clip already means there is nothing to
        jump to — its siblings are a different scene, not this one's clip jump.
        """
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
        # Falling back to the performer alone has to hold in *both* directions:
        # one clip for them, and this scene their only one. One clip against
        # several scenes still leaves which scene it came from unknowable, so a
        # pairing there would be a guess dressed as an answer.
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
    """Whether *meta*'s clip might have been cut from *scene*, on the names alone.

    The performer is the one thing a library filename reliably carries, so this
    is the widest net worth casting — the same net Evolver's clip-match batch
    casts before narrowing it by looking at the pictures.
    """
    performer = _performer_of(meta)
    return bool(performer) and performer <= _tokens(scene.stem)


def _performer_of(meta: dict) -> set[str]:
    return _tokens(str(meta.get("performer", "")))


def _largest(candidates: list[Path]) -> Path | None:
    """The biggest of *candidates*, or None when there are none.

    Biggest is canonical throughout the library layer: several files matching
    one scene are versions of it, and the largest is the best of them.
    """
    return max(candidates, key=_size) if candidates else None


def _only_candidate(candidates: list[Path]) -> Path | None:
    """The one scene among *candidates*, or None when they are several.

    Re-encodes of one video are not "several": ``X.mp4`` and ``X_iris2.mp4`` are
    the same scene, so they collapse under :func:`stable_title` first. The
    largest survivor wins, the canonical-is-biggest rule used elsewhere.
    """
    if not candidates:
        return None
    by_title: dict[str, list[Path]] = {}
    for candidate in candidates:
        by_title.setdefault(stable_title(candidate.stem), []).append(candidate)
    if len(by_title) != 1:
        return None
    return _largest(next(iter(by_title.values())))


def _resolve(meta: dict, candidates: list[Path]) -> Path | None:
    """The library file *meta*'s scene was taken from, or None.

    Two tiers, because filenames in the wild carry wildly different detail. A
    name that repeats the movie ("...Alpha-Scene-Deluxe-2-(2009)...") is matched
    outright. A name that carries only the performer ("Iris-Fenn_540-hash")
    can still be resolved — but only when that performer has exactly one scene
    here, since with several there is no way to tell which one it was.
    """
    named = _largest([c for c in candidates if _matches(meta, _tokens(c.stem))])
    if named is not None:
        return named
    return _only_candidate([c for c in candidates if could_be_cut_from(meta, c)])

"""Smart library layer for the main player: version grouping, quality dedup, and
length-mode filtering — all pure logic, no pygame or I/O.

Many files in the wild are the same content at different quality or upscale
(``...-1080p_60fps.mp4``, ``...-old_iris2.mp4``, ``..._topaz.mp4``). This
module folds those variants into a single *version group* keyed on the name —
an upscale is almost always the original's name plus an appended tag — picks
the largest file as the canonical one, and offers a canonical-only shuffle, a
Fun-Time playlist collapse, and a full-length/shorts filter.
"""
from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from player_core.modes import LengthMode

from .video_kind import EXCERPT, FULL_LENGTH, GENAU_CLIP, SHORT

# Tunable heuristic: tokens dropped anywhere in a title because they mark a
# quality/upscaler/codec/resolution/container variant rather than content.
# This is deliberately conservative — the list can over-group (folding two
# genuinely different videos) or under-group (missing an unlisted tag), and is
# meant to be edited as new tags show up in the library.
_QUALITY_TOKENS = frozenset({
    # upscalers / restoration
    "topaz", "iris2", "old_iris2", "upscale", "upscaled", "remux",
    # codecs
    "x264", "x265", "hevc", "av1",
    # frame rates
    "60fps", "30fps", "24fps",
    # resolutions
    "480p", "540", "540p", "720p", "1080p", "1440p", "2160p", "4k",
    # containers / sources
    "mp4", "mkv", "web", "webrip",
})

# "old_iris2" contains an underscore, so it must be matched before the
# separator collapse splits it. Keep the multi-part quality tokens here.
_MULTIPART_QUALITY = tuple(t for t in _QUALITY_TOKENS if "_" in t)

_SEPARATORS = re.compile(r"[-_ .]+")
_HASH_TOKEN = re.compile(r"[a-z0-9]{6,12}")


def _is_hash_token(token: str) -> bool:
    """A short alnum token that mixes letters and digits (e.g. ``ehwgjw62``).

    Requiring both classes keeps pure words (``compilation``) and pure
    numbers (a scene index like ``1``) from being mistaken for hashes.
    """
    if not _HASH_TOKEN.fullmatch(token):
        return False
    return any(c.isalpha() for c in token) and any(c.isdigit() for c in token)


def normalize_title(stem: str) -> str:
    text = stem.lower()
    for token in _MULTIPART_QUALITY:
        text = text.replace(token, " ")
    tokens = [t for t in _SEPARATORS.split(text) if t and t not in _QUALITY_TOKENS]
    while tokens and _is_hash_token(tokens[-1]):
        tokens.pop()
    return " ".join(tokens)


# Evolver names an enhanced file by appending the models that made it, so
# "scene_apo8_iris2" is "scene".  normalize_title knows some of these as quality
# tokens but not all, and a suffix missing from both lists makes a scene and its
# own upscale look like two different scenes.
_PROCESSING_SUFFIXES = (
    "_topaz_cfr", "_topaz", "_gcg5", "_prob4", "_ghq5",
    "_iris3", "_iris2", "_apf2", "_apo8", "_enh",
)


def _strip_processing(stem: str) -> str:
    """*stem* with every trailing processing suffix removed."""
    changed = True
    while changed:
        changed = False
        for suffix in _PROCESSING_SUFFIXES:
            if stem.endswith(suffix):
                stem, changed = stem[: -len(suffix)], True
    return stem


def stable_title(stem: str) -> str:
    """*stem* reduced to what stays the same across versions of one video.

    Where :func:`normalize_title` drops the tags a name may carry, this also
    drops the ones Evolver appends, leaving two files equal exactly when they
    are one video re-encoded. Unlike the prefix test :func:`group_versions`
    falls back on, that equality does not fold a performer's longer-named
    second scene into their first.
    """
    return normalize_title(_strip_processing(stem))


@dataclass(frozen=True)
class LibraryEntry:
    """One playable video plus its optional funscript and file size.

    Size drives canonical selection within a version group (largest wins),
    so it is probed once at discovery and carried through the pipeline.
    """

    video: Path
    funscript: Path | None
    size: int


@dataclass(frozen=True)
class VersionGroup:
    """Entries that are versions of one video, ordered largest-first.

    Entries share a title prefix — an upscale is the original's name plus a
    tag. The first (largest) entry is the canonical version to shuffle into a
    playlist; the rest are alternates reachable via "cycle version".
    """

    entries: tuple[LibraryEntry, ...]

    @property
    def canonical(self) -> LibraryEntry:
        return self.entries[0]

    @property
    def alternates(self) -> list[LibraryEntry]:
        return list(self.entries[1:])


def _title_tokens(stem: str) -> tuple[str, ...]:
    return tuple(normalize_title(stem).split())


# A bare number in brackets at the end of a name -- "Jane Doe - Studio (3)" --
# is how a download names the third DIFFERENT video of a set, not a tag on a
# re-encode of the first.  Left to the prefix test every one of them joined the
# group anchored by the un-numbered name, so a shelf of five distinct scenes
# collapsed into one with four "other versions".
_COPY_INDEX = re.compile(r"\(\d+\)")


def _is_prefix(shorter: tuple[str, ...], longer: tuple[str, ...]) -> bool:
    """Whether *shorter*'s tokens begin *longer*'s (equal counts as a prefix).

    Except where the tokens *longer* carries past the prefix start with a copy
    index: that says a different video of the same set, and versions of it are
    named past the index rather than before it ("… (3)" and "… (3) topaz" are
    still one video, "… " and "… (3)" are two).
    """
    if len(shorter) > len(longer) or longer[: len(shorter)] != shorter:
        return False
    extra = longer[len(shorter):]
    return not (extra and _COPY_INDEX.fullmatch(extra[0]))


def _matching_group(
    groups: list[list[LibraryEntry]],
    group_tokens: list[tuple[str, ...]],
    toks: tuple[str, ...],
) -> list[LibraryEntry] | None:
    """The existing group whose title begins *toks*, or None.

    An empty *toks* (a name of only quality/hash tokens) matches nothing, so
    such a file stands alone rather than swallowing every other singleton.
    """
    if not toks:
        return None
    for group, gtoks in zip(groups, group_tokens):
        if gtoks and _is_prefix(gtoks, toks):
            return group
    return None


def group_versions(
    entries: list[LibraryEntry],
    group_id_of: Callable[[Path], str | None] | None = None,
) -> list[VersionGroup]:
    """Fold entries into same-content version groups.

    With *group_id_of* — a resolver for a clip's recorded version-family id
    (Evolver's metadata sidecar) — an entry that has an id is grouped by it,
    the authoritative source, and entries without one fall back to the name
    heuristic below. Without the resolver, everything groups by name.
    """
    if group_id_of is not None:
        return _group_by_recorded_id(entries, group_id_of)
    return _group_by_name(entries)


def _copy_index_of(video: Path) -> str:
    """The bracketed number a download gave this copy, or "".

    "Jane Doe - Studio (3).mp4" is the third DIFFERENT video of a set, and the
    only thing in the name that says so.  Read off the file name rather than off
    the recorded family, because the family is exactly what does not say it.
    """
    found = _COPY_INDEX.findall(video.stem)
    return found[-1] if found else ""


def _recorded_family(video: Path, gid: str) -> str:
    """The family a recorded id puts *video* in, split by its copy index.

    Evolver records one id for every file whose name begins the same way, so a
    set downloaded as "… ", "… (2)" … "… (5)" comes back as one family of nine
    when it is five scenes with their upscales.  The id still anchors the
    family -- it is the only thing that can pair a hand-renamed re-encode with
    its original -- and the number refines it, so "(2)" and "(2)_apo8_iris2"
    stay together while "(2)" and "(3)" come apart.
    """
    return f"{gid}{_copy_index_of(video)}"


def _group_by_recorded_id(
    entries: list[LibraryEntry],
    group_id_of: Callable[[Path], str | None],
) -> list[VersionGroup]:
    """Group entries by their recorded family id; unrecorded ones fall back to name."""
    by_id: dict[str, list[LibraryEntry]] = {}
    order: list[str] = []
    unrecorded: list[LibraryEntry] = []
    for entry in entries:
        gid = group_id_of(entry.video)
        if gid is None:
            unrecorded.append(entry)
            continue
        gid = _recorded_family(entry.video, gid)
        if gid not in by_id:
            by_id[gid] = []
            order.append(gid)
        by_id[gid].append(entry)
    groups = [
        VersionGroup(entries=tuple(sorted(by_id[gid], key=lambda e: (-e.size, str(e.video)))))
        for gid in order
    ]
    groups.extend(_group_by_name(unrecorded))
    return groups


def _group_by_name(entries: list[LibraryEntry]) -> list[VersionGroup]:
    """Fold entries into same-content version groups, by name alone.

    Two files are the same video when one's normalized title is a token-wise
    *prefix* of the other's — the overwhelmingly common case of upscaling a
    video and appending a tag to its name (``scene`` vs ``scene_topaz_v2``).
    Duration is deliberately not consulted: real re-encodes and re-trims drift
    a handful of seconds, and a shared name prefix is the reliable signal,
    where a shared runtime would fold genuinely different scenes of like length.

    Entries are matched shortest-title-first, so an original anchors the group
    its longer-named upscales join. Within a group entries are ordered
    largest-file first (canonical first); group order follows first appearance.
    """
    order = {entry: i for i, entry in enumerate(entries)}
    tokens = {entry: _title_tokens(entry.video.stem) for entry in entries}
    groups: list[list[LibraryEntry]] = []
    group_tokens: list[tuple[str, ...]] = []
    for entry in sorted(entries, key=lambda e: (len(tokens[e]), order[e])):
        toks = tokens[entry]
        anchor = _matching_group(groups, group_tokens, toks)
        if anchor is None:
            groups.append([entry])
            group_tokens.append(toks)
        else:
            anchor.append(entry)
    groups.sort(key=lambda group: min(order[m] for m in group))
    return [
        VersionGroup(entries=tuple(sorted(group, key=lambda e: (-e.size, str(e.video)))))
        for group in groups
    ]


def collapse_playlist_versions(
    pairs: list[tuple[Path, Path | None]],
    version_index: dict[Path, list[tuple[Path, Path | None]]],
) -> list[tuple[Path, Path | None]]:
    """Dedupe a playlist to one entry per version group, order preserved.

    *version_index* (from :func:`version_index_from_groups`) maps each known
    video to its group's pairs, largest-first. Each group is emitted once, at
    its first-seen position, keeping the largest entry actually present in
    *pairs* (with that entry's funscript from *pairs*). Videos absent from the
    index pass through unchanged. This turns Fun Time's raw per-file playlist
    into the one-slot-per-video rotation the main slot player shows, matching the
    set :meth:`PlayerSession.cycle_version` walks.
    """
    funscript_by_video = dict(pairs)
    present = set(funscript_by_video)
    collapsed: list[tuple[Path, Path | None]] = []
    seen: set[Path] = set()
    for video, _funscript in pairs:
        versions = version_index.get(video)
        if versions:
            group_id = versions[0][0]
            keep = next((v for v, _ in versions if v in present), video)
        else:
            group_id = video
            keep = video
        if group_id in seen:
            continue
        seen.add(group_id)
        collapsed.append((keep, funscript_by_video.get(keep)))
    return collapsed


def canonical_playlist(
    entries: list[LibraryEntry], rng: random.Random,
) -> list[LibraryEntry]:
    """One canonical entry per version group, shuffled with *rng*.

    Deterministic for a given seeded ``random.Random`` so a session (and its
    tests) can reproduce the order.
    """
    canonicals = [group.canonical for group in group_versions(entries)]
    rng.shuffle(canonicals)
    return canonicals


# What "short" means for a video Evolver has not recorded yet, and the same
# number it records by — one line through the family instead of this player's
# old 60 seconds and the phone's 10.
SHORT_MAX_S = 10.0

# The three length modes.  MIXED applies no length filter at all — it is what a
# playlist looks like before anyone asks for a length, and so what the player
# opens in; FULL and SHORTS are the two halves it splits into.
MIXED = LengthMode.MIXED
FULL = LengthMode.FULL
SHORTS = LengthMode.SHORTS
# Neither length: a browse with nothing in it.  Degenerate, and the console
# offers it anyway rather than refusing the press -- what happens is that the
# video on screen is held, which is a state you can see and undo.
NONE = LengthMode.NONE

# Which kinds each of the two filtering modes plays.  A delivered loop and a
# scene carved out of a longer one are shorts however long they run: the loop
# is a couple of seconds by construction, and a carved scene is an excerpt of
# something, which is what "full length" means the absence of.
SHORTS_KINDS = frozenset({SHORT, EXCERPT, GENAU_CLIP})
FULL_KINDS = frozenset({FULL_LENGTH})


def kind_of_video(
    video: Path,
    *,
    kind_of: Callable[[Path], str] | None,
    durations: dict[Path, float],
    genau_clips: set[Path],
) -> str:
    """What *video* is: what Evolver recorded, or what can be told without it.

    The record is the answer wherever there is one.  The fallbacks are for a
    video Evolver has not reached — a library it has never run over, or a file
    that arrived since its last run: a loop is known by the folder it was
    delivered to, and everything else by its running time.  ``""`` is a video
    nothing could classify, which both length modes drop.
    """
    recorded = kind_of(video) if kind_of is not None else ""
    if recorded:
        return recorded
    if video in genau_clips:
        return GENAU_CLIP
    seconds = durations.get(video)
    if seconds is None:
        return ""
    return SHORT if seconds <= SHORT_MAX_S else FULL_LENGTH


def select_library(
    entries: list[LibraryEntry],
    *,
    mode: str,
    durations: dict[Path, float],
    genau_clips: list[LibraryEntry],
    kind_of: Callable[[Path], str] | None = None,
) -> list[LibraryEntry]:
    """Filter *entries* by length *mode*, then version-dedup the survivors.

    Mixed mode applies no length filter: every entry and every clip survives,
    including the ones nothing has classified, since nothing here has to.  NONE
    is its opposite and keeps nothing at all.  The
    other two keep the kinds :data:`SHORTS_KINDS` and :data:`FULL_KINDS` name —
    :func:`kind_of_video` says what each video's kind is, and a video with no
    kind at all is dropped by both.  Anything that is not one of the three
    modes filters as full-length, which is what it has always done.

    *kind_of* reads what Evolver recorded (``main_player.sidecar.read_video_type``); the
    *durations* are the fallback for what it has not reached, and *genau_clips*
    — the videos discovered in Genau's own delivery folder — are loops by where
    they came from.  Not the other sense of "clip" in this package: a scene
    carved out of a compilation is an ``EXCERPT``, and lives in its sidecar.

    Returns one canonical entry per surviving version group.
    """
    if mode == NONE:
        kept = []
    elif mode == MIXED:
        kept = [*entries, *genau_clips]
    else:
        wanted = SHORTS_KINDS if mode == SHORTS else FULL_KINDS
        delivered = {clip.video for clip in genau_clips}
        kept = [
            entry for entry in (*entries, *genau_clips)
            if kind_of_video(entry.video, kind_of=kind_of, durations=durations,
                             genau_clips=delivered) in wanted
        ]
    return [group.canonical for group in group_versions(kept)]


def entries_to_pairs(entries: list[LibraryEntry]) -> list[tuple[Path, Path | None]]:
    """Drop file sizes, leaving the (video, funscript) pairs the session wants."""
    return [(e.video, e.funscript) for e in entries]


def version_index_from_groups(
    groups: list[VersionGroup],
) -> dict[Path, list[tuple[Path, Path | None]]]:
    """Map every video to its group's (video, funscript) pairs, largest-first.

    This is what :meth:`PlayerSession.cycle_version` consults to walk between
    versions of the same content — each entry points at the same ordered
    pair list, so cycling is stable regardless of which one is playing.
    """
    index: dict[Path, list[tuple[Path, Path | None]]] = {}
    for group in groups:
        versions = [group.canonical, *group.alternates]
        pairs = entries_to_pairs(versions)
        for entry in versions:
            index[entry.video] = pairs
    return index


def library_playlist(
    entries: list[LibraryEntry],
    *,
    mode: str,
    durations: dict[Path, float],
    genau_clips: list[LibraryEntry],
    rng: random.Random,
    kind_of: Callable[[Path], str] | None = None,
) -> list[tuple[Path, Path | None]]:
    """The library as a playlist: filter by *mode*, version-dedup, shuffle, pair.

    Deterministic for a seeded *rng*. This is the single composition both
    startup and the length-mode toggle use, so their playlists stay
    consistent.
    """
    selected = select_library(
        entries, mode=mode, durations=durations, genau_clips=genau_clips,
        kind_of=kind_of,
    )
    return entries_to_pairs(canonical_playlist(selected, rng))

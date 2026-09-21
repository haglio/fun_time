"""Sidecar access for the library's videos, generated and filmed alike.

Every library video may have a JSON sidecar in a mirrored tree under the
metadata root: a generated one records what it was generated from (a ``video``
block, plus a ``source_image`` block when it was animated from a generated
image), a carved one records the compilation, movie and performer it came from,
and every one of them carries the kind and the watch stamps Evolver writes.
This module owns the mapping from a video file to its sidecar, the sidecar
loading, and the one edit Fun Time makes to a sidecar (:func:`reject_action`);
consumers layer their own interpretation on top (e.g. :mod:`fun_time.regen`
builds regenerate URLs from it).
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app_support.json_store import locked_update
from app_support.mirrored_tree import library_roots_beside, mirrored_path


def normalize_path_key(path: str) -> str:
    return path.strip().lower()


def metadata_path_for(
    video_path: str | Path,
    metadata_root: str | Path | None,
) -> Path | None:
    """Map a video to its metadata JSON, mirroring the whole video library.

    The rule -- same relative path under the other root, suffix swapped, and
    the several spellings a path can reach here under -- belongs to every app
    that reads one of these files and is
    :mod:`app_support.mirrored_tree`, not this module.
    """
    if metadata_root is None:
        return None
    metadata_root = Path(metadata_root)
    return mirrored_path(
        video_path,
        roots=library_roots_beside(metadata_root),
        mirror_root=metadata_root,
        suffix=".json",
    )


# Evolver records what kind every library video is on its sidecar, as
# ``video.type``: one answer to "what kind of video is this", for the whole
# library.  This app asks it one thing, so this is the one kind it names.
EXCERPT = "excerpt"


def video_type_of(payload: dict) -> str:
    """The kind *payload* records, or ``""`` when it records none.

    The one older record still read here is the ``clip`` object: it says a
    scene was carved out of a longer one, which is what :data:`EXCERPT` says,
    and it was on these sidecars before there was a kind to write.  So a
    library Evolver has not been over since keeps its cuts in their own band
    rather than waiting for the run that records them.
    """
    video = payload.get("video")
    if isinstance(video, dict) and video.get("type"):
        return str(video["type"])
    return EXCERPT if isinstance(payload.get("clip"), dict) else ""


WATCH_BLOCK = "watch"


def clip_title(payload: dict) -> str:
    """What *payload*'s clip record calls the scene -- "performer - movie", and
    ``""`` for a video that is not a carved clip."""
    clip = payload.get("clip")
    if not isinstance(clip, dict):
        return ""
    parts = (str(clip.get(field, "") or "").strip() for field in ("performer", "source"))
    return " - ".join(part for part in parts if part)


# A bare number in brackets at the end of a name is how a download names a
# DIFFERENT video of a set; Evolver gives every such name one family id.
_COPY_INDEX = re.compile(r"\(\d+\)")


def recorded_group(payload: dict, video: str | Path) -> str:
    """The version family Evolver recorded for *video*, split by its copy index:
    the id pairs a hand-renamed re-encode with its original, and the number
    keeps "(2)" apart from "(3)".  ``""`` for no record."""
    version = payload.get("version")
    if not isinstance(version, dict):
        return ""
    group = version.get("group")
    if not group:
        return ""
    found = _COPY_INDEX.findall(Path(video).stem)
    return f"{group} {found[-1]}" if found else str(group)


TITLE_FIELD = "title"


def video_title(payload: dict, video: str | Path) -> str:
    """What to call *video*: the name Evolver recorded, else the pair recorded
    for the clip, else the family it was grouped under, else its filename."""
    return (
        str(payload.get(TITLE_FIELD, "") or "").strip()
        or clip_title(payload)
        or recorded_group(payload, video)
        or Path(video).stem
    )


def watch_weight_of(payload: dict) -> float:
    """The playback weight stamped on *payload*, 1.0 for a video nobody has watched."""
    block = payload.get(WATCH_BLOCK)
    if not isinstance(block, dict):
        return 1.0
    try:
        return float(block.get("weight", 1.0))
    except (TypeError, ValueError):
        return 1.0


def load_metadata(json_path: str | Path) -> dict:
    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# Where a struck-out act is kept once a viewer has said it is wrong.  Clearing
# ``video.action`` is what puts the clip back in front of Evolver's backfill tool
# (a clip with no act is one that still needs one); this key is what tells that
# tool the clip was *rejected* rather than never labeled, so it can ask about it
# first and whatever the clip's source.  Evolver reads it in ``util/sidecar.py``
# and drops it again the moment a new act is recorded.
WRONG_ACTION_FIELD = "wrong_action"


def reject_action(video_path: str | Path, metadata_root: str | Path | None) -> str:
    """Strike the act out of *video_path*'s sidecar; return the act that went.

    Writes nothing, and answers ``""``, for a clip with no sidecar, a sidecar
    recording no act, and a document this app cannot parse -- which is somebody
    else's record, not ours to replace.  Everything else on it is left alone.

    The read and the write hold the document's own lock, the one Evolver's
    pipeline takes on the same name: without it whichever wrote second erased
    what the other had just put in (bug 8).
    """
    json_path = metadata_path_for(video_path, metadata_root)
    if json_path is None:
        return ""
    struck = ""

    def strike_the_act_out(payload: dict) -> dict | None:
        nonlocal struck
        video = payload.get("video")
        if not isinstance(video, dict):
            return None
        struck = str(video.pop("action", "") or "").strip()
        if not struck:
            return None
        video[WRONG_ACTION_FIELD] = struck
        return payload

    try:
        locked_update(json_path, strike_the_act_out)
    except ValueError:
        return ""
    return struck


def _norm_text(value: object) -> str:
    return " ".join(str(value or "").split()).lower()


def filter_haystack(metadata: dict) -> str:
    """Lowercased, whitespace-collapsed text a filter query is matched against.

    The clip's recorded act, and nothing else.  The generation prompts used to be
    matched too (the video prompt and the source image's positive prompt), and
    they say what was *asked for* — the still's pose, the motion requested — not
    what the finished clip was judged to show, so they drag in clips doing
    something else entirely.  Nothing downstream can recover from that: a filter
    is spoken from the act vocabulary and every clip in the HUD map is labeled
    with its act, so a clip pulled in by prompt text sits under a row naming some
    other act — or "(unknown)" where no act was recorded at all.  Half of one
    two-word act's browse arrived that way in this library.

    A clip with no act recorded is therefore out of every filter: it is the
    backfill tool's backlog (see :data:`WRONG_ACTION_FIELD`), not something to
    guess at from its prompt.
    """
    return _norm_text((metadata.get("video") or {}).get("action"))


def matches_query(metadata: dict, query: str) -> bool:
    """Whether *metadata* satisfies *query* — an empty query matches everything.

    A query matches when it appears as a contiguous substring of the video's
    act, so "alpha" catches the "Alpha, Theta Motion" action and "beta gamma"
    catches the "Beta Gamma" action while ignoring word order noise.
    """
    normalized = _norm_text(query)
    if not normalized:
        return True
    return normalized in filter_haystack(metadata)


def path_matches_query(
    video_path: str,
    metadata_root: str | Path | None,
    query: str,
) -> bool:
    """Whether the sidecar for *video_path* satisfies *query*.

    An empty query passes every video.  A non-empty query can only be satisfied
    by a video whose sidecar records an act, so videos with no sidecar — and
    videos still waiting for an act — drop out of a filtered build.
    """
    if not _norm_text(query):
        return True
    sidecar = metadata_path_for(video_path, metadata_root)
    if sidecar is None or not sidecar.is_file():
        return False
    return matches_query(load_metadata(sidecar), query)


# Fields that pin down the generated image (subject(s) + situation).  "created"
# is deliberately excluded: regenerating the identical config on another day
# yields the same picture.  Public, like the video's below: they are what this
# app reads off a record another repo writes, and
# tests/test_library_record_contract.py holds them to what it promises.
IMAGE_IDENTITY_FIELDS = (
    "positive_prompt",
    "negative_prompt",
    "model",
    "resolution",
    "aspect_ratio",
    "quality",
    "style",
    "creativity",
    "seed",
)

# Video-block fields shared by every variation axis; "action" and "seed" are
# appended per key kind ("created" excluded as above).
VIDEO_BASE_FIELDS = (
    "prompt",
    "model",
    "resolution",
    "aspect_ratio",
    "quality",
)


def _field_key(prefix: str, block: dict, fields: tuple[str, ...]) -> str:
    return prefix + "|" + "|".join(_norm_text(block.get(f)) for f in fields)


def action_group_key(metadata: dict) -> str | None:
    """Identity of the subject(s) + situation a video shows, or None if unknown.

    Videos sharing a key form an "action group": the same subject rendered
    doing different things.  For image-to-video clips the source image IS the
    subject, so the key is the image's full generation identity.  For
    text-to-video clips the subject is pinned by the video prompt + seed, so
    only the action dropdown is left free.
    """
    source = metadata.get("source_image")
    if source:
        return _field_key("img", source, IMAGE_IDENTITY_FIELDS)
    video = metadata.get("video") or {}
    if not video.get("prompt"):
        return None
    return _field_key("t2v", video, VIDEO_BASE_FIELDS + ("seed",))


_IMAGE_FAMILY_FIELDS = tuple(f for f in IMAGE_IDENTITY_FIELDS if f != "seed")


def _seed_key(
    metadata: dict, image_fields: tuple[str, ...], video_fields: tuple[str, ...]
) -> tuple[str, str] | None:
    """(family, seed) for a video, familied by *image_fields* / *video_fields*.

    Returns None when the metadata lacks a seed or a prompt to family by.
    """
    source = metadata.get("source_image")
    if source:
        if not source.get("seed") or not source.get("positive_prompt"):
            return None
        return _field_key("img", source, image_fields), _norm_text(source.get("seed"))
    video = metadata.get("video") or {}
    if not video.get("seed") or not video.get("prompt"):
        return None
    return _field_key("t2v", video, video_fields), _norm_text(video.get("seed"))


def seed_group_key(metadata: dict) -> tuple[str, str] | None:
    """(family, seed) placing a video among its same-config-different-seed kin.

    Videos sharing a family were generated from the identical configuration
    with only the seed varied — the same scenario cast with a different subject.
    """
    return _seed_key(metadata, _IMAGE_FAMILY_FIELDS, VIDEO_BASE_FIELDS + ("action",))


def scene_tags(metadata: dict) -> frozenset[str]:
    """The scene a video shows, as the set of its prompt's comma-separated tags.

    These prompts are tag lists ("redacted, bangs, big bright eyes, …"), so
    how much two clips have in common is how much their tag sets overlap — the
    measure "more seeds" ranks by.  The source image's prompt describes the
    subject for an image-to-video clip; a text-to-video clip has only its own.
    """
    source = metadata.get("source_image") or {}
    text = source.get("positive_prompt") or (metadata.get("video") or {}).get("prompt") or ""
    return frozenset(tag for tag in (_norm_text(part) for part in str(text).split(",")) if tag)


def _tag_overlap(one: frozenset[str], other: frozenset[str]) -> float:
    """How alike two scenes are, 0.0-1.0 — shared tags over all tags between them."""
    if not one or not other:
        return 0.0
    return len(one & other) / len(one | other)


@dataclass(frozen=True)
class ClipEntry:
    """Everything the index knows about one clip.  A clip with no sidecar still
    gets one: the defaults ARE "nothing is recorded about it"."""

    path: str
    action: str = ""
    action_key: str | None = None
    seed_key: tuple[str, str] | None = None
    scene_tags: frozenset[str] = frozenset()
    from_image: bool = False
    weight: float = 1.0


@dataclass(frozen=True)
class GroupIndex:
    """Grouping of a video library by generation identity: one
    :class:`ClipEntry` per clip keyed by :func:`normalize_path_key`, plus the two
    inverted indexes answering "who else is in this group" (sorted lists of the
    original path strings)."""

    entries: dict[str, ClipEntry]
    action_items: dict[str, list[str]]
    seed_items: dict[str, list[str]]

    def entry(self, path: str) -> ClipEntry:
        return self.entries.get(normalize_path_key(path)) or _UNKNOWN_CLIP

    def contains(self, path: str) -> bool:
        return normalize_path_key(path) in self.entries

    def weight_of(self, path: str) -> float:
        return self.entry(path).weight

    def act_of(self, path: str) -> str:
        return self.entry(path).action

    def indexed_path(self, key: str, fallback: str = "") -> str:
        entry = self.entries.get(key)
        return entry.path if entry is not None else fallback


_UNKNOWN_CLIP = ClipEntry(path="")


def action_group_items(index: GroupIndex, path: str) -> list[str]:
    """Every clip of *path*'s subject — the same subject(s)+scene, each action."""
    key = index.entry(path).action_key
    if key is None:
        return []
    return list(index.action_items[key])


def indexed_act(index: GroupIndex, path: str) -> str:
    """*path*'s recorded act as the seed axis compares it — lowercased, spacing
    collapsed.

    The seed axis asks "is this the same act?" to decide who is in a row, so a raw
    string compare splits one act into pools that cannot see each other the moment
    two clips are labeled with different casing — and a clip alone in its spelling
    has no seed row at all.  One act spelled two ways -- title case beside
    capitals -- is the ordinary case in a hand-labeled library, and this is what
    keeps it from mattering.
    """
    return _norm_text(index.act_of(path))


def seed_family_items(index: GroupIndex, path: str) -> list[str]:
    """Every clip of *path*'s parameter set doing *path*'s action, each seed.

    A text-to-video family already pins the action, but an image-to-video family
    is keyed on the source image alone, so its items are narrowed here to the
    current clip's action — "the same act, another subject".
    """
    seed_key = index.entry(path).seed_key
    if seed_key is None:
        return []
    family, _seed = seed_key
    action = indexed_act(index, path)
    return [
        item
        for item in index.seed_items[family]
        if indexed_act(index, item) == action
    ]


# How many near-matches "more seeds" adds to the exact family.  Six fills the
# HUD's seed row; the point of the cap is that widening must never dump the
# whole action (hundreds of clips) into a row meant to show a few close kin.
WIDEN_ADDITIONS = 6


def widened_seed_items(
    index: GroupIndex, path: str, additions: int = WIDEN_ADDITIONS
) -> list[str]:
    """The widened seed row for *path* — "more seeds": its exact seed family plus
    the *additions* clips of *path*'s own action whose scene is closest to it.

    The action is a hard bound, not a preference.  The seed axis means "the same
    act, another subject", and a different act is what the action column is for —
    so a widened row that ranked other acts in was answering a question nobody
    asked, and, since "more seeds" loops the row it draws, those other acts
    *played*.  Under a side filter that is plainly wrong (the filter is an act,
    and the widen walked straight out of it); off a filter it is still wrong, just
    quieter.  Bounding here rather than at each caller is what keeps a filter out
    of this function entirely: the row can no longer leave the act it started in,
    and every act filter is satisfied by the act it started in.

    Within the action, candidates are ranked:

    1. **Same generation kind.**  An image-to-video clip and a text-to-video one
       look drastically different however alike their prompts read, so the widen
       prefers the kind it started in.
    2. **Prompt-tag overlap** (:func:`scene_tags`) — how alike the scenes are.

    Both are preferences, so a lone clip of its kind falls through to the
    next-best thing rather than to nothing.  The bound does mean a widen can come
    up empty — an act nothing else in the library does has no wider row, and the
    caller says so rather than reaching for a stranger.
    """
    key = normalize_path_key(path)
    items = list(seed_family_items(index, path))
    if not any(normalize_path_key(item) == key for item in items):
        # A clip with no exact family of its own (no sidecar, no seed) is still
        # the row it anchors, so the pool always opens with it.
        items.insert(0, index.indexed_path(key, path))
    seen = {normalize_path_key(item) for item in items} | {key}
    action = indexed_act(index, path)
    mine_entry = index.entry(path)
    ranked = sorted(
        (
            (
                other.from_image == mine_entry.from_image,
                _tag_overlap(mine_entry.scene_tags, other.scene_tags),
                other_key,
            )
            for other_key, other in index.entries.items()
            if other_key not in seen and _norm_text(other.action) == action
        ),
        # Nearest first; the path key only breaks ties, so the row is stable.
        key=lambda scored: (-scored[0], -scored[1], scored[2]),
    )
    items.extend(index.entries[scored[-1]].path for scored in ranked[:max(additions, 0)])
    return items


def action_label(index: GroupIndex, path: str) -> str:
    """*path*'s action, numbered when its group holds several of that action.

    Two "Alpha" renders of one seed are ordinary action-group siblings, so
    they read as "Alpha 1" and "Alpha 2" as you cycle around the group.
    """
    key = normalize_path_key(path)
    entry = index.entry(path)
    action, group = entry.action, entry.action_key
    if not action or group is None:
        return action
    twins = [
        item
        for item in index.action_items[group]
        if index.act_of(item) == action
    ]
    if len(twins) < 2:
        return action
    position = next(
        (slot for slot, item in enumerate(twins) if normalize_path_key(item) == key), 0
    )
    return f"{action} {position + 1}"




def _entry_for(path: str, metadata: dict) -> ClipEntry:
    return ClipEntry(
        path=path,
        action=str((metadata.get("video") or {}).get("action") or "").strip(),
        action_key=action_group_key(metadata),
        seed_key=seed_group_key(metadata),
        scene_tags=scene_tags(metadata),
        from_image=bool(metadata.get("source_image")),
        weight=watch_weight_of(metadata),
    )


def build_group_index(
    video_paths: Iterable[str],
    metadata_root: str | Path | None,
) -> GroupIndex:
    """Index *video_paths* into action groups and seed families.

    Videos without a metadata sidecar are remembered (for staleness checks)
    but belong to no group.
    """
    entries: dict[str, ClipEntry] = {}
    action_items: dict[str, list[str]] = {}
    seed_items: dict[str, list[str]] = {}
    for path in video_paths:
        sidecar = metadata_path_for(path, metadata_root)
        metadata = load_metadata(sidecar) if sidecar is not None and sidecar.is_file() else {}
        entry = _entry_for(path, metadata)
        entries[normalize_path_key(path)] = entry
        if entry.action_key is not None:
            action_items.setdefault(entry.action_key, []).append(path)
        if entry.seed_key is not None:
            seed_items.setdefault(entry.seed_key[0], []).append(path)
    for items in (*action_items.values(), *seed_items.values()):
        items.sort()
    return GroupIndex(entries=entries, action_items=action_items, seed_items=seed_items)


class GroupIndexCache:
    """Indexes held per library key, so a ~1000-file scan happens once: one is
    rebuilt only when a probe path is missing from it, which is what happens when
    a new arrival starts playing.  The session runs on :data:`SESSION_INDEXES`;
    anything wanting its own — over another library, or to drop without touching
    the session's — makes one of these."""

    def __init__(self) -> None:
        self._indexes: dict[str, GroupIndex] = {}

    def index_for(
        self,
        cache_key: str,
        *,
        paths_supplier,
        metadata_root: str | Path | None,
        must_contain: str | None = None,
    ) -> GroupIndex:
        index = self._indexes.get(cache_key)
        if index is None or (must_contain is not None and not index.contains(must_contain)):
            index = build_group_index(paths_supplier(), metadata_root)
            self._indexes[cache_key] = index
        return index

    def clear(self) -> None:
        self._indexes.clear()


# The one every satellite and HUD in a session shares: its keys are that
# session's library roots, a handful of them.
SESSION_INDEXES = GroupIndexCache()

cached_group_index = SESSION_INDEXES.index_for
reset_group_index_cache = SESSION_INDEXES.clear

"""Generation-metadata sidecar access for AI videos.

Every AI video under the provider media root may have a JSON sidecar in a
mirrored tree under the metadata root, recording the prompts and settings it
was generated from (a ``video`` block, plus a ``source_image`` block when the
video was animated from a generated image).  This module owns the mapping
from a video file to its sidecar, the sidecar loading, and the one edit Fun
Time makes to a sidecar (:func:`reject_action`); consumers layer their own
interpretation on top (e.g. :mod:`fun_time.regen` builds regenerate URLs from
it).
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


def normalize_path_key(path: str) -> str:
    return path.strip().lower()


def _as_spelled(path: str | Path) -> Path:
    """The path made absolute without asking the disk anything."""
    return Path(os.path.abspath(path))


def _as_folded(path: str | Path) -> Path:
    """The spelling with its case folded.  The stats file keys a clip by its
    lowercased path (:func:`normalize_path_key`), and a library root with a
    capital in it is the same place on the case-insensitive disks the library
    lives on -- though not to ``relative_to`` on a platform whose paths compare
    case-sensitively, which left the breeding report empty there."""
    return Path(str(_as_spelled(path)).lower())


def _as_the_disk_has_it(path: str | Path) -> Path:
    """The path with every junction and symlink followed: a trip to the disk, which on
    a drive busy syncing can block for minutes, so only taken when the spelling alone
    did not place the clip under the library."""
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def metadata_path_for(
    video_path: str | Path,
    metadata_root: str | Path | None,
) -> Path | None:
    """Map a video to its metadata JSON, mirroring the whole video library.

    The metadata tree parallels the video tree one-to-one: a clip at
    ``<library>/2D/AI/2_outbox/x.mp4`` has its sidecar at
    ``<metadata_root>/2D/AI/2_outbox/x.json``.  The library root is the
    ``videos`` sibling of *metadata_root* (``…/videos/metadata`` pairs with
    ``…/videos/videos``), so AI and non-AI clips both resolve through here.
    """
    if metadata_root is None:
        return None
    metadata_root = Path(metadata_root)
    library_root = metadata_root.parent / "videos"
    for place in (_as_spelled, _as_folded, _as_the_disk_has_it):
        try:
            rel = place(video_path).relative_to(place(library_root))
        except ValueError:
            continue
        return metadata_root / rel.with_suffix(".json")
    return None


# Evolver records what kind every library video is on its sidecar, as
# ``video.type`` — one answer to "what kind of video is this", written once for
# the whole library in place of the several tests each app used to run.  This
# app asks it one thing, so this is the one kind it names; the others
# (``genau_clip``, ``short``, ``full_length``) come back as themselves.
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


# What Evolver stamps on every library video's sidecar, generation or not:
# the kind, the ``watch`` block summing every app's viewing with the playback
# weight the shuffled builds use, and a favorite flag.  None of it says how the
# clip was made, so anything reading a sidecar as evidence of a recorded
# generation asks :func:`records_no_generation` first.
WATCH_BLOCK = "watch"
FAVORITE_FIELD = "favorite"
_STAMPED_KEYS = frozenset({WATCH_BLOCK, FAVORITE_FIELD})


def records_no_generation(payload: dict) -> bool:
    if set(payload) - _STAMPED_KEYS != {"video"}:
        return False
    video = payload["video"]
    return isinstance(video, dict) and set(video) == {"type"}


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

    Returns ``""`` — and writes nothing — when the clip has no sidecar or its
    sidecar records no act: there is nothing to be wrong about.  Everything else
    the sidecar holds (the prompts, the seed, the source image) is left alone.
    """
    json_path = metadata_path_for(video_path, metadata_root)
    if json_path is None:
        return ""
    payload = load_metadata(json_path)
    video = payload.get("video")
    if not isinstance(video, dict):
        return ""
    action = str(video.pop("action", "") or "").strip()
    if not action:
        return ""
    video[WRONG_ACTION_FIELD] = action
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return action


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
# yields the same picture.
_IMAGE_IDENTITY_FIELDS = (
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
# appended per key kind ("created" excluded for the same reason as above).
_VIDEO_BASE_FIELDS = (
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
        return _field_key("img", source, _IMAGE_IDENTITY_FIELDS)
    video = metadata.get("video") or {}
    if not video.get("prompt"):
        return None
    return _field_key("t2v", video, _VIDEO_BASE_FIELDS + ("seed",))


_IMAGE_FAMILY_FIELDS = tuple(f for f in _IMAGE_IDENTITY_FIELDS if f != "seed")


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
    return _seed_key(metadata, _IMAGE_FAMILY_FIELDS, _VIDEO_BASE_FIELDS + ("action",))


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
class GroupIndex:
    """Grouping of a video library by generation identity.

    Paths are keyed by :func:`normalize_path_key`; member lists hold the
    original path strings, sorted.  ``path_by_key`` remembers every input path
    (sidecar or not) so callers can tell "no metadata" apart from "not indexed
    yet" when deciding whether a cached index is stale — and so the widen can
    range over the whole library, not just the grouped part of it.
    """

    action_key_by_path: dict[str, str]
    action_members: dict[str, list[str]]
    action_by_path: dict[str, str]
    seed_key_by_path: dict[str, tuple[str, str]]
    seed_members: dict[str, list[str]]
    path_by_key: dict[str, str]
    scene_tags_by_path: dict[str, frozenset[str]] = field(default_factory=dict)
    # Which clips were animated from a generated image rather than from text
    # alone.  The two look nothing alike, so the widen ranks its own kind first.
    image_to_video_by_path: dict[str, bool] = field(default_factory=dict)
    weight_by_path: dict[str, float] = field(default_factory=dict)

    def contains(self, path: str) -> bool:
        return normalize_path_key(path) in self.path_by_key

    def weight_of(self, path: str) -> float:
        return self.weight_by_path.get(normalize_path_key(path), 1.0)


def action_group_members(index: GroupIndex, path: str) -> list[str]:
    """Every clip of *path*'s subject — the same subject(s)+scene, each action."""
    key = index.action_key_by_path.get(normalize_path_key(path))
    if key is None:
        return []
    return list(index.action_members[key])


def indexed_act(index: GroupIndex, path: str) -> str:
    """*path*'s recorded act as the seed axis compares it — lowercased, spacing
    collapsed.

    The seed axis asks "is this the same act?" to decide who is in a row, so a raw
    string compare splits one act into pools that cannot see each other the moment
    two clips are labeled with different casing — and a clip alone in its spelling
    has no seed row at all.  The library had 179 such clips ("Pov …" beside
    "POV …") before they were consolidated; this is what keeps the next one from
    mattering.
    """
    return _norm_text(index.action_by_path.get(normalize_path_key(path), ""))


def seed_family_members(index: GroupIndex, path: str) -> list[str]:
    """Every clip of *path*'s parameter set doing *path*'s action, each seed.

    A text-to-video family already pins the action, but an image-to-video family
    is keyed on the source image alone, so its members are narrowed here to the
    current clip's action — "the same act, another subject".
    """
    entry = index.seed_key_by_path.get(normalize_path_key(path))
    if entry is None:
        return []
    family, _seed = entry
    action = indexed_act(index, path)
    return [
        member
        for member in index.seed_members[family]
        if indexed_act(index, member) == action
    ]


# How many near-matches "more seeds" adds to the exact family.  Six fills the
# HUD's seed row; the point of the cap is that widening must never dump the
# whole action (hundreds of clips) into a row meant to show a few close kin.
WIDEN_ADDITIONS = 6


def widened_seed_members(
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
    members = list(seed_family_members(index, path))
    if not any(normalize_path_key(member) == key for member in members):
        # A clip with no exact family of its own (no sidecar, no seed) is still
        # the row it anchors, so the pool always opens with it.
        members.insert(0, index.path_by_key.get(key, path))
    seen = {normalize_path_key(member) for member in members} | {key}
    action = indexed_act(index, path)
    from_image = index.image_to_video_by_path.get(key, False)
    mine = index.scene_tags_by_path.get(key, frozenset())
    ranked = sorted(
        (
            (
                index.image_to_video_by_path.get(other_key, False) == from_image,
                _tag_overlap(mine, index.scene_tags_by_path.get(other_key, frozenset())),
                other_key,
            )
            for other_key in index.path_by_key
            if other_key not in seen and indexed_act(index, other_key) == action
        ),
        # Nearest first; the path key only breaks ties, so the row is stable.
        key=lambda scored: (-scored[0], -scored[1], scored[2]),
    )
    members.extend(index.path_by_key[scored[-1]] for scored in ranked[:max(additions, 0)])
    return members


def action_label(index: GroupIndex, path: str) -> str:
    """*path*'s action, numbered when its group holds several of that action.

    Two "Alpha" renders of one seed are ordinary action-group siblings, so
    they read as "Alpha 1" and "Alpha 2" as you cycle around the group.
    """
    key = normalize_path_key(path)
    action = index.action_by_path.get(key, "")
    group = index.action_key_by_path.get(key)
    if not action or group is None:
        return action
    twins = [
        member
        for member in index.action_members[group]
        if index.action_by_path.get(normalize_path_key(member), "") == action
    ]
    if len(twins) < 2:
        return action
    position = next(
        (slot for slot, member in enumerate(twins) if normalize_path_key(member) == key), 0
    )
    return f"{action} {position + 1}"


def _record_seed_membership(
    key: tuple[str, str] | None,
    path: str,
    key_by_path: dict[str, tuple[str, str]],
    members: dict[str, list[str]],
) -> None:
    """File *path* under its ``(family, seed)`` *key*, if it has one."""
    if key is None:
        return
    key_by_path[normalize_path_key(path)] = key
    members.setdefault(key[0], []).append(path)


def build_group_index(
    video_paths: Iterable[str],
    metadata_root: str | Path | None,
) -> GroupIndex:
    """Index *video_paths* into action groups and seed families.

    Videos without a metadata sidecar are remembered (for staleness checks)
    but belong to no group.
    """
    action_key_by_path: dict[str, str] = {}
    action_members: dict[str, list[str]] = {}
    action_by_path: dict[str, str] = {}
    seed_key_by_path: dict[str, tuple[str, str]] = {}
    seed_members: dict[str, list[str]] = {}
    path_by_key: dict[str, str] = {}
    scene_tags_by_path: dict[str, frozenset[str]] = {}
    image_to_video_by_path: dict[str, bool] = {}
    weight_by_path: dict[str, float] = {}
    for path in video_paths:
        path_by_key[normalize_path_key(path)] = path
        sidecar = metadata_path_for(path, metadata_root)
        if sidecar is None or not sidecar.is_file():
            continue
        metadata = load_metadata(sidecar)
        weight_by_path[normalize_path_key(path)] = watch_weight_of(metadata)
        action = str((metadata.get("video") or {}).get("action") or "").strip()
        if action:
            action_by_path[normalize_path_key(path)] = action
        image_to_video_by_path[normalize_path_key(path)] = bool(metadata.get("source_image"))
        tags = scene_tags(metadata)
        if tags:
            scene_tags_by_path[normalize_path_key(path)] = tags
        action_key = action_group_key(metadata)
        if action_key is not None:
            action_key_by_path[normalize_path_key(path)] = action_key
            action_members.setdefault(action_key, []).append(path)
        _record_seed_membership(seed_group_key(metadata), path, seed_key_by_path, seed_members)
    for members in (*action_members.values(), *seed_members.values()):
        members.sort()
    return GroupIndex(
        action_key_by_path=action_key_by_path,
        action_members=action_members,
        action_by_path=action_by_path,
        seed_key_by_path=seed_key_by_path,
        seed_members=seed_members,
        path_by_key=path_by_key,
        scene_tags_by_path=scene_tags_by_path,
        image_to_video_by_path=image_to_video_by_path,
        weight_by_path=weight_by_path,
    )


# Sidecar scans cost ~1000 file reads per library, so indexes are cached per
# library key and rebuilt only when a probe path is missing — which is exactly
# what happens when a new arrival starts playing.
_INDEX_CACHE: dict[str, GroupIndex] = {}


def cached_group_index(
    cache_key: str,
    *,
    paths_supplier,
    metadata_root: str | Path | None,
    must_contain: str | None = None,
) -> GroupIndex:
    index = _INDEX_CACHE.get(cache_key)
    if index is None or (must_contain is not None and not index.contains(must_contain)):
        index = build_group_index(paths_supplier(), metadata_root)
        _INDEX_CACHE[cache_key] = index
    return index


def reset_group_index_cache() -> None:
    _INDEX_CACHE.clear()

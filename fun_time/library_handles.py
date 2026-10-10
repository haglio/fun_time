"""The main library as browsable handles — one per video, not per file.

The library on disk is organized by *pipeline stage*, not by content: one video
turns up under ``0 unsorted``, again under ``1 could use work/…``, again under
``3_good_to_go/processed``, each a different trim or upscale of the same scene.
Those folders are the librarian's business, not the viewer's, so browsing them
means knowing which stage a video reached before you can find it at all.

A *handle* is the answer: every rendition of one video collapsed into a single
entry, named after the video rather than after the file.  Evolver records the
family on each video's metadata sidecar (``version.group``), which is the
authority — the names alone cannot say so — and a video with no record simply
stands alone as its own handle.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from player_core.clip_folder import flat_clips_in, vr_clips_in

from .folder_listings import FolderListings
from .media_metadata import (
    EXCERPT,
    load_metadata,
    metadata_path_for,
    normalize_path_key,
    recorded_group,
    video_title,
    video_type_of,
)
from .modes import collect_video_files, source_roots


@dataclass(frozen=True)
class LibraryHandle:
    """One video, however many files it exists as.

    *versions* holds every rendition, largest file first — the same canonical
    ordering the main player's own version cycling walks, so the version a
    handle plays is the one that player would have chosen anyway.
    """

    title: str
    versions: tuple[str, ...]
    # Which band of the browse this sits in — the source folder it came from,
    # and whether it is an excerpt.  See :func:`band_names`.
    section: str = ""

    @property
    def video(self) -> str:
        """The rendition picking this handle plays — the largest, as above."""
        return self.versions[0]

    @property
    def display_name(self) -> str:
        """What the browser labels this row with; a folder answers too."""
        return self.title

    @property
    def previews(self) -> tuple[str, ...]:
        """The stills this row shows — one, where a folder has four."""
        return (self.preview,)

    @property
    def preview(self) -> str:
        """The rendition to take a thumbnail off — the smallest, so cheapest.

        An upscale runs to hundreds of megabytes of HEVC where the original it
        came from is a couple of megabytes of H.264: minutes rather than seconds
        to decode a frame out of, for the same picture.
        """
        return self.versions[-1]


def handle_for(handles: Sequence[LibraryHandle], video: str) -> LibraryHandle | None:
    key = normalize_path_key(video)
    if not key:
        return None
    return next(
        (
            handle
            for handle in handles
            if any(normalize_path_key(version) == key for version in handle.versions)
        ),
        None,
    )


def _payload(video: str, metadata_root: Path | None) -> dict:
    """Everything Evolver recorded about *video*, read once for every answer the
    browse takes off it."""
    sidecar = metadata_path_for(video, metadata_root)
    return {} if sidecar is None else load_metadata(sidecar)


FULL_FOLDER = "full"
CLIPS_FOLDER = "clips"


def source_path(video: str, sources: str) -> tuple[str, ...]:
    """*video*'s folders below whichever library source holds it.

    Empty for a video that is directly in a source root, or under none of them.
    """
    path = Path(video)
    for source in sources.split("|"):
        root = source.strip()
        if not root:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        return relative.parts[:-1]
    return ()


def source_folder(video: str, sources: str) -> str:
    """The folder under a library source that *video* came from.

    The FIRST component below the source root, which is the one thing about a
    library path always worth knowing — which batch or origin a video came from.
    Everything under it is pipeline stage, which is what the browse hides, unless
    :func:`band_names` finds a real division in it.
    """
    folders = source_path(video, sources)
    return folders[0] if folders else ""


def band_names(bands: dict[tuple[str, bool], list[tuple[str, ...]]]) -> dict[tuple[str, bool], str]:
    names = {}
    for (folder, is_clip), paths in bands.items():
        mate = bands.get((folder, not is_clip), [])
        mine = _own_folder(paths, mate)
        if mine:
            names[(folder, is_clip)] = f"{folder}/{mine}"
        elif is_clip:
            names[(folder, is_clip)] = f"{folder}/{CLIPS_FOLDER}"
        elif mate:
            names[(folder, is_clip)] = f"{folder}/{FULL_FOLDER}"
        else:
            names[(folder, is_clip)] = folder
    return names


def _own_folder(band: list[tuple[str, ...]], mate: list[tuple[str, ...]]) -> str:
    mine, theirs = _dominant_subfolder(band), _dominant_subfolder(mate)
    if not mine or not theirs or mine == theirs:
        return ""
    return mine if _holds_mostly(mine, band, mate) and _holds_mostly(theirs, mate, band) else ""


def _holds_mostly(subfolder: str, band: list[tuple[str, ...]], mate: list[tuple[str, ...]]) -> bool:
    return _count_under(subfolder, band) > _count_under(subfolder, mate)


def _count_under(subfolder: str, paths: list[tuple[str, ...]]) -> int:
    return sum(1 for parts in paths if len(parts) > 1 and parts[1] == subfolder)


def _dominant_subfolder(paths: list[tuple[str, ...]]) -> str:
    """The second folder most of *paths* sit under, or "" when they are split
    across several — which is what a set of pipeline stages looks like."""
    seconds = Counter(parts[1] for parts in paths if len(parts) > 1)
    if not seconds:
        return ""
    name, count = seconds.most_common(1)[0]
    return name if count * 2 > sum(seconds.values()) else ""


def cut_folders(
    recorded_excerpts: list[tuple[str, ...]], everything_else: list[tuple[str, ...]]
) -> dict[str, str]:
    def by_source(paths: list[tuple[str, ...]]) -> dict[str, list[tuple[str, ...]]]:
        grouped: dict[str, list[tuple[str, ...]]] = {}
        for path in paths:
            if path:
                grouped.setdefault(path[0], []).append(path)
        return grouped

    cuts, rest = by_source(recorded_excerpts), by_source(everything_else)
    folders = {}
    for folder, paths in cuts.items():
        mine = _own_folder(paths, rest.get(folder, []))
        if mine:
            folders[folder] = mine
    return folders


def is_an_excerpt(path: tuple[str, ...], kind: str, cuts: dict[str, str]) -> bool:
    """Whether a video is a cut — by its own record, or by the company it keeps.

    Evolver's recorded kind settles it, either way: a video it says is an
    excerpt is one, and a video it says is anything else is not, wherever that
    video happens to sit.

    The fallback is for a video it has recorded nothing about — a library it has
    not run over yet.  A source folder which filed its cuts into a folder of
    their own put nothing else in there, so a video sitting among them is one of
    them until something says otherwise.  It reaches only where the librarian
    has already drawn the line on disk: a folder that never separated its cuts
    has no folder here, so nothing of its is reclassified — the pipeline's own
    stage folders can never stand in for a division of the library, which is the
    whole rule this repo browses by.
    """
    if kind:
        return kind == EXCERPT
    return len(path) > 1 and cuts.get(path[0]) == path[1]


def build_library_handles(
    sources: str, metadata_root: Path | None, *, listing=collect_video_files,
) -> list[LibraryHandle]:
    """Every video under *sources*, as one handle per version family.

    Sectioned by where a video came from, biggest section first so the browse
    opens on the bulk of the library, and alphabetical within a section.  Where
    a video sits *below* its source folder says how far it got through the
    pipeline, never what it is, so it never decides where the video turns up.

    A family that spans the excerpt line becomes two handles — see below.
    """
    videos = listing(sources)
    sizes = FolderListings()
    payloads = {video: _payload(video, metadata_root) for video in videos}
    paths = {video: source_path(video, sources) for video in videos}
    # Where each source folder files the cuts Evolver HAS recorded, so the ones
    # it has not reached can be recognized by the company they keep — see
    # :func:`is_an_excerpt`.
    kinds = {video: video_type_of(payloads[video]) for video in videos}
    cuts = cut_folders(
        [paths[video] for video in videos if kinds[video] == EXCERPT],
        [paths[video] for video in videos if kinds[video] != EXCERPT],
    )
    groups = {
        video: recorded_group(payloads[video], video) or Path(video).stem
        for video in videos
    }

    # Keyed by family AND by whether it is an excerpt: Evolver ties a cut to the
    # scene it came out of with the same version.group, but a cut is a *piece* of
    # that scene, not another rendition of it.  Folded together the cut would
    # vanish — the whole scene is the bigger file, so it would take the handle,
    # the name and the folder, leaving the cut reachable only by cycling versions
    # inside a video it is not a version of.
    families: dict[tuple[str, bool], list[str]] = {}
    for video in videos:
        families.setdefault(
            (groups[video], is_an_excerpt(paths[video], kinds[video], cuts)), []
        ).append(video)

    played = {
        family: tuple(sorted(videos, key=lambda video: (-sizes.size(video), video)))
        for family, videos in families.items()
    }
    keys = {
        family: (source_folder(versions[0], sources), family[1])
        for family, versions in played.items()
    }
    bands: dict[tuple[str, bool], list[tuple[str, ...]]] = {}
    for family, key in keys.items():
        bands.setdefault(key, []).append(source_path(played[family][0], sources))
    names = band_names(bands)

    # Folders rank by their whole weight, cuts included, so a folder that was
    # sliced into hundreds of excerpts cannot send those excerpts ahead of a
    # different folder entirely.  Its two bands then stay adjacent, whole videos
    # first: the cuts came out of them, so they follow.  Ordering reads the band
    # key rather than the section name, which is only what the band is *called*.
    weight = Counter(folder for folder, _clip in keys.values())
    shown = {
        family: video_title(payloads[played[family][0]], played[family][0])
        for family in played
    }
    return [
        LibraryHandle(
            title=shown[family], versions=played[family], section=names[keys[family]]
        )
        for family in sorted(
            played,
            key=lambda family: (
                -weight[keys[family][0]], keys[family][0], keys[family][1],
                shown[family].casefold(), shown[family],
            ),
        )
    ]


_VR_FOLDER = "VR"
_FLAT_FOLDER = "2D"


def handles_by_shape(
    sources: str, vr_sources: str, metadata_root: Path | None, *, listing=collect_video_files,
) -> list[LibraryHandle]:
    vr_roots = source_roots(vr_sources)
    flat_sources = "|".join(str(root) for root in source_roots(sources) if root not in vr_roots)
    shelves = [
        (name, handles)
        for name, spec in ((_VR_FOLDER, vr_sources), (_FLAT_FOLDER, flat_sources))
        if (handles := build_library_handles(spec, metadata_root, listing=listing))
    ]
    if len(shelves) < 2:
        return [handle for _name, handles in shelves for handle in handles]
    return [
        replace(handle, section="/".join(part for part in (name, handle.section) if part))
        for name, handles in shelves
        for handle in handles
    ]


def genau_vr_clips(clips_folder: str) -> str:
    return str(vr_clips_in(Path(clips_folder))) if clips_folder else ""


def genau_clip_sources(clips_folder: str) -> str:
    if not clips_folder:
        return ""
    return "|".join((genau_vr_clips(clips_folder), str(flat_clips_in(Path(clips_folder)))))

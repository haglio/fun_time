"""The main library as browsable handles — one per video, not per file.

The library on disk is organised by *pipeline stage*, not by content: one video
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
from dataclasses import dataclass
from pathlib import Path

from .media_metadata import load_metadata, metadata_path_for
from .modes import collect_video_files


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
    def preview(self) -> str:
        """The rendition to take a thumbnail off — the smallest, so cheapest.

        An upscale runs to hundreds of megabytes of HEVC where the original it
        came from is a couple of megabytes of H.264: minutes rather than seconds
        to decode a frame out of, for the same picture.
        """
        return self.versions[-1]


def _recorded_group(video: str, metadata_root: Path | None) -> str | None:
    """The version family Evolver recorded for *video*, or None if it has none."""
    sidecar = metadata_path_for(video, metadata_root)
    if sidecar is None:
        return None
    version = load_metadata(sidecar).get("version")
    if not isinstance(version, dict):
        return None
    group = version.get("group")
    return str(group) if group else None


def _file_size(video: str) -> int:
    try:
        return Path(video).stat().st_size
    except OSError:
        return 0


def _is_clip(video: str, metadata_root: Path | None) -> bool:
    """Whether *video* was carved out of a compilation rather than shot as one.

    Evolver writes a ``clip`` record — the parent compilation, the running order
    within it, the source scene — only on an excerpt, so its presence is the
    whole test.
    """
    sidecar = metadata_path_for(video, metadata_root)
    return sidecar is not None and isinstance(load_metadata(sidecar).get("clip"), dict)


# What marks a section as holding excerpts rather than whole videos.  Structural
# on purpose: the section name is built from the user's own folder names plus
# this, so no word describing what is in the library lives in this repo.
CLIPS_SUFFIX = " · clips"


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
    """What to call each (folder, is-clip) band — its own folder where it has one.

    A folder whose cuts and whole videos have been filed into two folders of
    their own is named after them, so the header and Explorer read the same
    words.  That is recognised by each band having a *dominant* second folder and
    the two differing: where the split is the sidecar's alone the two bands share
    their stage folders, and where a folder holds only whole videos there is no
    second band to differ from — in both of those the folder keeps its own name
    and the cuts, if any, take the suffix.

    Dominant rather than unanimous, so one straggler left behind by a move (a
    file the running session had open) cannot drag a band's name back.
    """
    names = {}
    for (folder, is_clip), paths in bands.items():
        mate = bands.get((folder, not is_clip))
        mine, theirs = _dominant_subfolder(paths), _dominant_subfolder(mate or [])
        if mine and theirs and mine != theirs:
            names[(folder, is_clip)] = f"{folder}/{mine}"
        else:
            names[(folder, is_clip)] = folder + CLIPS_SUFFIX if is_clip else folder
    return names


def _dominant_subfolder(paths: list[tuple[str, ...]]) -> str:
    """The second folder most of *paths* sit under, or "" when they are split
    across several — which is what a set of pipeline stages looks like."""
    seconds = Counter(parts[1] for parts in paths if len(parts) > 1)
    if not seconds:
        return ""
    name, count = seconds.most_common(1)[0]
    return name if count * 2 > sum(seconds.values()) else ""


def build_library_handles(sources: str, metadata_root: Path | None) -> list[LibraryHandle]:
    """Every video under *sources*, as one handle per version family.

    Sectioned by where a video came from, biggest section first so the browse
    opens on the bulk of the library, and alphabetical within a section.  Where
    a video sits *below* its source folder says how far it got through the
    pipeline, never what it is, so it never decides where the video turns up.

    A family that spans the excerpt line becomes two handles — see below.
    """
    # Keyed by family AND by whether it is an excerpt: Evolver ties a cut to the
    # scene it came out of with the same version.group, but a cut is a *piece* of
    # that scene, not another rendition of it.  Folded together the cut would
    # vanish — the whole scene is the bigger file, so it would take the handle,
    # the name and the folder, leaving the cut reachable only by cycling versions
    # inside a video it is not a version of.
    families: dict[tuple[str, bool], list[str]] = {}
    for video in collect_video_files(sources):
        title = _recorded_group(video, metadata_root) or Path(video).stem
        families.setdefault((title, _is_clip(video, metadata_root)), []).append(video)

    played = {
        family: tuple(sorted(videos, key=lambda video: (-_file_size(video), video)))
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
    return [
        LibraryHandle(title=family[0], versions=played[family], section=names[keys[family]])
        for family in sorted(
            played,
            key=lambda family: (
                -weight[keys[family][0]], keys[family][0], keys[family][1],
                family[0].casefold(), family[0],
            ),
        )
    ]

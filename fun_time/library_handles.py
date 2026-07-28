"""The primary library as browsable handles — one per video, not per file.

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
    ordering the primary player's own version cycling walks, so the version a
    handle plays is the one that player would have chosen anyway.
    """

    title: str
    versions: tuple[str, ...]
    # Which band of the browse this sits in — the source folder it came from,
    # and whether it is an excerpt.  See :func:`section_for`.
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


def source_folder(video: str, sources: str) -> str:
    """The folder under a library source that *video* came from.

    The FIRST component below the source root, which is the one thing about a
    library path worth knowing — which batch or origin a video came from.
    Everything under it is pipeline stage, which is what the browse hides.  A
    video sitting directly in a source root has no such folder, and gets "".
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
        return relative.parts[0] if len(relative.parts) > 1 else ""
    return ""


def section_for(video: str, sources: str, metadata_root: Path | None) -> str:
    """Which band of the browse *video* belongs to.

    Its source folder, and — for an excerpt — a band of its own beside that
    folder's whole videos, so a reel's worth of cuts never sits between the
    scenes they came from.
    """
    folder = source_folder(video, sources)
    return folder + CLIPS_SUFFIX if _is_clip(video, metadata_root) else folder


def build_library_handles(sources: str, metadata_root: Path | None) -> list[LibraryHandle]:
    """Every video under *sources*, as one handle per version family.

    Sectioned by where a video came from, biggest section first so the browse
    opens on the bulk of the library, and alphabetical within a section.  Where
    a video sits *below* its source folder says how far it got through the
    pipeline, never what it is, so it never decides where the video turns up.

    A family's section follows the version it plays, not its members at large:
    the odd family holding both an excerpt and the whole scene it came from
    belongs with the whole videos, because that is what picking it plays.
    """
    families: dict[str, list[str]] = {}
    for video in collect_video_files(sources):
        title = _recorded_group(video, metadata_root) or Path(video).stem
        families.setdefault(title, []).append(video)

    handles = [
        LibraryHandle(
            title=title,
            versions=(versions := tuple(
                sorted(families[title], key=lambda video: (-_file_size(video), video))
            )),
            section=section_for(versions[0], sources, metadata_root),
        )
        for title in families
    ]
    # Folders rank by their whole weight, cuts included, so a folder that was
    # sliced into hundreds of excerpts cannot send those excerpts ahead of a
    # different folder entirely.  Its two bands then stay adjacent, whole videos
    # first: the cuts came out of them, so they follow.
    weight = Counter(_folder_of(handle.section) for handle in handles)
    return sorted(
        handles,
        key=lambda handle: (
            -weight[_folder_of(handle.section)],
            _folder_of(handle.section),
            handle.section.endswith(CLIPS_SUFFIX),
            handle.title.casefold(),
            handle.title,
        ),
    )


def _folder_of(section: str) -> str:
    """The source folder a section belongs to — its two bands share one."""
    return section[: -len(CLIPS_SUFFIX)] if section.endswith(CLIPS_SUFFIX) else section

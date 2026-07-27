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


def build_library_handles(sources: str, metadata_root: Path | None) -> list[LibraryHandle]:
    """Every video under *sources*, as one alphabetical handle per version family.

    Alphabetical by title and nothing else: where a video sits in the stage
    folders says how far it got through the pipeline, never what it is, so it
    must not decide where the video turns up in the browse.
    """
    families: dict[str, list[str]] = {}
    for video in collect_video_files(sources):
        title = _recorded_group(video, metadata_root) or Path(video).stem
        families.setdefault(title, []).append(video)
    return [
        LibraryHandle(
            title=title,
            versions=tuple(sorted(families[title], key=lambda video: (-_file_size(video), video))),
        )
        for title in sorted(families, key=lambda title: (title.casefold(), title))
    ]

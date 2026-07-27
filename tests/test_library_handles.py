"""The browsable handles of the primary library — one per version group."""
from __future__ import annotations

import json
from pathlib import Path

from fun_time.library_handles import build_library_handles


def _library(tmp_path: Path) -> tuple[Path, Path]:
    """A ``videos/videos`` library beside its ``videos/metadata`` sidecar tree."""
    videos = tmp_path / "videos" / "videos" / "main"
    metadata = tmp_path / "videos" / "metadata"
    videos.mkdir(parents=True)
    metadata.mkdir(parents=True)
    return videos, metadata


def _video(videos_root: Path, relative: str, *, size: int = 1024) -> Path:
    path = videos_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def _sidecar(metadata_root: Path, video: Path, library_root: Path, group: str) -> None:
    path = (metadata_root / video.relative_to(library_root)).with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": {"group": group}}), encoding="utf-8")


def test_files_sharing_a_recorded_version_group_become_one_handle(tmp_path: Path):
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    trimmed = _video(videos, "1 could use work/3_trimmed/jane doe - scene one_trim.mp4")
    processed = _video(videos, "3_good_to_go/processed/jane doe - scene one_topaz.mp4")
    for video in (trimmed, processed):
        _sidecar(metadata, video, library_root, "jane doe - scene one")

    handles = build_library_handles(str(videos), metadata)

    assert [handle.title for handle in handles] == ["jane doe - scene one"]
    assert set(handles[0].versions) == {str(trimmed), str(processed)}


def test_handles_are_alphabetical_regardless_of_case_or_folder(tmp_path: Path):
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    # Deliberately shelved so the folder walk yields gamma, beta, alpha — the
    # order a folder browser would show, and the one a handle browser must not.
    for relative, group in (
        ("0 unsorted/gamma.mp4", "Gamma Scene"),
        ("1 could use work/beta.mp4", "Beta Scene"),
        ("3_good_to_go/processed/alpha.mp4", "alpha scene"),
    ):
        _sidecar(metadata, _video(videos, relative), library_root, group)

    handles = build_library_handles(str(videos), metadata)

    assert [handle.title for handle in handles] == ["alpha scene", "Beta Scene", "Gamma Scene"]


def test_a_version_group_leads_with_its_largest_file(tmp_path: Path):
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    original = _video(videos, "0 unsorted/beta.mp4", size=2048)
    upscale = _video(videos, "3_good_to_go/processed/beta_upscaled.mp4", size=9000)
    for video in (original, upscale):
        _sidecar(metadata, video, library_root, "Beta Scene")

    handles = build_library_handles(str(videos), metadata)

    assert handles[0].versions == (str(upscale), str(original))


def test_a_video_with_no_recorded_family_stands_alone_under_its_own_name(tmp_path: Path):
    videos, metadata = _library(tmp_path)
    orphan = _video(videos, "0 unsorted/gamma scene.mp4")

    handles = build_library_handles(str(videos), metadata)

    assert [(handle.title, handle.versions) for handle in handles] == [
        ("gamma scene", (str(orphan),)),
    ]


def test_a_handle_plays_its_largest_version_but_pictures_its_smallest(tmp_path: Path):
    """The thumbnail comes off the cheapest rendition to decode.

    An upscale is many times the size of the original it came from — minutes
    rather than seconds to open — for a picture that is the same either way.
    """
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    original = _video(videos, "0 unsorted/beta.mp4", size=2048)
    upscale = _video(videos, "3_good_to_go/processed/beta_upscaled.mp4", size=9000)
    for video in (original, upscale):
        _sidecar(metadata, video, library_root, "Beta Scene")

    handle = build_library_handles(str(videos), metadata)[0]

    assert handle.video == str(upscale)
    assert handle.preview == str(original)

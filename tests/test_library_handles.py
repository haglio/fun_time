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


def _sidecar(
    metadata_root: Path,
    video: Path,
    library_root: Path,
    group: str,
    *,
    carved_from: str = "",
) -> None:
    path = (metadata_root / video.relative_to(library_root)).with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"version": {"group": group}}
    if carved_from:
        payload["clip"] = {"compilation": carved_from, "index": 1, "count": 4}
    path.write_text(json.dumps(payload), encoding="utf-8")


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
    # One source folder, three pipeline stages under it, deliberately shelved so
    # the folder walk yields gamma, beta, alpha — the order a folder browser
    # would show, and the one a handle browser must not.
    for relative, group in (
        ("big_batch/0 unsorted/gamma.mp4", "Gamma Scene"),
        ("big_batch/1 could use work/beta.mp4", "Beta Scene"),
        ("big_batch/3_good_to_go/processed/alpha.mp4", "alpha scene"),
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


def test_handles_are_sectioned_by_source_folder_biggest_first(tmp_path: Path):
    """Videos group under the folder they come from, not the stage folder.

    The top folder under a library source is where a video *came from* — a
    source, a batch — which is the one thing about its path worth knowing; the
    stage folders under it are still hidden.  Sections lead with the biggest, so
    the bulk of the library is where the browse opens.
    """
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    for relative, group in (
        ("small_batch/0 unsorted/alpha.mp4", "alpha scene"),
        ("big_batch/0 unsorted/gamma.mp4", "Gamma Scene"),
        ("big_batch/3_good_to_go/beta.mp4", "Beta Scene"),
    ):
        _sidecar(metadata, _video(videos, relative), library_root, group)

    handles = build_library_handles(str(videos), metadata)

    assert [(handle.section, handle.title) for handle in handles] == [
        ("big_batch", "Beta Scene"),
        ("big_batch", "Gamma Scene"),
        ("small_batch", "alpha scene"),
    ]


def test_clips_carved_from_a_compilation_get_their_own_section(tmp_path: Path):
    """An excerpt is not a shorter version of the library — it is its own thing.

    Evolver marks a video carved out of a compilation with a ``clip`` record, so
    those split off into a section of their own rather than sitting between the
    full videos they were cut from — and behind them, since a folder's whole
    videos are what it is *for*, however many cuts came out of them.
    """
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    full = _video(videos, "big_batch/3_good_to_go/beta.mp4")
    _sidecar(metadata, full, library_root, "Beta Scene")
    for index in range(2):
        clip = _video(videos, f"big_batch/0 unsorted/excerpt{index}.mp4")
        _sidecar(metadata, clip, library_root, f"Excerpt {index}", carved_from="Reel One")

    handles = build_library_handles(str(videos), metadata)

    assert [(handle.section, handle.title) for handle in handles] == [
        ("big_batch", "Beta Scene"),
        ("big_batch · clips", "Excerpt 0"),
        ("big_batch · clips", "Excerpt 1"),
    ]


def test_a_folders_two_sections_stay_together_however_big_each_gets(tmp_path: Path):
    """Section order ranks whole *folders*, not the bands inside them.

    A folder whose cuts outnumber its videos many times over must not have those
    cuts jump the queue over another folder entirely — the folders stay in size
    order, and each one's videos and cuts stay adjacent, videos first.
    """
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    _sidecar(metadata, _video(videos, "big_batch/0 unsorted/beta.mp4"), library_root, "Beta Scene")
    for index in range(4):
        clip = _video(videos, f"big_batch/0 unsorted/excerpt{index}.mp4")
        _sidecar(metadata, clip, library_root, f"Excerpt {index}", carved_from="Reel One")
    for index in range(2):
        other = _video(videos, f"small_batch/0 unsorted/scene{index}.mp4")
        _sidecar(metadata, other, library_root, f"Scene {index}")

    sections = [handle.section for handle in build_library_handles(str(videos), metadata)]

    assert sections == (
        ["big_batch"] + ["big_batch · clips"] * 4 + ["small_batch"] * 2
    )


def test_a_handle_is_a_clip_only_when_the_version_it_plays_is_one(tmp_path: Path):
    """A family recording both an excerpt and the full scene sits with the full
    videos, because that larger version is the one picking it plays."""
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    excerpt = _video(videos, "big_batch/0 unsorted/beta_excerpt.mp4", size=64)
    _sidecar(metadata, excerpt, library_root, "Beta Scene", carved_from="Reel One")
    whole = _video(videos, "big_batch/3_good_to_go/beta.mp4", size=9000)
    _sidecar(metadata, whole, library_root, "Beta Scene")

    handles = build_library_handles(str(videos), metadata)

    assert [(handle.section, handle.video) for handle in handles] == [
        ("big_batch", str(whole)),
    ]

"""The browsable handles of the main player library — one per version group."""
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
    # All in one stage folder: the split is the sidecar's alone, with nothing on
    # disk separating the cuts from the videos they came out of.
    full = _video(videos, "big_batch/0 unsorted/beta.mp4")
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
    # All in one stage folder: the split is the sidecar's alone, with nothing on
    # disk separating the cuts from the videos they came out of.
    full = _video(videos, "big_batch/0 unsorted/beta.mp4")
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


def test_a_band_is_named_after_the_folder_it_was_filed_into(tmp_path: Path):
    """Once the split is on disk, the browse says the folders, not a suffix.

    Two bands filed into two folders of their own are named after those folders,
    so what the header reads and what Explorer shows are the same words.
    """
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    _sidecar(metadata, _video(videos, "big_batch/whole/0 unsorted/beta.mp4"),
             library_root, "Beta Scene")
    for index in range(2):
        clip = _video(videos, f"big_batch/cuts/0 unsorted/excerpt{index}.mp4")
        _sidecar(metadata, clip, library_root, f"Excerpt {index}", carved_from="Reel One")

    handles = build_library_handles(str(videos), metadata)

    assert [(handle.section, handle.title) for handle in handles] == [
        ("big_batch/whole", "Beta Scene"),
        ("big_batch/cuts", "Excerpt 0"),
        ("big_batch/cuts", "Excerpt 1"),
    ]


def test_a_folder_that_was_never_split_keeps_its_own_name(tmp_path: Path):
    """Descending is only right where it separates the bands.

    A folder holding whole videos alone must stay named after itself — the
    folders under it are pipeline stages, and naming a band after one of those
    would put the browse back to reading stage names.
    """
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    for relative in ("small_batch/0 unsorted/alpha.mp4", "small_batch/3_good_to_go/beta.mp4"):
        _sidecar(metadata, _video(videos, relative), library_root, Path(relative).stem)

    sections = {handle.section for handle in build_library_handles(str(videos), metadata)}

    assert sections == {"small_batch"}


def test_a_straggler_left_unfiled_does_not_rename_its_band(tmp_path: Path):
    """One clip still sitting in the old tree must not drag the band's name back.

    A move of hundreds of files can leave one behind — held open by the running
    session — and the band is still, in every sense that matters, the folder the
    rest of it is in.
    """
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    _sidecar(metadata, _video(videos, "big_batch/whole/0 unsorted/beta.mp4"),
             library_root, "Beta Scene")
    for index in range(3):
        clip = _video(videos, f"big_batch/cuts/0 unsorted/excerpt{index}.mp4")
        _sidecar(metadata, clip, library_root, f"Excerpt {index}", carved_from="Reel One")
    left = _video(videos, "big_batch/0 unsorted/excerpt_stuck.mp4")
    _sidecar(metadata, left, library_root, "Excerpt Stuck", carved_from="Reel One")

    sections = {handle.section for handle in build_library_handles(str(videos), metadata)}

    assert sections == {"big_batch/whole", "big_batch/cuts"}


def test_an_excerpt_is_never_folded_into_the_scene_it_was_cut_from(tmp_path: Path):
    """A cut and its source scene share a recorded family, and must not share a
    handle.  Evolver ties an excerpt to the scene it came out of with the same
    ``version.group``, but they are not two renditions of one video — one is a
    piece of the other.  Folded together, the cut vanishes: the whole scene is
    the larger file, so it takes the handle, the title and the folder, and the
    excerpt is left reachable only by cycling versions inside the scene.
    """
    videos, metadata = _library(tmp_path)
    library_root = tmp_path / "videos" / "videos"
    excerpt = _video(videos, "big_batch/cuts/0 unsorted/beta_cut.mp4", size=64)
    _sidecar(metadata, excerpt, library_root, "Beta Scene", carved_from="Reel One")
    whole = _video(videos, "big_batch/whole/3_good_to_go/beta.mp4", size=9000)
    _sidecar(metadata, whole, library_root, "Beta Scene")

    handles = build_library_handles(str(videos), metadata)

    assert [(handle.section, handle.versions) for handle in handles] == [
        ("big_batch/whole", (str(whole),)),
        ("big_batch/cuts", (str(excerpt),)),
    ]

from __future__ import annotations

import os
from pathlib import Path

from fun_time.folder_listings import FolderListings
from tests.drive_fakes import files_that_never_answer, folders_listed


def _clip(folder: Path, name: str, *, size: int, modified: float) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / name
    video.write_bytes(b"x" * size)
    os.utime(video, (modified, modified))
    return video


def test_a_file_s_size_and_date_come_from_its_folder(tmp_path: Path):
    video = _clip(tmp_path / "vids", "clip.mp4", size=7, modified=1000)

    with files_that_never_answer(tmp_path / "vids"):
        listings = FolderListings()

        assert listings.size(video) == 7
        assert listings.modified(video) == 1000


def test_a_folder_is_listed_once_however_many_of_its_files_are_asked_about(tmp_path: Path):
    """The map under the main player asks about a dozen clips each time it is
    drawn, and the browse about hundreds -- one listing has to serve them all."""
    folder = tmp_path / "vids"
    videos = [_clip(folder, f"clip{index}.mp4", size=index + 1, modified=1000 + index)
              for index in range(5)]
    listings = FolderListings()

    with folders_listed() as read:
        sizes = [listings.size(video) for video in videos]

    assert sizes == [1, 2, 3, 4, 5]
    assert len(read) == 1


def test_a_video_no_longer_in_its_folder_has_no_size_or_date(tmp_path: Path):
    listings = FolderListings()

    assert listings.size(tmp_path / "vids" / "gone.mp4") == 0
    assert listings.modified(tmp_path / "vids" / "gone.mp4") == 0.0


def test_each_folder_of_a_library_is_listed_in_its_turn(tmp_path: Path):
    one = _clip(tmp_path / "vids", "clip.mp4", size=3, modified=1000)
    two = _clip(tmp_path / "vids" / "deeper", "other.mp4", size=9, modified=2000)

    with files_that_never_answer(tmp_path / "vids"):
        listings = FolderListings()

        assert (listings.size(one), listings.size(two)) == (3, 9)
        assert (listings.modified(one), listings.modified(two)) == (1000, 2000)

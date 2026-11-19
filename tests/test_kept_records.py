from __future__ import annotations

import json
import os
from pathlib import Path

from fun_time.kept_records import records_of
from fun_time.media_metadata import metadata_path_for
from tests.disk_looks import DiskLooks


def _library(tmp_path: Path, count: int) -> tuple[list[str], Path]:
    videos_folder = tmp_path / "videos" / "videos" / "room"
    metadata_root = tmp_path / "videos" / "metadata"
    videos = []
    for number in range(count):
        video = videos_folder / f"video {number}.mp4"
        sidecar = metadata_path_for(video, metadata_root)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"title": f"title {number}"}), encoding="utf-8")
        videos.append(str(video))
    return videos, metadata_root


def test_records_read_once_are_not_opened_again_while_they_stay_as_they_were(tmp_path, monkeypatch):
    videos, metadata_root = _library(tmp_path, 5)
    kept = tmp_path / "state" / "kept_records.json"
    first = records_of(videos, metadata_root, kept=kept)
    looks = DiskLooks(monkeypatch, under=metadata_root)

    again = records_of(videos, metadata_root, kept=kept)

    assert (again, looks.made["open"], looks.made["scandir"]) == (first, 0, 1)


def test_a_record_that_changed_is_read_again(tmp_path):
    videos, metadata_root = _library(tmp_path, 2)
    kept = tmp_path / "state" / "kept_records.json"
    records_of(videos, metadata_root, kept=kept)
    changed = metadata_path_for(videos[1], metadata_root)
    changed.write_text(json.dumps({"title": "a longer title than before"}), encoding="utf-8")
    os.utime(changed, (2_000_000_000, 2_000_000_000))

    again = records_of(videos, metadata_root, kept=kept)

    assert [again[video]["title"] for video in videos] == ["title 0", "a longer title than before"]


def test_a_video_with_no_record_has_an_empty_one_and_nothing_is_kept_without_a_place(tmp_path):
    videos, metadata_root = _library(tmp_path, 1)
    stranger = str(tmp_path / "videos" / "videos" / "room" / "no record.mp4")

    read = records_of([*videos, stranger], metadata_root, kept=None)

    assert (read[stranger], read[videos[0]]["title"]) == ({}, "title 0")

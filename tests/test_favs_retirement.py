"""favs.csv retires once the records say which videos are favorites and Evolver reads them there."""
from __future__ import annotations

import json
from pathlib import Path

from app_support.favorites import is_favorite, page_of

from fun_time.favs_retirement import listed_favorites, main, retire
from fun_time.media_metadata import load_metadata, mark_favorite, metadata_path_for

PAGE = "https://example.com/gallery/101"


def _library(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"


def _video(library: Path, name: str) -> Path:
    video = library / "2D" / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    return video


def _favs(tmp_path: Path, *rows: tuple[Path, str]) -> Path:
    favs = tmp_path / "favs.csv"
    lines = ["local_file,web_url"]
    for video, page in rows:
        uri = "file:///" + str(video).replace("\\", "/").replace(" ", "%20")
        web = f'"=HYPERLINK(""{page}"";""{page}"")"' if page else '""'
        lines.append(f'"=HYPERLINK(""{uri}"";""{video}"")",{web}')
    favs.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return favs


def _contract(tmp_path: Path, *, written_by_the_reader: list[str] | None) -> Path:
    record: dict = {"top_level_keys": ["title", "favorite"]}
    if written_by_the_reader is not None:
        record["top_level_written_by_the_reader"] = written_by_the_reader
    contract = tmp_path / "evolver" / "evolver_contract.json"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(json.dumps({"library_record": record}), encoding="utf-8")
    return contract


def _record(video: Path, metadata_root: Path) -> dict:
    return load_metadata(metadata_path_for(video, metadata_root))


def test_a_listed_favorite_is_its_video_and_the_page_it_was_listed_with(tmp_path: Path):
    library, _records = _library(tmp_path)
    video = _video(library, "scene one.mp4")

    assert listed_favorites(_favs(tmp_path, (video, PAGE))) == [(str(video), PAGE)]


def test_a_row_with_no_page_is_listed_without_one(tmp_path: Path):
    library, _records = _library(tmp_path)
    video = _video(library, "scene one.mp4")

    assert listed_favorites(_favs(tmp_path, (video, ""))) == [(str(video), "")]


def test_the_page_a_favorite_was_listed_with_is_carried_onto_its_record(tmp_path: Path):
    library, records = _library(tmp_path)
    video = _video(library, "scene one.mp4")
    mark_favorite(video, records, True)

    report = retire(_favs(tmp_path, (video, PAGE)), records,
                    _contract(tmp_path, written_by_the_reader=None))

    assert page_of(_record(video, records)) == PAGE
    assert (report.rows, report.marked, report.pages_carried) == (1, 1, 1)


def test_a_row_whose_record_is_no_favorite_any_more_stays_no_favorite(tmp_path: Path):
    """The phone took it back after Evolver stopped copying favs.csv onto the records."""
    library, records = _library(tmp_path)
    video = _video(library, "scene two.mp4")

    report = retire(_favs(tmp_path, (video, PAGE)), records,
                    _contract(tmp_path, written_by_the_reader=None))

    assert not is_favorite(_record(video, records))
    assert (report.marked, report.unmarked) == (0, 1)


def test_a_favorite_made_since_and_never_listed_is_counted(tmp_path: Path):
    library, records = _library(tmp_path)
    listed, made_since = _video(library, "scene one.mp4"), _video(library, "scene two.mp4")
    for video in (listed, made_since):
        mark_favorite(video, records, True)

    report = retire(_favs(tmp_path, (listed, "")), records,
                    _contract(tmp_path, written_by_the_reader=None))

    assert report.marked_unlisted == 1


def test_favs_csv_stays_while_evolver_still_copies_it_onto_the_records(tmp_path: Path):
    library, records = _library(tmp_path)
    favs = _favs(tmp_path, (_video(library, "scene one.mp4"), ""))

    report = retire(favs, records, _contract(tmp_path, written_by_the_reader=None))

    assert favs.exists() and not report.deleted


def test_favs_csv_stays_when_evolvers_contract_cannot_be_read(tmp_path: Path):
    library, records = _library(tmp_path)
    favs = _favs(tmp_path, (_video(library, "scene one.mp4"), ""))

    assert not retire(favs, records, tmp_path / "evolver" / "missing.json").deleted
    assert favs.exists()


def test_favs_csv_goes_once_evolver_leaves_the_favorite_to_the_apps_that_mark_it(tmp_path: Path):
    library, records = _library(tmp_path)
    favs = _favs(tmp_path, (_video(library, "scene one.mp4"), ""))

    report = retire(favs, records,
                    _contract(tmp_path, written_by_the_reader=["favorite", "web_url"]))

    assert report.deleted and not favs.exists()


def test_the_command_says_what_it_did_in_counts(tmp_path: Path, capsys):
    library, records = _library(tmp_path)
    video = _video(library, "scene one.mp4")
    mark_favorite(video, records, True)
    favs = _favs(tmp_path, (video, PAGE))

    assert main([str(favs), str(records),
                 str(_contract(tmp_path, written_by_the_reader=["favorite", "web_url"]))]) == 0

    said = capsys.readouterr().out
    assert "1 row(s)" in said and "deleted" in said and str(video) not in said

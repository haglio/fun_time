from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app_support import haglio_settings, kino_folder

from fun_time.gallery import (
    CONTRACT_FILE,
    Gallery,
    ai_videos_of_the_library,
    gallery_database_in,
    star_in_the_gallery,
    the_librarys_origenerator,
)
from tests.made_up_gallery import DATABASE, a_row, made_up_gallery, made_up_origenerator


def _kino_copy(tmp_path: Path, name: str) -> Path:
    return tmp_path / "kino" / "2D" / "AI" / "portrait" / "made up site" / name


def test_a_video_is_known_by_the_row_that_made_it(tmp_path):
    gallery = Gallery.read(made_up_gallery(
        tmp_path, a_row("gen-1", "alpha_00007_.mp4", starred=1, web_url="https://example.com/a",
                        positive_prompt="made up words"),
        a_row("gen-2", "beta_00007_.mp4", starred=0)))

    starred = _kino_copy(tmp_path, "alpha_00007__topaz.mp4")
    assert gallery.is_starred(starred)
    assert gallery.page_of(starred) == "https://example.com/a"
    assert gallery.prompt_of(starred) == "made up words"
    assert not gallery.is_starred(_kino_copy(tmp_path, "beta_00007__topaz.mp4"))


def test_a_video_no_row_made_is_unstarred_with_no_page_or_prompt(tmp_path):
    gallery = Gallery.read(made_up_gallery(tmp_path, a_row("gen-1", "alpha_00007_.mp4", starred=1)))

    stranger = _kino_copy(tmp_path, "gamma_00001__topaz.mp4")
    assert (gallery.is_starred(stranger), gallery.page_of(stranger), gallery.prompt_of(stranger)) \
        == (False, "", "")


def test_an_older_gallery_without_the_optional_columns_still_reads(tmp_path):
    db_path = tmp_path / "older.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE generations (prompt_id, output_files, positive_prompt, seed)")
    connection.execute("INSERT INTO generations VALUES (?, ?, ?, ?)",
                       ("gen-1", json.dumps([{"filename": "alpha_00007_.mp4"}]), "words", 7))
    connection.commit()
    connection.close()

    assert not Gallery.read(db_path).is_starred(_kino_copy(tmp_path, "alpha_00007_.mp4"))


def test_no_gallery_or_a_missing_one_knows_no_video(tmp_path):
    for gallery in (Gallery.read(None), Gallery.read(tmp_path / "nowhere.db")):
        assert not gallery.is_starred(_kino_copy(tmp_path, "alpha_00007_.mp4"))


def _settings_with_projects_in(tmp_path: Path, monkeypatch) -> Path:
    projects = tmp_path / "projects"
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({**haglio_settings.EXAMPLE, "project_roots": [str(projects)]}),
                        encoding="utf-8")
    monkeypatch.setenv(haglio_settings.SETTINGS_ENV, str(settings))
    return projects


def test_the_librarys_origenerator_is_the_one_in_the_haglio_projects(tmp_path, monkeypatch):
    projects = _settings_with_projects_in(tmp_path, monkeypatch)
    (projects / "origenerator").mkdir(parents=True)

    assert the_librarys_origenerator() == projects / "origenerator"


def test_the_gallery_is_where_its_origenerator_says_it_keeps_it(tmp_path):
    checkout = made_up_origenerator(tmp_path)

    assert gallery_database_in(checkout) == checkout / DATABASE


def test_an_origenerator_that_publishes_no_gallery_has_none(tmp_path):
    (tmp_path / "bare").mkdir()
    (tmp_path / "odd").mkdir()
    (tmp_path / "odd" / CONTRACT_FILE).write_text("[]", encoding="utf-8")

    assert [gallery_database_in(place) for place in (None, tmp_path / "bare", tmp_path / "odd")] == [
        None, None, None]


def test_a_star_is_asked_of_the_command_origenerator_publishes_for_it(tmp_path):
    checkout = made_up_origenerator(tmp_path)
    started = []

    star_in_the_gallery(tmp_path / "scene one.mp4", checkout,
                        popen=lambda argv, **kwargs: started.append((argv, kwargs["cwd"])))

    assert started == [([str(checkout / ".venv/Scripts/python.exe"), "-m",
                         "origenerator.ai_video_records", "favorite", "--video",
                         str(tmp_path / "scene one.mp4"), "--on"], str(checkout))]


def test_no_star_is_asked_of_an_origenerator_that_publishes_no_way_to_give_one(tmp_path):
    started = []
    (tmp_path / "bare").mkdir()

    for checkout in (None, tmp_path / "bare"):
        star_in_the_gallery(tmp_path / "scene one.mp4", checkout,
                            popen=lambda *args, **kwargs: started.append(args))

    assert started == []


def test_the_librarys_ai_videos_are_the_ones_in_kinos_and_genaus_ai_folders(genaus_folder):
    kino = kino_folder.kino_dir()
    wanted = [kino_folder.upscales_in(kino) / "portrait" / "made up site" / "scene one.mp4",
              genaus_folder / "flicks" / "2D" / "AI" / "scene two.mp4"]
    unwanted = [kino / "2D" / "non_AI" / "scene three.mp4",
                genaus_folder / "flicks" / "2D" / "non_AI" / "scene four.mp4",
                genaus_folder / "flicks" / "VR" / "scene five.mp4",
                genaus_folder / "flicks" / "2D" / "AI" / "scene six.partial.mp4",
                genaus_folder / "flicks" / "2D" / "AI" / "notes.txt"]
    for video in (*wanted, *unwanted):
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"")

    assert sorted(ai_videos_of_the_library()) == sorted(wanted)


def test_an_origenerator_that_cannot_be_started_leaves_the_star_unasked(tmp_path, caplog):
    def refuses(*_args, **_kwargs):
        raise FileNotFoundError("no interpreter there")

    star_in_the_gallery(tmp_path / "scene one.mp4", made_up_origenerator(tmp_path), popen=refuses)

    assert "could not be asked to star" in caplog.text

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePath

from app_support import gallery_rows, haglio_settings, kino_folder, siblings
from app_support.gallery_records import FLIPPED, GENAU_SECTION, RECORD_COLUMNS, GalleryReading
from app_support.subprocess_utils import hidden_subprocess_kwargs

from .modes import BEING_WRITTEN_MARK

logger = logging.getLogger(__name__)

CONTRACT_FILE = "origenerator_gallery_contract.json"
_COLUMNS = ("prompt_id", "output_files")
_OPTIONAL = (*(column for column in RECORD_COLUMNS if column not in _COLUMNS), "web_url")


def ai_videos_of_the_library() -> list[Path]:
    folders = (kino_folder.upscales_in(kino_folder.kino_dir()), Path(haglio_settings.value("genau_dir")))
    return [video for folder in folders if folder.is_dir() for video in sorted(_files_under(folder))
            if gallery_rows.is_video_name(video.name) and BEING_WRITTEN_MARK not in video.name.lower()
            and kino_folder.is_ai_video(video)]


def _files_under(folder: Path) -> list[Path]:
    return [Path(walked, name) for walked, _folders, files in os.walk(folder) for name in files]


def the_librarys_origenerator() -> Path:
    return siblings.project_dir("origenerator", haglio_settings.project_roots())


def _published_by(checkout: Path | None) -> dict:
    if checkout is None:
        return {}
    try:
        published = json.loads((Path(checkout) / CONTRACT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return published if isinstance(published, dict) else {}


def gallery_database_in(checkout: Path | None) -> Path | None:
    kept_at = _published_by(checkout).get("database_path")
    return Path(checkout) / kept_at if checkout is not None and isinstance(kept_at, str) else None


def star_in_the_gallery(video: str | Path, checkout: Path | None, *,
                        popen: Callable[..., object] = subprocess.Popen) -> None:
    _ask_the_gallery(checkout, "favorite", video, True, popen=popen)


def unstar_in_the_gallery(video: str | Path, checkout: Path | None, *,
                          popen: Callable[..., object] = subprocess.Popen) -> None:
    _ask_the_gallery(checkout, "favorite", video, False, popen=popen)


def _ask_the_gallery(checkout: Path | None, verb: str, video: str | Path, on: bool, *,
                     popen: Callable[..., object]) -> None:
    command = _published_by(checkout).get("records_command")
    if checkout is None or not isinstance(command, list) or not command:
        logger.warning("No Origenerator publishes a way to record %s of %s in its gallery",
                       verb, video)
        return
    interpreter, *module = command
    try:
        popen([str(Path(checkout) / interpreter), *module, verb, "--video", str(video),
               "--on" if on else "--off"],
              cwd=str(checkout), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, **hidden_subprocess_kwargs())
    except OSError as error:
        logger.warning("Origenerator at %s could not be asked to record %s of %s (%s)",
                       checkout, verb, video, error)


def the_librarys_gallery() -> Gallery:
    return the_gallery_in(the_librarys_origenerator())


def the_gallery_in(checkout: Path | None) -> Gallery:
    return Gallery.read(gallery_database_in(checkout), checkout=checkout)


class Gallery:
    def __init__(self, rows: list[dict], *, checkout: Path | None = None) -> None:
        self._reading = GalleryReading(rows)
        self.checkout = checkout

    @classmethod
    def read(cls, db_path: Path | None, *, checkout: Path | None = None) -> Gallery:
        if db_path is None:
            return cls([], checkout=checkout)
        try:
            return cls(gallery_rows.rows(_COLUMNS, db_path=db_path, optional=_OPTIONAL),
                       checkout=checkout)
        except (FileNotFoundError, sqlite3.Error) as error:
            logger.warning("Could not read Origenerator's gallery at %s (%s), so no AI video "
                           "is known to it", db_path, error)
            return cls([], checkout=checkout)

    def _row(self, video: str | Path) -> dict:
        return self._reading.row_of(video) or {}

    def is_starred(self, video: str | Path) -> bool:
        return bool(self._row(video).get("starred"))

    def page_of(self, video: str | Path) -> str:
        return str(self._row(video).get("web_url") or "")

    def record_of(self, video: str | Path) -> dict:
        row = self._reading.row_of(video)
        return {} if row is None else self._reading.record_of(row, where=PurePath(video))


def _last_written(db_path: Path | None) -> tuple[float, ...]:
    if db_path is None:
        return ()
    stamps = []
    for path in (db_path, db_path.with_name(db_path.name + "-wal")):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            stamps.append(0.0)
    return tuple(stamps)


_SET_WHEN_A_ROW_IS_MADE = frozenset({"positive_prompt", "negative_prompt", "params_json"})


def what_fun_time_reads(db_path: Path | None) -> tuple | None:
    if db_path is None:
        return None
    try:
        conn = gallery_rows.connect_read_only(db_path)
    except (OSError, sqlite3.Error):
        return None
    try:
        present = {column[1] for column in conn.execute("PRAGMA table_info(generations)")}
        read = [column for column in (*_COLUMNS, *_OPTIONAL)
                if column in present and column not in _SET_WHEN_A_ROW_IS_MADE]
        sums = [f"total(octet_length({column})), "
                f"total(CASE WHEN typeof({column}) IN ('integer', 'real') THEN {column} END)"
                for column in read]
        return conn.execute(
            f"SELECT count(*), max(rowid), {', '.join(sums)} FROM generations").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()


class GalleryKeptOffTheLoop:
    def __init__(self, db_path: Path | None, *,
                 read: Callable[[Path | None], Gallery] = Gallery.read,
                 changed_at: Callable[[Path | None], object] = _last_written,
                 what_it_reads: Callable[[Path | None], object] = what_fun_time_reads) -> None:
        self._db_path = db_path
        self._read = read
        self._changed_at = changed_at
        self._what_it_reads = what_it_reads
        self._kept: Gallery | None = None
        self._read_as_of: object = None
        self._read_what: object = None

    def latest(self) -> Gallery:
        kept = self._kept
        if kept is None:
            self.look()
            kept = self._kept
        return kept

    def look(self) -> None:
        as_of = self._changed_at(self._db_path)
        if self._kept is not None and as_of == self._read_as_of:
            return
        what = self._what_it_reads(self._db_path)
        self._read_as_of = as_of
        if self._kept is not None and what is not None and what == self._read_what:
            return
        self._kept = self._read(self._db_path)
        self._read_what = what


class GalleryFlips:
    def __init__(self, gallery: Gallery, *, popen: Callable[..., object] = subprocess.Popen) -> None:
        self._gallery, self._popen = gallery, popen
        self._flipped_since: dict[Path, bool] = {}

    @staticmethod
    def keeps(flick: Path) -> bool:
        return kino_folder.is_ai_video(flick)

    def is_flipped(self, flick: Path) -> bool:
        if flick in self._flipped_since:
            return self._flipped_since[flick]
        return self._gallery.record_of(flick).get(GENAU_SECTION, {}).get(FLIPPED) is True

    def keep(self, flick: Path, flipped: bool) -> None:
        self._flipped_since[flick] = flipped
        _ask_the_gallery(self._gallery.checkout, "flipped", flick, flipped, popen=self._popen)

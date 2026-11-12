from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

from app_support import gallery_rows, haglio_settings, kino_folder, siblings
from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.watch_weights import COUNT_FIELDS

logger = logging.getLogger(__name__)

CONTRACT_FILE = "origenerator_gallery_contract.json"
_COLUMNS = ("prompt_id", "output_files", "positive_prompt", "seed")
_WATCH_COLUMNS = tuple(f"watch_{field}" for field in COUNT_FIELDS)
_OPTIONAL = ("starred", "evolver_upscale", "web_url", *_WATCH_COLUMNS)


def ai_videos_of_the_library() -> list[Path]:
    folders = (kino_folder.upscales_in(kino_folder.kino_dir()), Path(haglio_settings.value("genau_dir")))
    return [video for folder in folders if folder.is_dir() for video in sorted(folder.rglob("*"))
            if gallery_rows.is_video_name(video.name) and ".partial." not in video.name
            and kino_folder.is_ai_video(video) and video.is_file()]


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
    command = _published_by(checkout).get("records_command")
    if checkout is None or not isinstance(command, list) or not command:
        logger.warning("No Origenerator publishes a way to star %s in its gallery", video)
        return
    interpreter, *module = command
    try:
        popen([str(Path(checkout) / interpreter), *module, "favorite", "--video", str(video), "--on"],
              cwd=str(checkout), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, **hidden_subprocess_kwargs())
    except OSError as error:
        logger.warning("Origenerator at %s could not be asked to star %s (%s)", checkout, video, error)


def the_librarys_gallery() -> Gallery:
    return Gallery.read(gallery_database_in(the_librarys_origenerator()))


class Gallery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = {row["prompt_id"]: row for row in rows}
        self._row_of = gallery_rows.VideoRows(rows)

    @classmethod
    def read(cls, db_path: Path | None) -> Gallery:
        if db_path is None:
            return cls([])
        try:
            return cls(gallery_rows.rows(_COLUMNS, db_path=db_path, optional=_OPTIONAL))
        except (FileNotFoundError, sqlite3.Error) as error:
            logger.warning("Could not read Origenerator's gallery at %s (%s), so no AI video "
                           "is known to it", db_path, error)
            return cls([])

    def _row(self, video: str | Path) -> dict:
        return self._rows.get(self._row_of(video) or "", {})

    def is_starred(self, video: str | Path) -> bool:
        return bool(self._row(video).get("starred"))

    def page_of(self, video: str | Path) -> str:
        return str(self._row(video).get("web_url") or "")

    def prompt_of(self, video: str | Path) -> str:
        return str(self._row(video).get("positive_prompt") or "")

    def seed_of(self, video: str | Path) -> str:
        return str(self._row(video).get("seed") or "")

    def watch_counts(self, video: str | Path) -> dict[str, int] | None:
        row = self._row(video)
        if all(row.get(column) is None for column in _WATCH_COLUMNS):
            return None
        return {field: int(row.get(column) or 0)
                for field, column in zip(COUNT_FIELDS, _WATCH_COLUMNS, strict=True)}

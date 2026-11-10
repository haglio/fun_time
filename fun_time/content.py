from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app_support import haglio_settings
from app_support.overlay import backfilled

_PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_CONTENT = _PROJECT_DIR / "content.local.json"
EXAMPLE_CONTENT = _PROJECT_DIR / "content.example.json"

_TEMPLATES_WRITTEN_INTO_THE_FAVORITES_FILE: dict[str, Any] = {"web_providers": []}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as unparsable:
        raise ValueError(f"{path}: {unparsable}") from unparsable


@functools.cache
def load_content(
    local_path: Path = LOCAL_CONTENT,
    example_path: Path = EXAMPLE_CONTENT,
) -> dict[str, Any]:
    example = _read_json(example_path)
    if not local_path.exists():
        return example
    return backfilled(_read_json(local_path), example, empty_when_absent=_TEMPLATES_WRITTEN_INTO_THE_FAVORITES_FILE)


@dataclass(frozen=True)
class Noun:
    one: str
    many: str


def genau_flick_noun() -> Noun:
    words = haglio_settings.value("genau_flick_words")
    return Noun(one=words["one"], many=words["many"])


def no_other_flicks() -> str:
    return f"Including no other {genau_flick_noun().many}"


@dataclass(frozen=True)
class WebProvider:
    marker: str
    gallery_url: str

    def serves(self, url: str) -> bool:
        return urlparse(url).hostname == urlparse(self.gallery_url).hostname


def provider_that_made(video_path: str | Path, providers: tuple[WebProvider, ...]) -> WebProvider | None:
    folders = str(video_path).replace("/", "\\").lower()
    return next((provider for provider in providers if f"\\{provider.marker}\\" in folders), None)


@functools.cache
def load_web_providers(
    local_path: Path = LOCAL_CONTENT,
    example_path: Path = EXAMPLE_CONTENT,
) -> tuple[WebProvider, ...]:
    data = load_content(local_path, example_path)
    return tuple(
        WebProvider(marker=entry["marker"], gallery_url=entry["gallery_url"])
        for entry in data.get("web_providers", ())
    )

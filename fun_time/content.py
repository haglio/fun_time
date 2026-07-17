"""Shared access to the JSON content overlay.

Real copy lives in the git-ignored ``content.local.json``; the committed
``content.example.json`` is a tame placeholder used on a fresh or public
checkout.  Modules that read user-facing content (the spoken act vocabulary in
:mod:`fun_time.filter_vocab`, the provider gallery URLs in
:mod:`fun_time.media_actions`) load it through here, so the overlay location is
defined in exactly one place.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_CONTENT = _PROJECT_DIR / "content.local.json"
EXAMPLE_CONTENT = _PROJECT_DIR / "content.example.json"


def load_content(
    local_path: Path = LOCAL_CONTENT,
    example_path: Path = EXAMPLE_CONTENT,
) -> dict[str, Any]:
    """The content overlay dict — the local override if present, else the example."""
    path = local_path if local_path.exists() else example_path
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class WebProvider:
    """A media provider's library-folder marker and gallery-URL template.

    ``marker`` is the folder name that tags a clip as this provider's (matched as
    ``\\<marker>\\`` in a path); ``gallery_url`` is a template taking ``{id}``,
    the image id parsed from the filename.
    """

    marker: str
    gallery_url: str


def load_web_providers(
    local_path: Path = LOCAL_CONTENT,
    example_path: Path = EXAMPLE_CONTENT,
) -> tuple[WebProvider, ...]:
    """Provider gallery rules from the overlay, or ``()`` when none are set."""
    data = load_content(local_path, example_path)
    return tuple(
        WebProvider(marker=entry["marker"], gallery_url=entry["gallery_url"])
        for entry in data.get("web_providers", ())
    )


# Loaded once at import; the git-ignored overlay's real providers, or the
# example's tame placeholders on a fresh/public checkout.
WEB_PROVIDERS: tuple[WebProvider, ...] = load_web_providers()

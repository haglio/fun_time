"""Lazy-load landing pages for Random Favs Browser tabs.

A tab first lands on a tiny local page that holds its real destination, shows
the clip you might want to recreate, and navigates on the first reload (Ctrl+R)
or click.  The RFB opens ten favorites at once and the lock hotkey opens one
mid-session; both go through here rather than loading a heavy generate page
straight away.

The destination cannot ride on Chrome's command line.  A provider regenerate URL
carries a whole prompt set in its ``#ft=`` fragment — up to ~4 KB — and ten of
those overflow the 32,767-character ceiling ``CreateProcess`` puts on a command
line, which fails the launch outright (``WinError 206``).  So the destination is
baked into a generated page per tab and Chrome is handed short ``file://`` URIs.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .loopback_server import omnipause_url

_TEMPLATE_PATH = Path(__file__).resolve().parent / "static" / "tab_page_template.html"
_PAGE_GLOB = "*.html"


@dataclass(frozen=True)
class TabTarget:
    """Where a tab should land, how to name it, and the clip it came from."""

    url: str
    label: str
    video_path: str = ""


def tabs_dir(state_dir: str | Path) -> Path:
    """The directory holding this session's generated tab pages."""
    return Path(state_dir) / "rfb_tabs"


def _js_string(value: str) -> str:
    """Render *value* as a JavaScript string literal, safe inside ``<script>``.

    ``json.dumps`` handles quotes and backslashes; escaping ``<`` additionally
    stops a ``</script>`` inside a URL or label from closing the block early.
    """
    return json.dumps(value).replace("<", "\\u003c")


def _file_uri(video_path: str) -> str:
    """The ``file://`` URI for *video_path*, or "" when there is nothing to show."""
    if not video_path:
        return ""
    path = Path(video_path)
    if not path.is_absolute():
        return ""
    return path.as_uri()


def render_tab_page(target: TabTarget) -> str:
    """Render the landing page that defers loading *target* until it is triggered."""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    for token, value in (
        ('"__FT_TARGET__"', target.url),
        ('"__FT_LABEL__"', target.label),
        ('"__FT_VIDEO__"', _file_uri(target.video_path)),
        ('"__FT_OMNIPAUSE__"', omnipause_url()),
    ):
        template = template.replace(token, _js_string(value))
    return template


def _write_page(page: Path, target: TabTarget) -> str:
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(render_tab_page(target), encoding="utf-8")
    return page.as_uri()


def write_tab_pages(pages_dir: Path, targets: Sequence[TabTarget]) -> list[str]:
    """Write this session's tab pages into *pages_dir*, returning their file URIs.

    Every page from the previous session is removed first — the startup pages
    name favorites this session did not pick, and the lock pages name videos
    nobody is looking at any more.  Nothing else ever cleans them up.
    """
    pages_dir.mkdir(parents=True, exist_ok=True)
    for stale in pages_dir.glob(_PAGE_GLOB):
        stale.unlink()

    return [
        _write_page(pages_dir / f"tab_{index:02d}.html", target)
        for index, target in enumerate(targets, start=1)
    ]


def write_lock_tab_page(pages_dir: Path, target: TabTarget) -> str:
    """Write the page for a video locked mid-session, returning its file URI.

    Named after the destination, so locking the same video twice rewrites one
    page instead of littering the directory, and so it can never collide with
    the ``tab_NN`` pages the session start laid down.
    """
    digest = hashlib.sha1(target.url.encode("utf-8")).hexdigest()[:12]
    return _write_page(pages_dir / f"lock_{digest}.html", target)

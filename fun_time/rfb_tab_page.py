"""Lazy-load landing pages for Random Favs Browser tabs.

The RFB opens ten favourites at once, so each tab first lands on a tiny local
page that holds its real destination and navigates there on the first reload
(Ctrl+R) or click.

The destination cannot ride on Chrome's command line.  A Provider regenerate URL
carries a whole prompt set in its ``#ft=`` fragment — up to ~4 KB — and ten of
those overflow the 32,767-character ceiling ``CreateProcess`` puts on a command
line, which fails the launch outright (``WinError 206``).  So the destination is
baked into a generated page per tab and Chrome is handed short ``file://`` URIs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .random_favs_browser import FavTarget

_TEMPLATE_PATH = Path(__file__).resolve().parent / "static" / "tab_page_template.html"
_TAB_GLOB = "tab_*.html"


def _js_string(value: str) -> str:
    """Render *value* as a JavaScript string literal, safe inside ``<script>``.

    ``json.dumps`` handles quotes and backslashes; escaping ``<`` additionally
    stops a ``</script>`` inside a URL or label from closing the block early.
    """
    return json.dumps(value).replace("<", "\\u003c")


def render_tab_page(url: str, label: str) -> str:
    """Render the landing page that defers loading *url* until it is triggered."""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace('"__FT_TARGET__"', _js_string(url)).replace(
        '"__FT_LABEL__"', _js_string(label)
    )


def write_tab_pages(tabs_dir: Path, targets: Sequence[FavTarget]) -> list[str]:
    """Write one landing page per target into *tabs_dir*, returning their file URIs.

    Pages from the previous session are removed first: they name favourites this
    session did not pick, and nothing else ever cleans them up.
    """
    tabs_dir.mkdir(parents=True, exist_ok=True)
    for stale in tabs_dir.glob(_TAB_GLOB):
        stale.unlink()

    uris: list[str] = []
    for index, target in enumerate(targets, start=1):
        page = tabs_dir / f"tab_{index:02d}.html"
        page.write_text(render_tab_page(target.url, target.label), encoding="utf-8")
        uris.append(page.as_uri())
    return uris

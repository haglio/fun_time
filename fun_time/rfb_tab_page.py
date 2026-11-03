# The destination cannot ride on Chrome's command line: ten URLs of up to 4 KB overflow
# CreateProcess's 32,767 characters (WinError 206).
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from shared_ui.pages import with_family_look

from .loopback_server import LOOPBACK_PORT, omnipause_url

_TEMPLATE_PATH = Path(__file__).resolve().parent / "static" / "tab_page_template.html"
_PAGE_GLOB = "*.html"


@dataclass(frozen=True)
class TabTarget:
    url: str
    label: str
    video_path: str = ""


def tabs_dir(state_dir: str | Path) -> Path:
    return Path(state_dir) / "rfb_tabs"


def _js_string(value: str) -> str:
    return json.dumps(value).replace("<", "\\u003c")


def _file_uri(video_path: str) -> str:
    if not video_path:
        return ""
    path = Path(video_path)
    if not path.is_absolute():
        return ""
    return path.as_uri()


def render_tab_page(target: TabTarget, *, loopback_port: int = LOOPBACK_PORT) -> str:
    template = with_family_look(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    for token, value in (
        ('"__FT_TARGET__"', target.url),
        ('"__FT_LABEL__"', target.label),
        ('"__FT_VIDEO__"', _file_uri(target.video_path)),
        ('"__FT_OMNIPAUSE__"', omnipause_url(loopback_port)),
    ):
        template = template.replace(token, _js_string(value))
    return template


def _write_page(page: Path, target: TabTarget, loopback_port: int) -> str:
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(render_tab_page(target, loopback_port=loopback_port), encoding="utf-8")
    return page.as_uri()


def write_tab_pages(
    pages_dir: Path, targets: Sequence[TabTarget], *, loopback_port: int = LOOPBACK_PORT,
) -> list[str]:
    pages_dir.mkdir(parents=True, exist_ok=True)
    for stale in pages_dir.glob(_PAGE_GLOB):
        stale.unlink()

    return [
        _write_page(pages_dir / f"tab_{index:02d}.html", target, loopback_port)
        for index, target in enumerate(targets, start=1)
    ]


def write_lock_tab_page(
    pages_dir: Path, target: TabTarget, *, loopback_port: int = LOOPBACK_PORT,
) -> str:
    digest = hashlib.sha1(target.url.encode("utf-8")).hexdigest()[:12]
    return _write_page(pages_dir / f"lock_{digest}.html", target, loopback_port)

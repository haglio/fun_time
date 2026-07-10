from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .config import ProviderRegenConfig, ProjectConfig
from .provider_regen import regen_url_for_video
from .rfb_tab_page import TabTarget

_T = TypeVar("_T")


def resolve_profile_directory(user_data_dir: Path, profile_name: str) -> str:
    local_state_path = user_data_dir / "Local State"
    if not local_state_path.is_file():
        return ""

    with local_state_path.open("r", encoding="utf-8") as fh:
        local_state = json.load(fh)

    info_cache = local_state.get("profile", {}).get("info_cache", {})
    if not isinstance(info_cache, dict):
        return ""

    for directory_name, entry in info_cache.items():
        if isinstance(entry, dict) and entry.get("name") == profile_name:
            return directory_name
    return ""


_HYPERLINK_PREFIX = '=HYPERLINK("'
_HYPERLINK_SEP = '";"'
_HYPERLINK_SUFFIX = '")'


def _hyperlink_parts(cell: str) -> tuple[str, str] | None:
    """Split ``=HYPERLINK("target";"display")`` into its two arguments."""
    if not cell.startswith(_HYPERLINK_PREFIX) or not cell.endswith(_HYPERLINK_SUFFIX):
        return None
    inner = cell[len(_HYPERLINK_PREFIX) : -len(_HYPERLINK_SUFFIX)]
    separator = inner.find(_HYPERLINK_SEP)
    if separator == -1:
        return None
    return inner[:separator], inner[separator + len(_HYPERLINK_SEP) :]


def extract_url_from_hyperlink(cell: str) -> str:
    """Extract the URL from an ``=HYPERLINK("url";"text")`` formula cell.

    If *cell* is not a HYPERLINK formula it is returned stripped as-is
    (allowing plain URLs).
    """
    cell = cell.strip()
    if not cell.startswith(_HYPERLINK_PREFIX):
        return cell
    parts = _hyperlink_parts(cell)
    return parts[0] if parts else ""


def extract_path_from_hyperlink(cell: str) -> str:
    """Extract the local path from a ``local_file`` ``=HYPERLINK`` cell.

    The display text holds the unescaped Windows path, so it round-trips
    exactly, unlike the ``file://`` URI in the formula's first argument.
    """
    cell = cell.strip()
    if not cell.startswith(_HYPERLINK_PREFIX):
        return cell
    parts = _hyperlink_parts(cell)
    return parts[1] if parts else ""


@dataclass(frozen=True)
class FavEntry:
    """One favourited video: where it lives on disk, and its gallery page."""

    local_path: str
    web_url: str


def load_favs_entries(favs_file: Path) -> list[FavEntry]:
    """Read every favourite from *favs_file*, keeping both of its columns."""
    if not favs_file.is_file():
        return []
    entries: list[FavEntry] = []
    with favs_file.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            entry = FavEntry(
                local_path=extract_path_from_hyperlink(row.get("local_file", "")),
                web_url=extract_url_from_hyperlink(row.get("web_url", "")),
            )
            if entry.local_path or entry.web_url:
                entries.append(entry)
    return entries


def target_for_fav(entry: FavEntry, regen: ProviderRegenConfig) -> TabTarget:
    """Resolve the page a favourite should open, and the clip it shows meanwhile.

    A Provider video with a metadata sidecar targets the generate page carrying its
    original prompts (``#ft=``), which the example.com userscript reads to fill the
    form and raise its floating note.  Everything else falls back to the stored
    gallery link.  The label stays short (a regenerate URL runs to kilobytes of
    encoded payload).  Both the RFB's startup tabs and the lock hotkey resolve
    their tabs through here, so they can never drift apart.
    """
    regen_url = regen_url_for_video(
        entry.local_path,
        media_root=regen.media_root,
        metadata_root=regen.metadata_root,
        video_url=regen.generate_video_url,
        image_url=regen.generate_image_url,
    )
    return TabTarget(
        url=regen_url or entry.web_url,
        label=entry.web_url or Path(entry.local_path).name,
        video_path=entry.local_path,
    )


def choose_random(items: list[_T], count: int, rng: random.Random | None = None) -> list[_T]:
    if not items or count <= 0:
        return []
    chooser = rng or random.SystemRandom()
    if len(items) <= count:
        picked = list(items)
        chooser.shuffle(picked)
        return picked
    return chooser.sample(items, count)


def build_manifest(config: ProjectConfig) -> tuple[str, list[TabTarget]]:
    browser = config.random_favs_browser
    if not browser.enabled:
        return "", []

    profile_directory = resolve_profile_directory(browser.user_data_dir, browser.profile_name)
    if not profile_directory:
        return "", []

    targets = [
        target
        for target in (
            target_for_fav(entry, config.provider_regen)
            for entry in load_favs_entries(config.paths.favs_file)
        )
        if target.url
    ]
    return profile_directory, choose_random(targets, browser.open_count)


def write_manifest(output_path: Path, profile_directory: str, urls: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [profile_directory, *urls]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

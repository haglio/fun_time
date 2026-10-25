from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .config import ProjectConfig, RegenConfig
from .content import WebProvider
from .media_metadata import favorites_in
from .media_renditions import original_rendition
from .regen import regen_url_for_video
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



@dataclass(frozen=True)
class FavEntry:
    """One favorited video: where it lives on disk, and its gallery page."""

    local_path: str
    web_url: str


def favorite_entries(metadata_root: Path | None) -> list[FavEntry]:
    if metadata_root is None:
        return []
    return [FavEntry(local_path=str(favorite.video), web_url=favorite.page)
            for favorite in favorites_in(metadata_root)]


def target_for_fav(entry: FavEntry, regen: RegenConfig, providers: tuple[WebProvider, ...]) -> TabTarget:
    """Resolve the page a favorite should open, and the clip it shows meanwhile.

    The label stays short (a regenerate URL runs to kilobytes of encoded
    payload).  Both the RFB's startup tabs and the lock hotkey resolve their
    tabs through here, so they can never drift apart.

    The clip is the video's pre-upscale original: a thumbnail has no use for
    hundreds of megabytes of HEVC when a couple of megabytes of H.264 says the
    same thing, and every Chrome can decode the latter.
    """
    regen_url = regen_url_for_video(
        entry.local_path,
        metadata_root=regen.metadata_root,
        video_url=regen.generate_video_url,
        image_url=regen.generate_image_url,
        providers=providers,
    )
    return TabTarget(
        url=regen_url or entry.web_url,
        label=entry.web_url or Path(entry.local_path).name,
        video_path=original_rendition(entry.local_path, regen.media_root) or entry.local_path,
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


def build_manifest(config: ProjectConfig, providers: tuple[WebProvider, ...]) -> tuple[str, list[TabTarget]]:
    browser = config.random_favs_browser
    if not browser.enabled:
        return "", []

    profile_directory = resolve_profile_directory(browser.user_data_dir, browser.profile_name)
    if not profile_directory:
        return "", []

    targets = [
        target
        for target in (
            target_for_fav(entry, config.regen, providers)
            for entry in favorite_entries(config.regen.metadata_root)
        )
        if target.url
    ]
    return profile_directory, choose_random(targets, browser.open_count)


def write_manifest(output_path: Path, profile_directory: str, urls: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [profile_directory, *urls]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

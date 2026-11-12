from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from app_support.kino_folder import is_ai_video
from app_support.media_renditions import original_rendition

from .config import ProjectConfig, RegenConfig
from .content import WebProvider
from .gallery import Gallery, ai_videos_of_the_library, the_librarys_gallery
from .media_actions import make_web_url_from_path
from .media_metadata import favorites_in
from .regen import regen_url_for_video
from .rfb_tab_page import TabTarget


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
    local_path: str
    web_url: str


def favorite_entries(metadata_root: Path | None, gallery: Gallery,
                     providers: tuple[WebProvider, ...]) -> list[FavEntry]:
    recorded = [] if metadata_root is None else [
        FavEntry(local_path=str(favorite.video), web_url=favorite.page)
        for favorite in favorites_in(metadata_root) if not is_ai_video(favorite.video)]
    return [*recorded, *(with_its_page(str(video), gallery, providers)
                         for video in ai_videos_of_the_library() if gallery.is_starred(video))]


def with_its_page(video: str, gallery: Gallery, providers: tuple[WebProvider, ...]) -> FavEntry:
    recorded_page = gallery.page_of(video) if is_ai_video(video) else ""
    return FavEntry(local_path=video,
                    web_url=recorded_page or make_web_url_from_path(video, providers))


def target_for_fav(entry: FavEntry, regen: RegenConfig, providers: tuple[WebProvider, ...],
                   gallery: Gallery) -> TabTarget:
    regen_url = regen_url_for_video(
        entry.local_path,
        metadata_root=regen.metadata_root,
        gallery=gallery,
        video_url=regen.generate_video_url,
        image_url=regen.generate_image_url,
        providers=providers,
    )
    return TabTarget(
        url=regen_url or entry.web_url,
        label=entry.web_url or Path(entry.local_path).name,
        video_path=_the_rendition_light_enough_for_a_tab(entry.local_path, regen.media_root),
    )


def _the_rendition_light_enough_for_a_tab(video: str, media_root: Path) -> str:
    return original_rendition(video, media_root) or video


def choose_random[T](items: list[T], count: int, rng: random.Random | None = None) -> list[T]:
    if not items or count <= 0:
        return []
    chooser = rng or random.SystemRandom()
    if len(items) <= count:
        picked = list(items)
        chooser.shuffle(picked)
        return picked
    return chooser.sample(items, count)


def build_manifest(config: ProjectConfig, providers: tuple[WebProvider, ...], *,
                   gallery: Gallery | None = None) -> tuple[str, list[TabTarget]]:
    browser = config.random_faves_browser
    if not browser.enabled:
        return "", []

    profile_directory = resolve_profile_directory(browser.user_data_dir, browser.profile_name)
    if not profile_directory:
        return "", []

    if gallery is None:
        gallery = the_librarys_gallery()
    targets = [
        target
        for target in (
            target_for_fav(entry, config.regen, providers, gallery)
            for entry in favorite_entries(config.regen.metadata_root, gallery, providers)
        )
        if target.url
    ]
    return profile_directory, choose_random(targets, browser.open_count)


def write_manifest(output_path: Path, profile_directory: str, urls: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [profile_directory, *urls]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

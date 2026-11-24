from __future__ import annotations

from pathlib import Path

from app_support.kino_folder import is_ai_video
from app_support.site_pages import page_address

from .content import WebProvider, provider_that_made
from .gallery import Gallery
from .media_metadata import recorded_for


def _records_a_prompt(metadata: dict) -> bool:
    video = metadata.get("video") or {}
    source = metadata.get("source_image") or {}
    return bool(video.get("prompt") or source.get("positive_prompt"))


def regen_url_for_video(
    video_path: str | Path,
    *,
    metadata_root: str | Path | None,
    gallery: Gallery,
    video_url: str,
    image_url: str,
    providers: tuple[WebProvider, ...],
) -> str:
    maker = provider_that_made(video_path, providers)
    if maker is None:
        return ""
    metadata = _what_made(video_path, metadata_root, gallery)
    if not _records_a_prompt(metadata):
        return ""
    url = page_address(metadata, video_page=video_url, image_page=image_url)
    return url if maker.serves(url) else ""


def _what_made(video_path: str | Path, metadata_root: str | Path | None, gallery: Gallery) -> dict:
    return gallery.record_of(video_path) if is_ai_video(video_path) else recorded_for(
        video_path, metadata_root)

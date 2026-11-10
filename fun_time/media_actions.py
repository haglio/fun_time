from __future__ import annotations

from pathlib import Path

from .content import WebProvider, provider_that_made


def make_web_url_from_path(full_path: str, providers: tuple[WebProvider, ...]) -> str:
    if not full_path:
        return ""
    provider = provider_that_made(full_path, providers)
    if provider is None:
        return ""
    name_no_ext = Path(full_path.replace("/", "\\")).stem
    image_id = name_no_ext.rsplit("_", 1)[0] if "_" in name_no_ext else name_no_ext
    return provider.gallery_url.format(id=image_id)

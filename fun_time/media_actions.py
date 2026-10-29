from __future__ import annotations

from pathlib import Path

from .content import WebProvider, load_web_providers, provider_that_made


def make_web_url_from_path(
    full_path: str, providers: tuple[WebProvider, ...] | None = None
) -> str:
    if not full_path:
        return ""
    provider = provider_that_made(full_path, load_web_providers() if providers is None else providers)
    if provider is None:
        return ""
    name_no_ext = Path(full_path.replace("/", "\\")).stem
    image_id = name_no_ext.rsplit("_", 1)[0] if "_" in name_no_ext else name_no_ext
    return provider.gallery_url.format(id=image_id)


def move_to_weird(weird_dir: Path, source: Path, *, destination_name: str | None = None) -> Path:
    weird_dir.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return weird_dir / (destination_name or source.name)

    target_name = destination_name or source.name
    destination = weird_dir / target_name
    stem = Path(target_name).stem
    suffix = Path(target_name).suffix
    duplicate_index = 1
    while destination.exists():
        destination = weird_dir / f"{stem}__dup{duplicate_index}{suffix}"
        duplicate_index += 1
    source.replace(destination)
    return destination

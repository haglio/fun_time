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


def move_to_trash(trash_dir: Path, source: Path, *, destination_name: str | None = None) -> Path:
    trash_dir.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return trash_dir / (destination_name or source.name)

    target_name = destination_name or source.name
    destination = trash_dir / target_name
    stem = Path(target_name).stem
    suffix = Path(target_name).suffix
    duplicate_index = 1
    while destination.exists():
        destination = trash_dir / f"{stem}__dup{duplicate_index}{suffix}"
        duplicate_index += 1
    source.replace(destination)
    return destination

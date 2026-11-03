from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from .library_handles import LibraryHandle

FOLDER_PREVIEWS = 4


@dataclass(frozen=True)
class SubFolder:
    name: str
    count: int
    previews: tuple[str, ...]

    @property
    def display_name(self) -> str:
        return self.name


@dataclass(frozen=True)
class Folder:
    path: tuple[str, ...]
    children: tuple[SubFolder, ...]
    handles: tuple[LibraryHandle, ...]

    @property
    def parent(self) -> tuple[str, ...] | None:
        return None if not self.path else self.path[:-1]


def folder_of(handle: LibraryHandle) -> tuple[str, ...]:
    return tuple(part for part in handle.section.split("/") if part)


def folder_at(
    handles: Sequence[LibraryHandle],
    path: Sequence[str],
    *,
    rng: random.Random | None = None,
) -> Folder:
    path = tuple(path)
    inside = [h for h in handles if folder_of(h)[: len(path)] == path]
    names: dict[str, list[LibraryHandle]] = {}
    for handle in inside:
        parts = folder_of(handle)
        if len(parts) > len(path):
            names.setdefault(parts[len(path)], []).append(handle)
    if names:
        return Folder(
            path=path,
            children=tuple(
                SubFolder(
                    name=name,
                    count=len(grouped),
                    previews=tuple(
                        handle.preview
                        for handle in (rng or random.Random(name)).sample(
                            grouped, min(FOLDER_PREVIEWS, len(grouped))
                        )
                    ),
                )
                for name, grouped in names.items()
            ),
            handles=(),
        )
    return Folder(path=path, children=(), handles=tuple(inside))

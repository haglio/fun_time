"""The folders the library browser lets you walk into.

The tree here is NOT the tree on disk.  The library's own folders record how far
each file got through the pipeline — ``0 unsorted``, ``1 could use work/…``,
``3_good_to_go/processed`` — and those are the librarian's business, not the
viewer's: a video's processing stage is an implementation detail Fun Time exists
to hide, and its other stages are reachable from the player as versions anyway.

So a folder here is a division of the *library*, never of the pipeline: the
source folder a video came from, and beneath it the folder its cuts or its whole
videos were filed into, which is exactly what :attr:`LibraryHandle.section`
already names.  Walking stops there — the deepest folder hands over every video
under it at once, however many stage folders they are spread across on disk.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from .library_handles import LibraryHandle


# How many videos a folder tile is pictured with.  Four says what kind of thing
# is inside where one said almost nothing about a folder of hundreds, and still
# leaves each cell big enough to read at a tile's size.
FOLDER_PREVIEWS = 4


@dataclass(frozen=True)
class SubFolder:
    """A folder tile: what it is called, how much is in it, and its pictures."""

    name: str
    count: int
    previews: tuple[str, ...]


@dataclass(frozen=True)
class Folder:
    """What the browser shows at one place in the tree.

    A folder holds *either* sub-folders or videos, never both — every level of
    this tree is a division of the library, so a folder that divides has nothing
    of its own to show and one that does not is where the videos live.
    """

    path: tuple[str, ...]
    children: tuple[SubFolder, ...]
    handles: tuple[LibraryHandle, ...]

    @property
    def parent(self) -> tuple[str, ...] | None:
        """The folder to go back to, or None at the root."""
        return None if not self.path else self.path[:-1]

    @property
    def title(self) -> str:
        """What to call this folder on screen."""
        return "/".join(self.path)


def _section_parts(handle: LibraryHandle) -> tuple[str, ...]:
    return tuple(part for part in handle.section.split("/") if part)


def folder_at(
    handles: Sequence[LibraryHandle],
    path: Sequence[str],
    *,
    rng: random.Random | None = None,
) -> Folder:
    """What the browser shows at *path* — its sub-folders, or its videos.

    Order follows *handles* throughout, which arrive sectioned and alphabetical
    from :func:`fun_time.library_handles.build_library_handles`, so a folder's
    tiles and its videos come up in the order the library was already ranked in.

    The stills on a tile are sampled, but seeded from the tile's own name, so
    they are the same four every visit.  *rng* overrides that for a test.
    """
    path = tuple(path)
    inside = [h for h in handles if _section_parts(h)[: len(path)] == path]
    names: dict[str, list[LibraryHandle]] = {}
    for handle in inside:
        parts = _section_parts(handle)
        if len(parts) > len(path):
            names.setdefault(parts[len(path)], []).append(handle)
    if names:
        return Folder(
            path=path,
            children=tuple(
                SubFolder(
                    name=name,
                    count=len(members),
                    previews=tuple(
                        handle.preview
                        for handle in (rng or random.Random(name)).sample(
                            members, min(FOLDER_PREVIEWS, len(members))
                        )
                    ),
                )
                for name, members in names.items()
            ),
            handles=(),
        )
    return Folder(path=path, children=(), handles=tuple(inside))

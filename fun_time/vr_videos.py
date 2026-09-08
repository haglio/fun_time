"""Which videos are mastered for the headset, and which are ordinary flat ones.

Two things say which a file is: the library folder it was put in, and a filename
that names its mastering outright.  :mod:`fun_time_vr.projection` reads the same
two to decide WHICH projection to open a VR video in.  Here rather than beside
those because the desktop half asks the question too: the answer narrows a
playlist, and playlists are built over here.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

# Filename tokens that name a VR mastering, in the order a name has to be read:
# a fisheye master often carries "180" too, so fisheye must win.  The order is
# load-bearing for fun_time_vr, which maps each of these to the projection it
# names.
VR_FILENAME_TOKENS: tuple[str, ...] = (
    "mkx200", "fisheye", "rf52", "_360", "_180", "180_",
)


def is_vr_video(video_path: str | Path, vr_dirs: Sequence[Path | str]) -> bool:
    """Whether *video_path* is a VR master: named as one, or in a VR library dir.

    The name goes first because it travels with the file, where the folder does not.
    """
    path = Path(video_path)
    name = path.name.lower()
    if any(token in name for token in VR_FILENAME_TOKENS):
        return True
    for root in vr_dirs:
        try:
            path.relative_to(Path(root))
        except ValueError:
            continue
        return True
    return False


def keep_shapes(
    paths: Iterable[str],
    *,
    vr_dirs: Sequence[Path | str],
    plays_vr: bool,
    plays_flat: bool,
) -> list[str]:
    """The videos of the shapes asked for: VR masters, flat ones, or both.
    Neither is empty rather than everything -- what to do with an empty browse is
    the caller's, and widening one nobody widened is not an answer.
    """
    if plays_vr and plays_flat:
        return list(paths)
    if not (plays_vr or plays_flat):
        return []
    return [path for path in paths if is_vr_video(path, vr_dirs) is plays_vr]

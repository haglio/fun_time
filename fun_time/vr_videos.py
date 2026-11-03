from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

VR_FILENAME_TOKENS: tuple[str, ...] = (
    "mkx200", "fisheye", "rf52", "_360", "_180", "180_",
)


def is_vr_video(video_path: str | Path, vr_dirs: Sequence[Path | str]) -> bool:
    path = Path(video_path)
    name = path.name.lower()
    return any(token in name for token in VR_FILENAME_TOKENS) or in_a_vr_folder(path, vr_dirs)


def in_a_vr_folder(video_path: str | Path, vr_dirs: Sequence[Path | str]) -> bool:
    path = Path(video_path)
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
    if plays_vr and plays_flat:
        return list(paths)
    if not (plays_vr or plays_flat):
        return []
    return [path for path in paths if is_vr_video(path, vr_dirs) is plays_vr]


def shapes_verb(*, plays_vr: bool, plays_flat: bool) -> str:
    return " ".join(("SHAPES", *(word for word, plays in (("vr", plays_vr), ("flat", plays_flat))
                                 if plays)))

from __future__ import annotations

from pathlib import Path


LABEL_PRIMARY_VLC = "Non-AI VLC"
LABEL_PRIMARY_ROBOT = "Non-AI Robot Hand"
LABEL_PORTRAIT_VLC = "Portrait AI VLC"
LABEL_LANDSCAPE_VLC = "Landscape AI VLC"
LABEL_OSR2 = "OSR2"
LABEL_MFP = "MFP"


def primary_panel_should_highlight(
    *,
    f_mode_enabled: bool,
    primary_path: str,
    has_matching_funscript: bool,
) -> bool:
    if f_mode_enabled:
        return True
    return bool(primary_path) and has_matching_funscript


def satellite_panel_should_highlight(*, f_mode_enabled: bool, is_favorite: bool) -> bool:
    if f_mode_enabled:
        return True
    return is_favorite


def clip_label_from_path(path: str) -> str:
    return Path(path).name if path else "(none)"


def build_mirrored_funscript_path(video_path: str, primary_sources: str) -> str:
    video = Path(video_path)
    for source_part in primary_sources.split("|"):
        source_root = source_part.strip()
        if not source_root:
            continue
        source_root_path = Path(source_root)
        if not source_root_path.is_dir():
            continue
        try:
            relative_path = video.relative_to(source_root_path)
        except ValueError:
            continue
        mirrored_root = Path(str(source_root_path).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\"))
        return str((mirrored_root / relative_path).with_suffix(".funscript"))
    return ""


def has_matching_funscript(video_path: str, primary_sources: str) -> bool:
    mirrored = build_mirrored_funscript_path(video_path, primary_sources)
    return bool(mirrored) and Path(mirrored).exists()


def read_favs_content(favs_file: Path) -> str:
    try:
        if not favs_file.exists():
            return ""
        return favs_file.read_text(encoding="utf-8")
    except OSError:
        return ""


def is_favorite_path(video_path: str, favs_content: str) -> bool:
    if not video_path or not favs_content:
        return False
    return video_path in favs_content

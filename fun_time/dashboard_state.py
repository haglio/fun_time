from __future__ import annotations

from pathlib import Path


LABEL_PRIMARY_NAU = "Nau"
LABEL_PRIMARY_GENAU = "Genau"
LABEL_PRIMARY_HYBRID = "Hybrid"
LABEL_PORTRAIT_VLC = "Portrait\nAI VLC"
LABEL_LANDSCAPE_VLC = "Landscape AI VLC"
LABEL_OSR2 = "OSR2"


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


def build_mirrored_funscript_path(video_path: str) -> str:
    normalized = str(Path(video_path))
    marker = "\\videos\\videos\\"
    if marker not in normalized:
        return ""
    mirrored = normalized.replace(marker, "\\videos\\scripts\\scripts\\", 1)
    return str(Path(mirrored).with_suffix(".funscript"))


def has_matching_funscript(video_path: str) -> bool:
    mirrored = build_mirrored_funscript_path(video_path)
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

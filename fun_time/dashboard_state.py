from __future__ import annotations

from pathlib import Path


LABEL_PRIMARY_NAU = "Nau"
LABEL_PRIMARY_GENAU = "Genau"
LABEL_PRIMARY_HYBRID = "Hybrid Nau+Genau"
# What each side's panel calls it — the same name its window carries in Alt-Tab
# (see windows_bridge_startup's SATELLITE_*_TITLE), so the panel and the window
# are recognisably the same player.  Portrait's panel is a narrow column, so its
# name wraps rather than being shortened.
LABEL_PORTRAIT = "Portrait\nAI Player"
LABEL_LANDSCAPE = "Landscape AI Player"
LABEL_OSR2 = "OSR2"


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

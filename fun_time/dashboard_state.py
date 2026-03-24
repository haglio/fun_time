from __future__ import annotations


LABEL_PRIMARY_VLC = "Non-AI VLC"
LABEL_PRIMARY_ROBOT = "Non-AI Robot Hand"
LABEL_PORTRAIT_VLC = "Portrait AI VLC"
LABEL_LANDSCAPE_VLC = "Landscape AI VLC"
LABEL_OSR2 = "OSR2"
LABEL_MFP = "MFP"
LABEL_BROKER = "Broker"
LABEL_CONTROLLER = "Controller"
LABEL_F_MODE = "F-Mode"


def panel_label_text(label: str) -> str:
    if label == LABEL_PORTRAIT_VLC:
        return "Portrait AI\nVLC"
    if label == LABEL_LANDSCAPE_VLC:
        return "Landscape AI\nVLC"
    if label == LABEL_PRIMARY_VLC:
        return "Non-AI\nVLC"
    if label == LABEL_PRIMARY_ROBOT:
        return "Non-AI\nRobot Hand"
    return label


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

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app_support.file_channel import read_key_values

from fun_time.config import MAIN_FUNESTRA_STATUS, SatelliteChannel
from fun_time.dashboard_layout import Rect
from fun_time.fun_time_funestras import FunTimeFunestra


@dataclass(frozen=True)
class FunestraRects:
    main: Rect
    portrait: Rect
    landscape: Rect
    dash: Rect


def message_target_rect(source: str, rects: FunestraRects) -> Rect:
    return {
        "main": rects.main,
        "portrait": rects.portrait,
        "landscape": rects.landscape,
        "dash": rects.dash,
    }.get(source, rects.main)


def funestra_window(state_dir: Path, source: str) -> int | None:
    satellites = {funestra.label: SatelliteChannel.in_state_dir(state_dir, funestra).status_file
                  for funestra in FunTimeFunestra.SATELLITES}
    status_file = satellites.get(source, state_dir / MAIN_FUNESTRA_STATUS)
    try:
        window = read_key_values(status_file).get("window", "").strip()
    except OSError:
        return None
    return int(window) if window.isdigit() else None

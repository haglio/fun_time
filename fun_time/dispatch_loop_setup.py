from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .fun_time_windows import FunTimeWindows
from .hud_transport import HudPublisher
from .rfb_slideshow import RfbSlideshow
from .shortcuts import Shortcut
from .text_field_clicks import Sightings
from .window_layout import SecondaryMonitorRects

LOOP_TICK_S = 0.05


@dataclass(frozen=True)
class SessionFiles:
    dashboard_cmd: Path
    shared_state: Path
    ahk_cmd: Path
    manifest: Path | None = None


@dataclass(frozen=True)
class SessionStart:
    windows: FunTimeWindows
    rfb_shortcut: Shortcut | None = None
    rfb_slideshow: RfbSlideshow | None = None
    press_and_caret_watch: Sightings | None = None
    origenerator_already_open: bool = False
    origenerator_showing_a_slideshow: bool = False


@dataclass(frozen=True)
class Publishing:
    dashboard_enabled: bool
    hud_publisher: HudPublisher | None = None
    secondary_rects: Callable[..., SecondaryMonitorRects] | None = None
    sync_interval_ms: int = 200

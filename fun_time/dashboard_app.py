from __future__ import annotations

import argparse
import configparser
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from dataclasses import replace
import math
import os
from pathlib import Path
import queue
import socket
import threading
import time

from PyQt6.QtGui import QColor, QFont

from shared_ui.colors import (
    AMBER,
    BG_PRIMARY,
    BG_SECONDARY,
    BLUE,
    BORDER_PANEL,
    CABLE_ACTIVE,
    CABLE_INACTIVE,
    GREEN,
    PINK,
    RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from shared_ui.fonts import (
    FONT_EMOJI,
    FONT_SYMBOL,
    FONT_UI,
    SIZE_BODY,
    SIZE_SMALL,
    SIZE_TINY,
    make_font,
)

from fun_time.config import LayoutConfig
from fun_time.startup_progress import loading_screen_active
from fun_time.manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.satellite_control import read_satellite_status
from fun_time.win32 import is_window_topmost, set_always_on_top
from fun_time.dashboard_actions import (
    BROKER_PANEL,
    CLIPPER_SAVE,
    GENAU_AMP_DOWN,
    GENAU_AMP_UP,
    GENAU_CRUISE,
    GENAU_TOGGLE_AUTO,
    GENAU_CTR_DOWN,
    GENAU_CTR_UP,
    GENAU_SHAPE,
    GENAU_SPD_DOWN,
    GENAU_SPD_UP,
    LANDSCAPE_LOCK,
    LANDSCAPE_NEXT,
    LANDSCAPE_PREV,
    LANDSCAPE_TRASH,
    FMODE_PANEL,
    GENAU_ACTIVATE,
    HELP_REFERENCE,
    HELP_REFERENCE_CLOSE,
    HYBRID_ACTIVATE,
    OMNIMINIMIZE,
    OMNIRESTORE,
    OMNIPAUSE_TOGGLE,
    OPEN_FILE_DIALOG,
    PORTRAIT_LOCK,
    PORTRAIT_NEXT,
    PORTRAIT_PREV,
    PORTRAIT_TRASH,
    NAU_ACTIVATE,
    NAU_RECORD,
    PRIMARY_NEXT,
    PRIMARY_NUDGE_NEXT,
    PRIMARY_NUDGE_PREV,
    PRIMARY_PREV,
    QUARTER_BUTTON,
    QUIT_BUTTON,
    VOICE_TOGGLE,
)
from fun_time.command_reference import render_reference_html
from player_core.file_channel import append_command
from fun_time.event_log import EVENT_LOG_FILENAME, event_log_path, read_events
from fun_time.log_panel import LogPanelWidget, prefs_path
from fun_time.monitors import enumerate_monitors, get_logical_monitor_rects
from fun_time.notice_overlay import (
    NoticeOverlay,
    PlayerRects,
    is_announcement,
    notice_target_rect,
)
from fun_time.window_layout import compute_primary_media_rect, compute_window_layout
from fun_time.dashboard_layout import (
    DashboardPreviewLayout,
    Rect,
    Size,
    client_rect_filling_frame,
    compute_dashboard_preview_layout,
)
from fun_time.dashboard_runtime import DashboardSnapshot, GenauStatus, genau_enabled_path, is_broker_heartbeat_fresh, load_dashboard_snapshot, read_genau_enabled, read_genau_status, read_nau_status
from fun_time.dashboard_state import (
    LABEL_LANDSCAPE,
    LABEL_OSR2,
    LABEL_PORTRAIT,
    LABEL_PRIMARY_GENAU,
    LABEL_PRIMARY_HYBRID,
    LABEL_PRIMARY_NAU,
    has_matching_funscript,
    is_favorite_path,
    read_favs_content,
)

# Semantic aliases — map old Dashboard names to shared_ui tokens.
COLOR_BG = BG_PRIMARY
COLOR_PANEL = BG_SECONDARY
COLOR_TEXT = TEXT_PRIMARY
COLOR_CABLE = CABLE_ACTIVE
COLOR_CABLE_DIM = CABLE_INACTIVE
COLOR_BLUE = BLUE
COLOR_GREEN = GREEN
COLOR_PINK = PINK
COLOR_RED = RED
COLOR_YELLOW = AMBER
# The "Fun Time" wordmark matches the loading screen's redder pink text, NOT the
# logo's magenta-pink (COLOR_PINK) — they are deliberately different hues.
COLOR_APP_TITLE = QColor("#e94560")

ICON_LOCK = "\U0001F512"
ICON_TRASH = "\U0001F5D1"


def lighten_color(color: QColor, amount: int = 50) -> QColor:
    return QColor(
        min(255, color.red() + amount),
        min(255, color.green() + amount),
        min(255, color.blue() + amount),
    )


@dataclass(frozen=True)
class DashboardAppConfig:
    layout: LayoutConfig
    manifest_path: Path
    favs_file: Path
    nau_status_file: Path
    portrait_status_file: Path
    landscape_status_file: Path
    broker_heartbeat_file: Path
    dashboard_state_file: Path
    dashboard_cmd_file: Path


@dataclass(frozen=True)
class DashboardLaunchGeometry:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class DashboardLineItem:
    points: tuple[tuple[int, int], ...]
    color: QColor
    width: int = 2
    smooth: bool = False


@dataclass(frozen=True)
class DashboardOvalItem:
    cx: int
    cy: int
    r: int
    fill: QColor
    outline: QColor | None = None
    outline_width: int = 1


@dataclass(frozen=True)
class DashboardArcItem:
    cx: int
    cy: int
    r: int
    start: float
    extent: float
    outline: QColor
    width: int = 1


@dataclass(frozen=True)
class DashboardTextItem:
    text: str
    rect: Rect
    color: QColor = field(default_factory=lambda: COLOR_TEXT)
    anchor: str = "center"
    font: QFont | None = None
    rotation: int = 0


@dataclass(frozen=True)
class DashboardImageItem:
    pixmap: QPixmap
    rect: Rect


@dataclass(frozen=True)
class DashboardRectItem:
    rect: Rect
    outline: QColor = field(default_factory=lambda: BORDER_PANEL)
    fill: QColor = field(default_factory=lambda: COLOR_PANEL)


@dataclass(frozen=True)
class DashboardScene:
    width: int
    height: int
    rects: tuple[DashboardRectItem, ...]
    texts: tuple[DashboardTextItem, ...]
    actions: tuple[tuple[str, Rect], ...]
    hover_texts: tuple[tuple[Rect, str], ...] = ()
    images: tuple[DashboardImageItem, ...] = ()
    lines: tuple[DashboardLineItem, ...] = ()
    ovals: tuple[DashboardOvalItem, ...] = ()
    arcs: tuple[DashboardArcItem, ...] = ()


def load_dashboard_app_config(manifest_path: Path) -> DashboardAppConfig:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(manifest_path, encoding="utf-8")

    layout = LayoutConfig(
        main_monitor=parser.getint("layout", "main_monitor"),
        secondary_monitor=parser.getint("layout", "secondary_monitor"),
        primary_top_ratio=parser.getfloat("layout", "primary_top_ratio"),
        landscape_width_ratio=parser.getfloat("layout", "landscape_width_ratio"),
    )
    return DashboardAppConfig(
        layout=layout,
        manifest_path=manifest_path,
        favs_file=Path(parser.get("media", "favs_file", fallback="favs.csv")),
        nau_status_file=Path(parser.get("commands", "nau_status_file", fallback="nau_status.txt")),
        portrait_status_file=Path(parser.get("commands", "portrait_status_file", fallback="portrait_status.txt")),
        landscape_status_file=Path(parser.get("commands", "landscape_status_file", fallback="landscape_status.txt")),
        broker_heartbeat_file=Path(parser.get("commands", "broker_heartbeat_file", fallback="broker_heartbeat.txt")),
        dashboard_state_file=Path(parser.get("commands", "dashboard_state_file", fallback="dashboard_state.ini")),
        dashboard_cmd_file=Path(parser.get("commands", "dashboard_cmd_file", fallback="dashboard_cmd.txt")),
    )


@dataclass(frozen=True)
class PlayerHydration:
    primary_path: str = ""
    portrait_path: str = ""
    landscape_path: str = ""
    primary_responsive: bool = False


def poll_players(app_config: DashboardAppConfig) -> PlayerHydration:
    # Every player publishes a status file now: Nau for the primary panel, and each
    # native satellite for the portrait/landscape panels.
    nau = read_nau_status(app_config.nau_status_file)
    portrait_path = read_satellite_status(app_config.portrait_status_file).video
    landscape_path = read_satellite_status(app_config.landscape_status_file).video
    return PlayerHydration(
        primary_path=nau.video,
        portrait_path=portrait_path,
        landscape_path=landscape_path,
        primary_responsive=bool(nau.video),
    )


def hydrate_dashboard_snapshot(snapshot: DashboardSnapshot, players: PlayerHydration) -> DashboardSnapshot:
    return replace(
        snapshot,
        primary_responsive=players.primary_responsive,
        primary=replace(snapshot.primary, path=players.primary_path),
        portrait=replace(snapshot.portrait, path=players.portrait_path),
        landscape=replace(snapshot.landscape, path=players.landscape_path),
    )


def resolve_logical_monitor_sizes(
    monitor_sizes: list[Size],
    *,
    main_monitor_index: int,
    secondary_monitor_index: int,
) -> tuple[Size, Size]:
    if len(monitor_sizes) < 2:
        raise ValueError("Expected at least two monitor sizes for dashboard preview")

    configured_main = monitor_sizes[max(0, min(len(monitor_sizes) - 1, main_monitor_index - 1))]
    configured_secondary = monitor_sizes[max(0, min(len(monitor_sizes) - 1, secondary_monitor_index - 1))]

    main_landscape = configured_main.width >= configured_main.height
    secondary_landscape = configured_secondary.width >= configured_secondary.height

    if main_landscape and not secondary_landscape:
        return configured_main, configured_secondary
    if secondary_landscape and not main_landscape:
        return configured_secondary, configured_main
    if configured_main.width >= configured_secondary.width:
        return configured_main, configured_secondary
    return configured_secondary, configured_main


def get_windows_monitor_work_areas() -> list[Size]:
    if not hasattr(ctypes, "WINFUNCTYPE"):
        return []

    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    monitors: list[Size] = []
    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )

    def _callback(hmonitor, _hdc, _rect_ptr, _lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            width = int(info.rcWork.right - info.rcWork.left)
            height = int(info.rcWork.bottom - info.rcWork.top)
            if width > 0 and height > 0:
                monitors.append(Size(width=width, height=height))
        return True

    user32.EnumDisplayMonitors(0, 0, monitor_enum_proc(_callback), 0)
    return monitors


def get_preview_monitor_sizes(app_config: DashboardAppConfig) -> tuple[Size, Size]:
    monitor_sizes = get_windows_monitor_work_areas()
    if len(monitor_sizes) >= 2:
        return resolve_logical_monitor_sizes(
            monitor_sizes,
            main_monitor_index=app_config.layout.main_monitor,
            secondary_monitor_index=app_config.layout.secondary_monitor,
        )

    return Size(2560, 1392), Size(1440, 3440)


_dashboard_pixmap_cache: dict[tuple[str, int], QPixmap] = {}


def _load_icon_pixmap(filename: str, height: int) -> QPixmap:
    """Load an icon .ico scaled to a square of *height* pixels, cached."""
    key = (filename, height)
    if key not in _dashboard_pixmap_cache:
        from PyQt6.QtCore import Qt

        ico_path = Path(__file__).resolve().parent.parent / filename
        pm = QPixmap(str(ico_path))
        if not pm.isNull():
            pm = pm.scaled(
                height, height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        _dashboard_pixmap_cache[key] = pm
    return _dashboard_pixmap_cache[key]


def _waveform_points(shape: str, w: int, h: int) -> list[tuple[int, int]]:
    """Return iconic pixel coordinates for a waveform shape.

    At dashboard icon sizes (~20x16), mathematically accurate full-cycle
    waveforms are indistinguishable.  Instead, draw a single arch/peak
    so the *character* of each shape is obvious: round top vs sharp peak
    vs flat top vs ramp.
    """
    mx, my = 2, 3
    dw = w - mx * 2
    dh = h - my * 2
    top = my
    bot = my + dh
    if shape == "triangle":
        # Sharp peak — two straight lines meeting at a point
        return [(mx, bot), (mx + dw // 2, top), (mx + dw, bot)]
    if shape == "rounded_square":
        # Flat top — clearly rectangular
        return [
            (mx, bot), (mx, top),
            (mx + dw, top), (mx + dw, bot),
        ]
    if shape == "sawtooth":
        # Ramp up, vertical drop
        return [(mx, bot), (mx + dw, top), (mx + dw, bot)]
    # Sine — smooth arch, obviously rounded at the peak
    steps = max(8, dw)
    points: list[tuple[int, int]] = []
    for i in range(steps + 1):
        t = (i / steps) * math.pi  # half-cycle: 0 to pi
        y = bot - int(math.sin(t) * dh)
        points.append((mx + round(i * dw / steps), y))
    return points


def _draw_waveform_pixmap(shape: str, w: int, h: int) -> QPixmap:
    """Draw a waveform icon as a QPixmap, cached."""
    key = (f"waveform_{shape}", w, h)
    if key not in _dashboard_pixmap_cache:
        from PyQt6.QtCore import Qt

        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(BLUE, 1))
        pts = _waveform_points(shape, w, h)
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        painter.end()
        _dashboard_pixmap_cache[key] = pm
    return _dashboard_pixmap_cache[key]


def _draw_mic_pixmap(w: int, h: int) -> QPixmap:
    """Draw the familiar voice-input microphone as a QPixmap, cached.

    A capsule head cradled by an upward-opening arc over a short stem and base —
    the mic glyph recording apps use — which reads as "voice" where a bare
    letter or a karaoke-mic emoji did not.  Drawn in a square centred in the
    panel so it stays round whatever the panel's aspect.
    """
    key = ("mic", w, h)
    if key not in _dashboard_pixmap_cache:
        from PyQt6.QtCore import Qt

        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(w, h)
        oy = (h - s) / 2.0
        cx = w / 2.0
        pen = QPen(COLOR_TEXT, max(1, round(s * 0.09)))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Capsule (mic head): a filled stadium in the upper portion.
        cap_w = s * 0.36
        painter.setBrush(QBrush(COLOR_TEXT))
        painter.drawRoundedRect(
            round(cx - cap_w / 2), round(oy + s * 0.12),
            round(cap_w), round(s * 0.44), cap_w / 2, cap_w / 2,
        )
        # Cradle: an upward-opening arc cupping the capsule from below.
        painter.setBrush(Qt.GlobalColor.transparent)
        r = round(s * 0.30)
        arc_cy = round(oy + s * 0.40)
        painter.drawArc(round(cx) - r, arc_cy - r, 2 * r, 2 * r, 200 * 16, 140 * 16)
        # Stem down to a short base line.
        base_y = round(oy + s * 0.90)
        painter.drawLine(round(cx), arc_cy + r, round(cx), base_y)
        painter.drawLine(round(cx - s * 0.17), base_y, round(cx + s * 0.17), base_y)
        painter.end()
        _dashboard_pixmap_cache[key] = pm
    return _dashboard_pixmap_cache[key]


# Short hover labels for every clickable dashboard action, so no button is
# left without a tooltip. GENAU_TOGGLE_AUTO is overridden at build time with
# its live allowed/suppressed state.
_ACTION_TOOLTIPS: dict[str, str] = {
    QUIT_BUTTON: "Quit",
    OMNIPAUSE_TOGGLE: "Pause all",
    HELP_REFERENCE: "Hotkeys & Voice Commands Reference",
    PORTRAIT_PREV: "Previous portrait clip",
    PORTRAIT_NEXT: "Next portrait clip",
    PORTRAIT_LOCK: "Lock / unlock portrait",
    PORTRAIT_TRASH: "Mark portrait weird",
    PRIMARY_PREV: "Previous video",
    PRIMARY_NEXT: "Next video",
    PRIMARY_NUDGE_PREV: "Nudge back 10s",
    PRIMARY_NUDGE_NEXT: "Nudge forward 10s",
    QUARTER_BUTTON: "Offset ¼ cycle",
    GENAU_AMP_UP: "Amplitude up",
    GENAU_AMP_DOWN: "Amplitude down",
    GENAU_CTR_UP: "Center up",
    GENAU_CTR_DOWN: "Center down",
    GENAU_SPD_UP: "Speed up",
    GENAU_SPD_DOWN: "Speed down",
    GENAU_CRUISE: "Cruise control",
    GENAU_SHAPE: "Cycle waveform shape",
    GENAU_TOGGLE_AUTO: "Genau takeover",
    HYBRID_ACTIVATE: "Hybrid mode",
    NAU_ACTIVATE: "Nau mode",
    GENAU_ACTIVATE: "Genau mode",
    OPEN_FILE_DIALOG: "Open file browser",
    CLIPPER_SAVE: "Save clip",
    NAU_RECORD: "Record loop",
    LANDSCAPE_PREV: "Previous landscape clip",
    LANDSCAPE_NEXT: "Next landscape clip",
    LANDSCAPE_LOCK: "Lock / unlock landscape",
    LANDSCAPE_TRASH: "Mark landscape weird",
    BROKER_PANEL: "Broker",
    FMODE_PANEL: "F-Mode",
    VOICE_TOGGLE: "Voice",
}


def _action_tooltip(action_id: str, takeover_hover: str) -> str:
    if action_id == GENAU_TOGGLE_AUTO:
        return takeover_hover  # live allowed/suppressed state
    return _ACTION_TOOLTIPS.get(action_id, "")


def build_dashboard_scene(
    layout: DashboardPreviewLayout,
    snapshot: DashboardSnapshot | None = None,
    *,
    favs_file: Path | None = None,
    broker_heartbeat_file: Path | None = None,
    genau_status: GenauStatus | None = None,
    genau_takeover_allowed: bool = True,
    pressed_actions: frozenset[str] = frozenset(),
) -> DashboardScene:
    primary_label = LABEL_PRIMARY_NAU
    portrait_label = LABEL_PORTRAIT
    landscape_label = LABEL_LANDSCAPE
    osr2_label = LABEL_OSR2
    primary_fill = COLOR_PANEL
    portrait_fill = COLOR_PANEL
    landscape_fill = COLOR_PANEL
    osr2_fill = COLOR_PANEL
    broker_fill = COLOR_PANEL
    fmode_fill = COLOR_PANEL
    voice_fill = COLOR_PANEL
    portrait_lock_fill = COLOR_PANEL
    landscape_lock_fill = COLOR_PANEL

    if snapshot is not None:
        favs_content = read_favs_content(favs_file) if favs_file is not None else ""
        broker_running = is_broker_heartbeat_fresh(broker_heartbeat_file) if broker_heartbeat_file is not None else False
        _mode_labels = {"nau": LABEL_PRIMARY_NAU, "genau": LABEL_PRIMARY_GENAU, "hybrid": LABEL_PRIMARY_HYBRID}
        primary_label_name = _mode_labels.get(snapshot.primary_mode, LABEL_PRIMARY_NAU)
        primary_label = primary_label_name
        primary_funscript_exists = has_matching_funscript(snapshot.primary.path)
        funscript_active = bool(snapshot.primary.path) and primary_funscript_exists
        if snapshot.osr2_mode == "off":
            osr2_label = f"{LABEL_OSR2}\n(off)"
        elif snapshot.osr2_mode == "auto":
            osr2_label = f"{LABEL_OSR2}\n(auto)"
        elif funscript_active:
            osr2_label = f"{LABEL_OSR2}\n(funscript\ncontrol)"
        else:
            osr2_label = f"{LABEL_OSR2}\n(idle; no\nfunscript)"
        # Each panel reflects the ACTUAL current video, never F-mode on faith:
        # green means "this video has a funscript / is a favorite".  Under a
        # working F-mode every video qualifies, so the panels are green anyway —
        # and a video that slips past the filter correctly shows neutral.
        if snapshot.osr2_mode == "auto":
            primary_fill = COLOR_PINK
        elif snapshot.primary_mode == "genau":
            primary_fill = COLOR_PANEL
        elif funscript_active:
            primary_fill = COLOR_GREEN
        else:
            primary_fill = COLOR_PANEL
        portrait_fill = COLOR_GREEN if is_favorite_path(snapshot.portrait.path, favs_content) else COLOR_PANEL
        landscape_fill = COLOR_GREEN if is_favorite_path(snapshot.landscape.path, favs_content) else COLOR_PANEL
        if snapshot.osr2_mode == "off":
            osr2_fill = COLOR_PANEL
        elif snapshot.osr2_mode == "auto":
            osr2_fill = COLOR_PINK
        elif funscript_active:
            osr2_fill = COLOR_GREEN
        else:
            osr2_fill = COLOR_PANEL
        broker_fill = COLOR_BLUE if broker_running else COLOR_RED
        fmode_fill = COLOR_GREEN if snapshot.f_mode_enabled else COLOR_PANEL
        voice_fill = COLOR_BLUE if snapshot.voice_active else COLOR_PANEL
        portrait_lock_fill = COLOR_GREEN if snapshot.portrait.locked else COLOR_PANEL
        landscape_lock_fill = COLOR_GREEN if snapshot.landscape.locked else COLOR_PANEL

    omni_paused = snapshot is not None and snapshot.omni_paused
    omnipause_icon = "\u25B6" if omni_paused else "\u23F8"
    omnipause_fill = COLOR_PANEL

    _genau = genau_status or GenauStatus()
    cruise_fill = BLUE if _genau.cruise_active else COLOR_PANEL

    def _press_fill(fill: QColor, action_id: str) -> QColor:
        return lighten_color(fill) if action_id in pressed_actions else fill

    _is_genau = snapshot is not None and snapshot.primary_mode == "genau"
    _is_hybrid = snapshot is not None and snapshot.primary_mode == "hybrid"

    # Genau takeover allow/suppress toggle — owns the bottom-left of the primary
    # panel in Nau/Hybrid mode (where Genau hasn't claimed the primary, so the
    # takeover is a live choice): green when allowed, red when suppressed.
    takeover_fill = COLOR_GREEN if genau_takeover_allowed else COLOR_RED
    takeover_hover = "Genau takeover: allowed" if genau_takeover_allowed else "Genau takeover: suppressed"

    rects = (
        DashboardRectItem(layout.main_monitor, fill=COLOR_PANEL),
        DashboardRectItem(layout.secondary_monitor, fill=COLOR_PANEL),
        DashboardRectItem(layout.rfb_panel, fill=COLOR_PANEL),
        DashboardRectItem(layout.dash_panel, fill=COLOR_PANEL),
        DashboardRectItem(layout.log_panel, fill=COLOR_PANEL),
        DashboardRectItem(layout.quit_button, fill=_press_fill(COLOR_PANEL, QUIT_BUTTON)),
        DashboardRectItem(layout.omnipause_button, fill=_press_fill(omnipause_fill, OMNIPAUSE_TOGGLE)),
        DashboardRectItem(layout.help_button, fill=COLOR_PANEL),
        DashboardRectItem(layout.landscape_panel, fill=landscape_fill),
        DashboardRectItem(layout.portrait_panel, fill=portrait_fill),
        DashboardRectItem(layout.primary_shadow, outline=COLOR_CABLE_DIM, fill=COLOR_PANEL),
        DashboardRectItem(layout.primary_panel, fill=primary_fill),
        DashboardRectItem(layout.osr2_panel, fill=osr2_fill),
        DashboardRectItem(layout.portrait_prev, fill=_press_fill(COLOR_PANEL, PORTRAIT_PREV)),
        DashboardRectItem(layout.portrait_next, fill=_press_fill(COLOR_PANEL, PORTRAIT_NEXT)),
        DashboardRectItem(layout.portrait_lock, fill=_press_fill(portrait_lock_fill, PORTRAIT_LOCK)),
        DashboardRectItem(layout.portrait_trash, fill=_press_fill(COLOR_PANEL, PORTRAIT_TRASH)),
        DashboardRectItem(layout.primary_prev, fill=_press_fill(COLOR_PANEL, PRIMARY_PREV)),
        DashboardRectItem(layout.primary_next, fill=_press_fill(COLOR_PANEL, PRIMARY_NEXT)),
        *(
            (
                DashboardRectItem(layout.quarter_button, fill=_press_fill(COLOR_PANEL, QUARTER_BUTTON)),
                DashboardRectItem(layout.genau_amp_up, fill=_press_fill(COLOR_PANEL, GENAU_AMP_UP)),
                DashboardRectItem(layout.genau_amp_down, fill=_press_fill(COLOR_PANEL, GENAU_AMP_DOWN)),
                DashboardRectItem(layout.genau_ctr_up, fill=_press_fill(COLOR_PANEL, GENAU_CTR_UP)),
                DashboardRectItem(layout.genau_ctr_down, fill=_press_fill(COLOR_PANEL, GENAU_CTR_DOWN)),
                DashboardRectItem(layout.genau_spd_up, fill=_press_fill(COLOR_PANEL, GENAU_SPD_UP)),
                DashboardRectItem(layout.genau_spd_down, fill=_press_fill(COLOR_PANEL, GENAU_SPD_DOWN)),
                DashboardRectItem(layout.genau_cruise, fill=_press_fill(cruise_fill, GENAU_CRUISE)),
                DashboardRectItem(layout.genau_shape, fill=_press_fill(COLOR_PANEL, GENAU_SHAPE)),
                DashboardRectItem(layout.hybrid_mode_button, fill=_press_fill(COLOR_PANEL, HYBRID_ACTIVATE)),
            )
            if _is_genau else (
                DashboardRectItem(layout.primary_nudge_prev, fill=_press_fill(COLOR_PANEL, PRIMARY_NUDGE_PREV)),
                DashboardRectItem(layout.primary_nudge_next, fill=_press_fill(COLOR_PANEL, PRIMARY_NUDGE_NEXT)),
                DashboardRectItem(layout.hybrid_quarter_button, fill=_press_fill(COLOR_PANEL, QUARTER_BUTTON)),
                DashboardRectItem(layout.hybrid_open_file_dialog, fill=_press_fill(COLOR_PANEL, OPEN_FILE_DIALOG)),
                DashboardRectItem(layout.clipper_save, fill=_press_fill(COLOR_PANEL, CLIPPER_SAVE)),
                DashboardRectItem(layout.hybrid_genau_amp_up, fill=_press_fill(COLOR_PANEL, GENAU_AMP_UP)),
                DashboardRectItem(layout.hybrid_genau_amp_down, fill=_press_fill(COLOR_PANEL, GENAU_AMP_DOWN)),
                DashboardRectItem(layout.hybrid_genau_ctr_up, fill=_press_fill(COLOR_PANEL, GENAU_CTR_UP)),
                DashboardRectItem(layout.hybrid_genau_ctr_down, fill=_press_fill(COLOR_PANEL, GENAU_CTR_DOWN)),
                DashboardRectItem(layout.hybrid_genau_spd_up, fill=_press_fill(COLOR_PANEL, GENAU_SPD_UP)),
                DashboardRectItem(layout.hybrid_genau_spd_down, fill=_press_fill(COLOR_PANEL, GENAU_SPD_DOWN)),
                DashboardRectItem(layout.genau_takeover, fill=_press_fill(takeover_fill, GENAU_TOGGLE_AUTO)),
                DashboardRectItem(layout.hybrid_cruise, fill=_press_fill(cruise_fill, GENAU_CRUISE)),
                DashboardRectItem(layout.genau_shape, fill=_press_fill(COLOR_PANEL, GENAU_SHAPE)),
            )
            if _is_hybrid else (
                DashboardRectItem(layout.primary_nudge_prev, fill=_press_fill(COLOR_PANEL, PRIMARY_NUDGE_PREV)),
                DashboardRectItem(layout.primary_nudge_next, fill=_press_fill(COLOR_PANEL, PRIMARY_NUDGE_NEXT)),
                DashboardRectItem(layout.open_file_dialog, fill=_press_fill(COLOR_PANEL, OPEN_FILE_DIALOG)),
                DashboardRectItem(layout.clipper_save, fill=_press_fill(COLOR_PANEL, CLIPPER_SAVE)),
                DashboardRectItem(layout.nau_record, fill=_press_fill(COLOR_PANEL, NAU_RECORD)),
                DashboardRectItem(layout.genau_takeover, fill=_press_fill(takeover_fill, GENAU_TOGGLE_AUTO)),
                DashboardRectItem(layout.hybrid_mode_button, fill=_press_fill(COLOR_PANEL, HYBRID_ACTIVATE)),
            )
        ),
        DashboardRectItem(layout.landscape_prev, fill=_press_fill(COLOR_PANEL, LANDSCAPE_PREV)),
        DashboardRectItem(layout.landscape_next, fill=_press_fill(COLOR_PANEL, LANDSCAPE_NEXT)),
        DashboardRectItem(layout.landscape_lock, fill=_press_fill(landscape_lock_fill, LANDSCAPE_LOCK)),
        DashboardRectItem(layout.landscape_trash, fill=_press_fill(COLOR_PANEL, LANDSCAPE_TRASH)),
        DashboardRectItem(layout.broker_panel, fill=_press_fill(broker_fill, BROKER_PANEL)),
        DashboardRectItem(layout.fmode_panel, fill=_press_fill(fmode_fill, FMODE_PANEL)),
        DashboardRectItem(layout.voice_panel, fill=_press_fill(voice_fill, VOICE_TOGGLE)),
        DashboardRectItem(layout.genau_mode_toggle, fill=_press_fill(COLOR_PANEL, GENAU_ACTIVATE)),
    )
    _font_symbol = make_font(FONT_SYMBOL, 10, bold=True)
    _font_ui_sm = make_font(FONT_UI, SIZE_SMALL, bold=True)
    _font_emoji = make_font(FONT_EMOJI, SIZE_SMALL)
    _font_ui_tiny = make_font(FONT_UI, SIZE_TINY, bold=True)
    # The app-name lockup, styled like the loading screen: pink, bold italic, a
    # step larger than the box titles.  Built fresh (not via the cached make_font)
    # so setItalic can't leak into every other user of a shared QFont.
    _font_app = QFont(FONT_UI, SIZE_BODY)
    _font_app.setBold(True)
    _font_app.setItalic(True)

    texts = (
        DashboardTextItem("\u23FB", layout.quit_button, font=_font_symbol),
        DashboardTextItem(omnipause_icon, layout.omnipause_button, font=_font_symbol),
        DashboardTextItem("?", layout.help_button, font=_font_ui_sm),
        DashboardTextItem(landscape_label, layout.landscape_panel, anchor="n"),
        DashboardTextItem(portrait_label, layout.portrait_panel, anchor="n"),
        DashboardTextItem(primary_label, layout.primary_panel, anchor="n"),
        DashboardTextItem(osr2_label, layout.osr2_panel, anchor="n"),
        DashboardTextItem("Favs Browser", layout.rfb_panel, anchor="n"),
        DashboardTextItem("Logs", layout.log_panel, anchor="n"),
        DashboardTextItem("Fun Time", layout.app_title, color=COLOR_APP_TITLE, anchor="w", font=_font_app),
        DashboardTextItem("<", layout.portrait_prev, font=_font_ui_sm),
        DashboardTextItem(">", layout.portrait_next, font=_font_ui_sm),
        DashboardTextItem(ICON_LOCK, layout.portrait_lock, font=_font_emoji),
        DashboardTextItem(ICON_TRASH, layout.portrait_trash, font=_font_emoji),
        DashboardTextItem("<", layout.primary_prev, font=_font_ui_sm),
        DashboardTextItem(">", layout.primary_next, font=_font_ui_sm),
        *(
            (
                DashboardTextItem("1/4", layout.quarter_button, font=_font_ui_tiny),
                DashboardTextItem("^", layout.genau_amp_up, font=_font_ui_tiny, color=TEXT_MUTED if _genau.amp_at_max else COLOR_TEXT),
                DashboardTextItem("v", layout.genau_amp_down, font=_font_ui_tiny, color=TEXT_MUTED if _genau.amp_at_min else COLOR_TEXT),
                DashboardTextItem("^", layout.genau_ctr_up, font=_font_ui_tiny, color=TEXT_MUTED if _genau.ctr_at_max else COLOR_TEXT),
                DashboardTextItem("v", layout.genau_ctr_down, font=_font_ui_tiny, color=TEXT_MUTED if _genau.ctr_at_min else COLOR_TEXT),
                DashboardTextItem("^", layout.genau_spd_up, font=_font_ui_tiny, color=TEXT_MUTED if _genau.spd_at_max else COLOR_TEXT),
                DashboardTextItem("v", layout.genau_spd_down, font=_font_ui_tiny, color=TEXT_MUTED if _genau.spd_at_min else COLOR_TEXT),
                DashboardTextItem("AMP", layout.genau_amp_label, font=_font_ui_tiny, rotation=90),
                DashboardTextItem("CTR", layout.genau_ctr_label, font=_font_ui_tiny, rotation=90),
                DashboardTextItem("SPD", layout.genau_spd_label, font=_font_ui_tiny, rotation=90),
                DashboardTextItem("cc", layout.genau_cruise, font=_font_ui_tiny),
                DashboardTextItem("h", layout.hybrid_mode_button, font=_font_ui_tiny),
            )
            if _is_genau else (
                DashboardTextItem("\u2212", layout.primary_nudge_prev, font=_font_ui_sm),
                DashboardTextItem("+", layout.primary_nudge_next, font=_font_ui_sm),
                DashboardTextItem("1/4", layout.hybrid_quarter_button, font=_font_ui_tiny),
                DashboardTextItem("\U0001F4C2", layout.hybrid_open_file_dialog, font=_font_emoji),
                DashboardTextItem("^", layout.hybrid_genau_amp_up, font=_font_ui_tiny, color=TEXT_MUTED if _genau.amp_at_max else COLOR_TEXT),
                DashboardTextItem("v", layout.hybrid_genau_amp_down, font=_font_ui_tiny, color=TEXT_MUTED if _genau.amp_at_min else COLOR_TEXT),
                DashboardTextItem("^", layout.hybrid_genau_ctr_up, font=_font_ui_tiny, color=TEXT_MUTED if _genau.ctr_at_max else COLOR_TEXT),
                DashboardTextItem("v", layout.hybrid_genau_ctr_down, font=_font_ui_tiny, color=TEXT_MUTED if _genau.ctr_at_min else COLOR_TEXT),
                # Hybrid SPD drives Nau's video or Genau per-stretch, so it is
                # never greyed by Genau's own limits (unlike amp/center above).
                DashboardTextItem("^", layout.hybrid_genau_spd_up, font=_font_ui_tiny, color=COLOR_TEXT),
                DashboardTextItem("v", layout.hybrid_genau_spd_down, font=_font_ui_tiny, color=COLOR_TEXT),
                DashboardTextItem("AMP", layout.hybrid_genau_amp_label, font=_font_ui_tiny, rotation=90),
                DashboardTextItem("CTR", layout.hybrid_genau_ctr_label, font=_font_ui_tiny, rotation=90),
                DashboardTextItem("SPD", layout.hybrid_genau_spd_label, font=_font_ui_tiny, rotation=90),
                DashboardTextItem("GA", layout.genau_takeover, font=_font_ui_tiny),
                DashboardTextItem("cc", layout.hybrid_cruise, font=_font_ui_tiny),
                DashboardTextItem("Nau", layout.hybrid_mode_button, font=_font_ui_tiny),
            )
            if _is_hybrid else (
                DashboardTextItem("\u2212", layout.primary_nudge_prev, font=_font_ui_sm),
                DashboardTextItem("+", layout.primary_nudge_next, font=_font_ui_sm),
                DashboardTextItem("\U0001F4C2", layout.open_file_dialog, font=_font_emoji),
                DashboardTextItem("\u23fa", layout.nau_record, font=_font_symbol),
                DashboardTextItem("GA", layout.genau_takeover, font=_font_ui_tiny),
                DashboardTextItem("h", layout.hybrid_mode_button, font=_font_ui_tiny),
            )
        ),
        DashboardTextItem("<", layout.landscape_prev, font=_font_ui_sm),
        DashboardTextItem(">", layout.landscape_next, font=_font_ui_sm),
        DashboardTextItem(ICON_LOCK, layout.landscape_lock, font=_font_emoji),
        DashboardTextItem(ICON_TRASH, layout.landscape_trash, font=_font_emoji),
        *(
            (DashboardTextItem("Nau", layout.genau_mode_toggle, font=_font_ui_tiny),)
            if _is_genau else ()
        ),
    )
    _icon_h = layout.broker_panel.height
    images = (
        DashboardImageItem(_load_icon_pixmap("icon.ico", layout.app_icon.height), layout.app_icon),
        DashboardImageItem(_load_icon_pixmap("broker_icon.ico", _icon_h), layout.broker_panel),
        DashboardImageItem(_load_icon_pixmap("fmode_icon.ico", _icon_h), layout.fmode_panel),
        DashboardImageItem(
            _draw_mic_pixmap(layout.voice_panel.width, layout.voice_panel.height),
            layout.voice_panel,
        ),
        *(
            (
                DashboardImageItem(
                    _draw_waveform_pixmap(_genau.shape, layout.genau_shape.width, layout.genau_shape.height),
                    layout.genau_shape,
                ),
            )
            if _is_genau else (
                DashboardImageItem(
                    _draw_waveform_pixmap(_genau.shape, layout.genau_shape.width, layout.genau_shape.height),
                    layout.genau_shape,
                ),
                DashboardImageItem(
                    _load_icon_pixmap("clipper_icon.ico", layout.clipper_save.height),
                    layout.clipper_save,
                ),
                DashboardImageItem(
                    _load_icon_pixmap("genau_icon.ico", layout.genau_mode_toggle.height),
                    layout.genau_mode_toggle,
                ),
            )
            if _is_hybrid else (
                DashboardImageItem(
                    _load_icon_pixmap("clipper_icon.ico", layout.clipper_save.height),
                    layout.clipper_save,
                ),
                DashboardImageItem(
                    _load_icon_pixmap("genau_icon.ico", layout.genau_mode_toggle.height),
                    layout.genau_mode_toggle,
                ),
            )
        ),
    )
    # Cable visual connecting OSR2 to Primary panel (simple straight line)
    cable_start_x = layout.osr2_panel.x + layout.osr2_panel.width
    cable_end_x = layout.primary_panel.x
    cable_y = layout.osr2_panel.y + layout.osr2_panel.height // 2
    cable_color = COLOR_CABLE
    cable_w = 2
    socket_r = 3
    cable_lines: tuple[DashboardLineItem, ...] = (
        DashboardLineItem(
            points=((cable_start_x, cable_y), (cable_end_x, cable_y)),
            color=cable_color, width=cable_w,
        ),
    )
    cable_ovals: tuple[DashboardOvalItem, ...] = (
        DashboardOvalItem(cx=cable_start_x, cy=cable_y, r=socket_r, fill=COLOR_BG, outline=cable_color),
        DashboardOvalItem(cx=cable_end_x, cy=cable_y, r=socket_r, fill=COLOR_BG, outline=cable_color),
    )
    cable_arcs: tuple[DashboardArcItem, ...] = ()

    # The log box carries a "Logs" title (in texts) with ruled lines below
    # standing in for the stream.  The lines start below the title — the top one
    # made way for it.
    log_lines: tuple[DashboardLineItem, ...] = tuple(
        DashboardLineItem(
            points=(
                (layout.log_panel.x + 4, y),
                (layout.log_panel.x + layout.log_panel.width - 4, y),
            ),
            color=TEXT_MUTED,
            width=1,
        )
        for y in range(layout.log_panel.y + 16, layout.log_panel.y + layout.log_panel.height - 4, 8)
    )

    scene = DashboardScene(
        width=layout.dashboard_width,
        height=layout.dashboard_height,
        rects=rects,
        texts=texts,
        images=images,
        hover_texts=(),  # derived from actions below so every button has one
        lines=cable_lines + log_lines,
        ovals=cable_ovals,
        arcs=cable_arcs,
        actions=(
            (QUIT_BUTTON, layout.quit_button),
            (OMNIPAUSE_TOGGLE, layout.omnipause_button),
            (HELP_REFERENCE, layout.help_button),
            (PORTRAIT_PREV, layout.portrait_prev),
            (PORTRAIT_NEXT, layout.portrait_next),
            (PORTRAIT_LOCK, layout.portrait_lock),
            (PORTRAIT_TRASH, layout.portrait_trash),
            (PRIMARY_PREV, layout.primary_prev),
            (PRIMARY_NEXT, layout.primary_next),
            *(
                (
                    (QUARTER_BUTTON, layout.quarter_button),
                    *(() if _genau.amp_at_max else ((GENAU_AMP_UP, layout.genau_amp_up),)),
                    *(() if _genau.amp_at_min else ((GENAU_AMP_DOWN, layout.genau_amp_down),)),
                    *(() if _genau.ctr_at_max else ((GENAU_CTR_UP, layout.genau_ctr_up),)),
                    *(() if _genau.ctr_at_min else ((GENAU_CTR_DOWN, layout.genau_ctr_down),)),
                    *(() if _genau.spd_at_max else ((GENAU_SPD_UP, layout.genau_spd_up),)),
                    *(() if _genau.spd_at_min else ((GENAU_SPD_DOWN, layout.genau_spd_down),)),
                    (GENAU_CRUISE, layout.genau_cruise),
                    (GENAU_SHAPE, layout.genau_shape),
                    (HYBRID_ACTIVATE, layout.hybrid_mode_button),
                    (NAU_ACTIVATE, layout.genau_mode_toggle),
                )
                if _is_genau else (
                    (PRIMARY_NUDGE_PREV, layout.primary_nudge_prev),
                    (PRIMARY_NUDGE_NEXT, layout.primary_nudge_next),
                    (QUARTER_BUTTON, layout.hybrid_quarter_button),
                    (OPEN_FILE_DIALOG, layout.hybrid_open_file_dialog),
                    (CLIPPER_SAVE, layout.clipper_save),
                    *(() if _genau.amp_at_max else ((GENAU_AMP_UP, layout.hybrid_genau_amp_up),)),
                    *(() if _genau.amp_at_min else ((GENAU_AMP_DOWN, layout.hybrid_genau_amp_down),)),
                    *(() if _genau.ctr_at_max else ((GENAU_CTR_UP, layout.hybrid_genau_ctr_up),)),
                    *(() if _genau.ctr_at_min else ((GENAU_CTR_DOWN, layout.hybrid_genau_ctr_down),)),
                    # Speed routes per-stretch in hybrid — to Nau's video on a
                    # scripted stretch, to Genau otherwise — so these stay live
                    # regardless of Genau's own limits (unlike amp/center, which
                    # only ever drive Genau).
                    (GENAU_SPD_UP, layout.hybrid_genau_spd_up),
                    (GENAU_SPD_DOWN, layout.hybrid_genau_spd_down),
                    (GENAU_TOGGLE_AUTO, layout.genau_takeover),
                    (GENAU_CRUISE, layout.hybrid_cruise),
                    (GENAU_SHAPE, layout.genau_shape),
                    (NAU_ACTIVATE, layout.hybrid_mode_button),
                    (GENAU_ACTIVATE, layout.genau_mode_toggle),
                )
                if _is_hybrid else (
                    (PRIMARY_NUDGE_PREV, layout.primary_nudge_prev),
                    (PRIMARY_NUDGE_NEXT, layout.primary_nudge_next),
                    (OPEN_FILE_DIALOG, layout.open_file_dialog),
                    (CLIPPER_SAVE, layout.clipper_save),
                    (NAU_RECORD, layout.nau_record),
                    (GENAU_TOGGLE_AUTO, layout.genau_takeover),
                    (HYBRID_ACTIVATE, layout.hybrid_mode_button),
                    (GENAU_ACTIVATE, layout.genau_mode_toggle),
                )
            ),
            (LANDSCAPE_PREV, layout.landscape_prev),
            (LANDSCAPE_NEXT, layout.landscape_next),
            (LANDSCAPE_LOCK, layout.landscape_lock),
            (LANDSCAPE_TRASH, layout.landscape_trash),
            (BROKER_PANEL, layout.broker_panel),
            (FMODE_PANEL, layout.fmode_panel),
            (VOICE_TOGGLE, layout.voice_panel),
        ),
    )
    hover_texts = tuple(
        (rect, text)
        for action_id, rect in scene.actions
        if (text := _action_tooltip(action_id, takeover_hover))
    )
    return replace(scene, hover_texts=hover_texts)


# ---------------------------------------------------------------------------
# PyQt6 rendering widget
# ---------------------------------------------------------------------------
from PyQt6.QtCore import Qt, QEvent, QRectF, QPointF, pyqtSignal
from PyQt6.QtWidgets import QWidget, QToolTip, QDialog, QHBoxLayout, QVBoxLayout, QTextBrowser
from PyQt6.QtGui import QPainter, QPen, QBrush, QPainterPath, QPixmap


class DashboardWidget(QWidget):
    """Custom widget that paints a DashboardScene using QPainter."""

    action_triggered = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene: DashboardScene | None = None
        self.setMouseTracking(True)

    def set_scene(self, scene: DashboardScene) -> None:
        self._scene = scene
        self.setFixedSize(scene.width, scene.height)
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802
        scene = self._scene
        if scene is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QBrush(COLOR_BG))

        _default_font = make_font(FONT_UI, SIZE_SMALL, bold=True)

        for item in scene.rects:
            p.setPen(QPen(item.outline, 1))
            p.setBrush(QBrush(item.fill))
            p.drawRect(item.rect.x, item.rect.y, item.rect.width, item.rect.height)

        for item in scene.lines:
            if len(item.points) < 2:
                continue
            pen = QPen(item.color, item.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if item.smooth and len(item.points) > 2:
                path = QPainterPath(QPointF(*item.points[0]))
                for pt in item.points[1:]:
                    path.lineTo(QPointF(*pt))
                p.drawPath(path)
            else:
                for i in range(len(item.points) - 1):
                    x1, y1 = item.points[i]
                    x2, y2 = item.points[i + 1]
                    p.drawLine(x1, y1, x2, y2)

        for item in scene.ovals:
            outline = item.outline if item.outline is not None else item.fill
            p.setPen(QPen(outline, item.outline_width))
            p.setBrush(QBrush(item.fill))
            p.drawEllipse(item.cx - item.r, item.cy - item.r, item.r * 2, item.r * 2)

        for item in scene.arcs:
            p.setPen(QPen(item.outline, item.width))
            p.setBrush(Qt.BrushStyle.NoBrush)
            # Qt arcs: angles in 1/16th of a degree, starting from 3 o'clock CCW
            p.drawArc(
                item.cx - item.r, item.cy - item.r, item.r * 2, item.r * 2,
                int(item.start * 16), int(item.extent * 16),
            )

        for item in scene.texts:
            if item.rect.width == 0 and item.rect.height == 0:
                continue
            p.setPen(QPen(item.color))
            p.setFont(item.font if item.font is not None else _default_font)
            rect = QRectF(item.rect.x, item.rect.y, item.rect.width, item.rect.height)
            flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            if item.anchor == "w":
                flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            elif item.anchor == "n":
                flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
            if item.rotation:
                p.save()
                cx = rect.x() + rect.width() / 2
                cy = rect.y() + rect.height() / 2
                p.translate(cx, cy)
                p.rotate(item.rotation)
                rotated = QRectF(-rect.height() / 2, -rect.width() / 2, rect.height(), rect.width())
                p.drawText(rotated, flags, item.text)
                p.restore()
            else:
                p.drawText(rect, flags, item.text)

        for item in scene.images:
            if item.pixmap.isNull():
                continue
            # Center the square pixmap within the (possibly wider) rect
            px_x = item.rect.x + (item.rect.width - item.pixmap.width()) // 2
            px_y = item.rect.y + (item.rect.height - item.pixmap.height()) // 2
            p.drawPixmap(px_x, px_y, item.pixmap)

        p.end()

    def mousePressEvent(self, event: object) -> None:  # noqa: N802
        scene = self._scene
        if scene is None:
            return
        pos = event.position()
        x, y = int(pos.x()), int(pos.y())
        for action_id, rect in scene.actions:
            if rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height:
                self.action_triggered.emit(action_id)
                return

    def mouseMoveEvent(self, event: object) -> None:  # noqa: N802
        scene = self._scene
        if scene is None:
            return
        pos = event.position()
        x, y = int(pos.x()), int(pos.y())
        for rect, text in scene.hover_texts:
            if rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height:
                QToolTip.showText(self.mapToGlobal(event.position().toPoint()), text, self)
                return
        QToolTip.hideText()


class ReferenceDialog(QDialog):
    """Modeless popup listing every hotkey and voice command.

    Carries no in-window heading — its content title lives on the window chrome
    ("Hotkeys & Voice Commands Reference") — and is sized/placed by the caller to fill the
    Random Favs Browser's rect.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hotkeys & Voice Commands Reference")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        icon_path = Path(__file__).resolve().parent.parent / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(False)
        browser.setHtml(render_reference_html())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(browser)


def write_dashboard_command(path: Path, action_id: str) -> None:
    """Post a dashboard button (or voice-toggle) action for the dispatch loop.

    Robust to the dispatch loop's ~20 Hz rename-drain of the same file: the append
    retries past the transient sharing violation rather than raising into the Qt
    slot.  Unhandled, that error propagates out of a click slot and PyQt6 aborts
    the whole window — the "power button closed the Dash instead of quitting Fun
    Time" bug — so a persistently locked file drops the line and the next click
    lands.
    """
    append_command(path, action_id)


def apply_dashboard_window_geometry(
    window: QWidget,
    snapshot: DashboardSnapshot | None,
    scene: DashboardScene,
    *,
    launch_geometry: DashboardLaunchGeometry | None = None,
) -> None:
    if launch_geometry is not None:
        window.setGeometry(
            launch_geometry.x, launch_geometry.y,
            launch_geometry.width, launch_geometry.height,
        )
        return
    if snapshot is None or snapshot.window.width <= 0 or snapshot.window.height <= 0:
        window.resize(scene.width, scene.height)
        return
    window.setGeometry(
        snapshot.window.x, snapshot.window.y,
        snapshot.window.width, snapshot.window.height,
    )


PRESS_FLASH_S = 0.2


from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QApplication


class DashboardWindow(QMainWindow):
    """Main dashboard window — PyQt6 equivalent of the old build_dashboard_window()."""

    _press_received = pyqtSignal()

    def __init__(
        self,
        app_config: DashboardAppConfig,
        preview_layout: DashboardPreviewLayout,
        *,
        launch_geometry: DashboardLaunchGeometry | None = None,
        rfb_rect: Rect | None = None,
        start_minimized: bool = False,
    ) -> None:
        super().__init__()
        self._app_config = app_config
        self._preview_layout = preview_layout
        self._launch_geometry = launch_geometry
        # The Random Favs Browser's screen rect; the reference popup opens over it.
        self._rfb_rect = rfb_rect
        # While the loading overlay is up the dashboard stays fully hidden so its
        # always-on-top window neither flashes above the overlay nor animates a
        # minimize on the way there (a hidden window renders nothing and the
        # geometry re-assert is gated on not-deferred).  We auto-detect that from
        # the loading screen's progress file and reveal ourselves once it is gone
        # — the launcher does not have to pass --start-minimized.  Neither a
        # loading-defer nor a persisted-minimized start may mirror its initial
        # off-screen state onto the other windows.
        self._deferred_for_loading = loading_screen_active(app_config.manifest_path.parent)
        self._suppress_minimize_routing = start_minimized or self._deferred_for_loading

        # Set on close, so the poller and press listener wind down with the
        # window instead of reading the player status files for the life of the
        # process.  Under test, several dashboards are built and closed in one
        # process, and leaked pollers would keep running past their window.
        self._stopping = threading.Event()
        self._pressed: dict[str, float] = {}
        self._reference_dialog: ReferenceDialog | None = None
        self._last_snapshot: DashboardSnapshot | None = None
        self._last_genau_status: GenauStatus | None = None
        self._press_queue: queue.Queue[str] = queue.Queue()
        self._player_cache: list[PlayerHydration] = [PlayerHydration()]

        self.setWindowTitle("Fun Time")
        icon_path = Path(__file__).resolve().parent.parent / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        # The window spans the whole left column: the schematic on the left and
        # the log stream filling the strip beside it.  The log used to be a second
        # top-level window the bridge tracked by title; embedding it as a child
        # lets it ride the dashboard's topmost band, minimize/restore and close.
        self._widget = DashboardWidget()
        self._widget.action_triggered.connect(self._on_action)
        state_dir = app_config.manifest_path.parent
        self._log_widget = LogPanelWidget(event_log_path(state_dir), prefs_path(state_dir))
        central = QWidget(self)
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._widget)
        central_layout.addWidget(self._log_widget, 1)
        self.setCentralWidget(central)

        if launch_geometry is not None:
            self.setGeometry(
                launch_geometry.x, launch_geometry.y,
                launch_geometry.width, launch_geometry.height,
            )

        # Title-bar controls: keep minimize + close, drop maximize (the schematic
        # is a fixed size).  Close routes through closeEvent (quits everything);
        # minimize routes through changeEvent (omniminimize).
        # Show in taskbar via WS_EX_APPWINDOW.
        # The subprocess is launched with SW_HIDE (hidden_subprocess_kwargs),
        # which PyQt6 inherits.  winId() realizes the native window handle
        # without showing it, so during the loading overlay the window stays
        # fully hidden — no flash, no minimize animation, nothing on screen —
        # and _maybe_reveal_after_loading shows it once the overlay closes.
        _hwnd = int(self.winId())
        self._dash_hwnd = _hwnd
        SW_HIDE = 0
        SW_SHOW = 5
        SW_SHOWMINNOACTIVE = 7
        if self._deferred_for_loading:
            ctypes.windll.user32.ShowWindow(_hwnd, SW_HIDE)
        elif start_minimized:
            self.show()
            ctypes.windll.user32.ShowWindow(_hwnd, SW_SHOWMINNOACTIVE)
        else:
            self.show()
            ctypes.windll.user32.ShowWindow(_hwnd, SW_SHOW)
        WS_SYSMENU = 0x00080000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000
        _style = ctypes.windll.user32.GetWindowLongW(_hwnd, -16)  # GWL_STYLE
        _style = (_style | WS_SYSMENU | WS_MINIMIZEBOX) & ~WS_MAXIMIZEBOX
        ctypes.windll.user32.SetWindowLongW(_hwnd, -16, _style)
        _ex = ctypes.windll.user32.GetWindowLongW(_hwnd, -20)  # GWL_EXSTYLE
        ctypes.windll.user32.SetWindowLongW(_hwnd, -20, (_ex | 0x00040000) & ~0x00000080)
        ctypes.windll.user32.SetWindowPos(
            _hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020,
        )

        self._ahk_cmd_file = app_config.manifest_path.parent / "ahk_cmd.txt"

        # UDP press listener
        self._press_received.connect(self._handle_press_event)
        self._press_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._press_sock.bind(("127.0.0.1", 0))
        press_port = self._press_sock.getsockname()[1]
        port_file = app_config.dashboard_state_file.parent / "dashboard_press_port.txt"
        port_file.parent.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(press_port), encoding="utf-8")
        threading.Thread(target=self._press_listener, daemon=True, name="press-listener").start()

        # Player status poller thread
        threading.Thread(target=self._player_poller, daemon=True, name="player-poller").start()

        # Notice overlays: flash each new event-log notice over the player it is
        # about.  A dedicated tail (its own offset) polls the shared file a touch
        # faster than the 500ms refresh so a "Clip saved" lands promptly.
        self._player_rects = self._compute_player_rects()
        self._notice_overlay = NoticeOverlay() if self._player_rects is not None else None
        self._notice_offset = 0
        self._notice_timer = QTimer(self)
        self._notice_timer.timeout.connect(self._poll_notices)
        self._notice_timer.start(250)

        # Refresh timer (500ms)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(500)
        self._refresh()

    def closeEvent(self, event: object) -> None:  # noqa: N802
        try:
            self._ahk_cmd_file.write_text("exit", encoding="utf-8")
        except OSError:
            pass
        self._stop_background_work()
        event.accept()

    def _stop_background_work(self) -> None:
        """Wind down the timers, threads, socket, and the log strip's tail.

        Closing the dashboard ends the session, so in production this only tidies
        up ahead of the process being killed.  It matters where a dashboard is
        built and closed inside a longer-lived process — the poller would
        otherwise keep reading the player status files forever.
        """
        self._stopping.set()
        self._refresh_timer.stop()
        self._notice_timer.stop()
        self._log_widget.shutdown()
        try:
            self._press_sock.close()  # unblocks the listener's recvfrom
        except OSError:
            pass
        if self._notice_overlay is not None:
            self._notice_overlay.shutdown()
            self._notice_overlay = None

    def changeEvent(self, event: object) -> None:  # noqa: N802
        """Mirror the dashboard's own minimize/restore onto every managed window.

        The dashboard cannot reach the other processes' windows directly, so it
        writes a command for the dispatch loop, which owns those handles.  This
        is what makes clicking the taskbar icon (which restores the dashboard)
        bring every window back.
        """
        if event.type() == QEvent.Type.WindowStateChange:
            now_minimized = self.isMinimized()
            was_minimized = bool(event.oldState() & Qt.WindowState.WindowMinimized)
            self._maybe_route_omniminimize(now_minimized=now_minimized, was_minimized=was_minimized)
            self._maybe_route_omnirestore(now_minimized=now_minimized, was_minimized=was_minimized)
        super().changeEvent(event)

    def _maybe_route_omniminimize(self, *, now_minimized: bool, was_minimized: bool) -> None:
        """Write the omniminimize command on the not-minimized -> minimized edge only."""
        if self._suppress_minimize_routing:
            return  # startup minimize (loading overlay) — not a user gesture
        if now_minimized and not was_minimized:
            write_dashboard_command(self._app_config.dashboard_cmd_file, OMNIMINIMIZE)

    def _maybe_route_omnirestore(self, *, now_minimized: bool, was_minimized: bool) -> None:
        """Write the omnirestore command on the minimized -> not-minimized edge only."""
        if was_minimized and not now_minimized and self._suppress_minimize_routing:
            # The post-loading reveal restored us; routing is live from here.
            self._suppress_minimize_routing = False
            return
        if was_minimized and not now_minimized:
            write_dashboard_command(self._app_config.dashboard_cmd_file, OMNIRESTORE)

    def _maybe_reveal_after_loading(self) -> None:
        """Show the window once the loading overlay is gone.

        The dashboard stays fully hidden (SW_HIDE, never Qt-shown) while the
        overlay is up, so it neither flashes above the overlay nor animates a
        minimize.  The overlay deletes its progress file when it closes, which
        is our cue to reveal.  Revealing from hidden does not fire a
        minimize->restore edge, so we clear the startup-minimize suppression
        here rather than relying on _maybe_route_omnirestore.
        """
        if not self._deferred_for_loading:
            return
        if loading_screen_active(self._app_config.manifest_path.parent):
            return
        self._deferred_for_loading = False
        self._suppress_minimize_routing = False
        self.show()
        SW_SHOW = 5
        ctypes.windll.user32.ShowWindow(self._dash_hwnd, SW_SHOW)
        ctypes.windll.user32.SetWindowPos(
            self._dash_hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020,
        )

    def _compute_pressed(self) -> frozenset[str]:
        now = time.monotonic()
        active = frozenset(aid for aid, t in self._pressed.items() if now - t < PRESS_FLASH_S)
        for aid in [a for a, t in self._pressed.items() if now - t >= PRESS_FLASH_S]:
            del self._pressed[aid]
        return active

    def _sync_own_topmost(self, omni_paused: bool) -> None:
        """Keep the dashboard's own topmost band in step with OmniPause.

        The dashboard floats over the players via WindowStaysOnTopHint, but
        OmniPause must free the desktop.  The orchestrator tries to drop it, yet
        its lookup for this Qt window (whose pid differs from the launcher's)
        intermittently fails, so the dashboard corrects its OWN band here using
        its reliable handle: non-topmost while paused, topmost otherwise.  It is
        drift correction — SetWindowPos runs only when the actual band differs
        from the desired one, so a Qt re-assert of the hint is undone on the next
        refresh with no flicker in the steady state.
        """
        desired_topmost = not omni_paused
        if is_window_topmost(self._dash_hwnd) != desired_topmost:
            set_always_on_top(self._dash_hwnd, desired_topmost)

    def _do_render(
        self,
        snapshot: DashboardSnapshot | None,
        pressed_actions: frozenset[str],
        genau_status: GenauStatus | None = None,
    ) -> None:
        self._last_snapshot = snapshot
        self._last_genau_status = genau_status
        # OmniPause must free the desktop; drop our own topmost while paused
        # (the orchestrator's drop of this window is unreliable) and restore it
        # after.  See _sync_own_topmost.  The log strip is a child widget, so it
        # rides this window's band automatically.
        omni_paused = snapshot is not None and snapshot.omni_paused
        self._sync_own_topmost(omni_paused)
        state_dir = self._app_config.dashboard_state_file.parent
        scene = build_dashboard_scene(
            self._preview_layout,
            snapshot,
            favs_file=self._app_config.favs_file,
            broker_heartbeat_file=self._app_config.broker_heartbeat_file,
            genau_status=genau_status,
            genau_takeover_allowed=read_genau_enabled(genau_enabled_path(state_dir)),
            pressed_actions=pressed_actions,
        )
        # While minimized, re-asserting geometry would restore the window and
        # fight the omniminimize — leave it minimized until the user restores it.
        # While deferred for loading it is hidden; don't touch it until reveal.
        if not self.isMinimized() and not self._deferred_for_loading:
            apply_dashboard_window_geometry(self, snapshot, scene, launch_geometry=self._launch_geometry)
        self._widget.set_scene(scene)

    def _on_action(self, action_id: str) -> None:
        if action_id == HELP_REFERENCE:
            self._toggle_reference_dialog()
            return
        self._pressed[action_id] = time.monotonic()
        write_dashboard_command(self._app_config.dashboard_cmd_file, action_id)
        gs = self._last_genau_status
        self._do_render(self._last_snapshot, self._compute_pressed(), genau_status=gs)
        QTimer.singleShot(
            int(PRESS_FLASH_S * 1000) + 10,
            lambda: self._do_render(self._last_snapshot, self._compute_pressed(), genau_status=gs),
        )
        if action_id.startswith("genau_"):
            QTimer.singleShot(100, self._refresh)

    def _toggle_reference_dialog(self) -> None:
        """Open the reference popup, or close it if it is already showing.

        Drives both the ``?`` button and the "help"/"reference"/… voice phrases:
        the same trigger opens and dismisses.
        """
        if self._reference_dialog is not None and self._reference_dialog.isVisible():
            self._reference_dialog.close()
        else:
            self._show_reference_dialog()

    def _show_reference_dialog(self) -> None:
        """Open (or re-focus) the hotkey/voice reference popup.

        On first open it is sized and placed to fill the Random Favs Browser's
        rect, so the reference occupies the exact same space; later opens keep
        wherever the user moved it.
        """
        if self._reference_dialog is None:
            self._reference_dialog = ReferenceDialog(self)
            if self._rfb_rect is not None:
                self._fit_reference_frame_to_rect(self._rfb_rect)
        self._reference_dialog.show()
        self._reference_dialog.raise_()
        self._reference_dialog.activateWindow()

    def _fit_reference_frame_to_rect(self, rect: Rect) -> None:
        """Size the reference popup so its whole frame — title bar included —
        fills *rect*, rather than its client area (which left the chrome
        overhanging the top).  Frame margins are known only once the window is
        realized, so place it at the rect, show it, measure, then inset the
        client to fill the frame."""
        dialog = self._reference_dialog
        assert dialog is not None
        dialog.setGeometry(rect.x, rect.y, rect.width, rect.height)
        dialog.show()
        frame = dialog.frameGeometry()
        client = dialog.geometry()
        x, y, w, h = client_rect_filling_frame(
            rect,
            left=client.left() - frame.left(),
            top=client.top() - frame.top(),
            right=frame.right() - client.right(),
            bottom=frame.bottom() - client.bottom(),
        )
        dialog.setGeometry(x, y, w, h)

    def _close_reference_dialog(self) -> None:
        """Dismiss the reference popup if it is open (the "close …" voice phrases)."""
        if self._reference_dialog is not None:
            self._reference_dialog.close()

    def _handle_press_event(self) -> None:
        toggle_reference = False
        close_reference = False
        while True:
            try:
                action = self._press_queue.get_nowait()
                if action == HELP_REFERENCE:
                    toggle_reference = True
                elif action == HELP_REFERENCE_CLOSE:
                    close_reference = True
                self._pressed[action] = time.monotonic()
            except queue.Empty:
                break
        # Voice arrives here as a press (the ? button drives _on_action directly):
        # "help"/… toggles the popup, "close help"/… only dismisses it.
        if close_reference:
            self._close_reference_dialog()
        if toggle_reference:
            self._toggle_reference_dialog()
        gs = self._last_genau_status
        self._do_render(self._last_snapshot, self._compute_pressed(), genau_status=gs)
        QTimer.singleShot(
            int(PRESS_FLASH_S * 1000) + 10,
            lambda: self._do_render(self._last_snapshot, self._compute_pressed(), genau_status=gs),
        )

    def _press_listener(self) -> None:
        while not self._stopping.is_set():
            try:
                data, _ = self._press_sock.recvfrom(256)
                self._press_queue.put(data.decode("utf-8").strip())
                self._press_received.emit()
            except OSError:
                break

    def _player_poller(self) -> None:
        while not self._stopping.is_set():
            self._player_cache[0] = poll_players(self._app_config)
            self._stopping.wait(0.5)

    def _compute_player_rects(self) -> PlayerRects | None:
        """Where each notice-bearing window sits, in real screen coordinates.

        Derived from the same layout functions startup positions the windows
        with, so the overlay lands on the window rather than near it.  Returns
        None when the monitors can't be read (e.g. a headless run) so notices
        simply don't flash instead of crashing the dashboard.
        """
        try:
            monitors = enumerate_monitors()
            main_rect, secondary_rect = get_logical_monitor_rects(
                monitors,
                main_index=self._app_config.layout.main_monitor,
                secondary_index=self._app_config.layout.secondary_monitor,
            )
        except (ValueError, OSError):
            return None
        plan = compute_window_layout(
            main_monitor=main_rect,
            secondary_monitor=secondary_rect,
            layout_config=self._app_config.layout,
        )
        primary = compute_primary_media_rect(
            secondary_monitor=secondary_rect, layout_config=self._app_config.layout,
        )
        as_rect = lambda w: Rect(w.x, w.y, w.width, w.height)  # noqa: E731
        return PlayerRects(
            primary=as_rect(primary),
            portrait=as_rect(plan.portrait),
            landscape=as_rect(plan.landscape),
            dash=as_rect(plan.dashboard),
        )

    def _poll_notices(self) -> None:
        """Flash every new announcement over the player it concerns."""
        if self._notice_overlay is None or self._player_rects is None:
            return
        if self._deferred_for_loading:
            return
        records, self._notice_offset = read_events(
            self._app_config.dashboard_state_file.parent / EVENT_LOG_FILENAME,
            self._notice_offset,
        )
        for record in records:
            if is_announcement(record):
                target = notice_target_rect(record.source, self._player_rects)
                self._notice_overlay.flash(record, target)

    def _refresh(self) -> None:
        self._maybe_reveal_after_loading()
        snapshot = load_dashboard_snapshot(self._app_config.dashboard_state_file)
        if snapshot is not None:
            snapshot = hydrate_dashboard_snapshot(snapshot, self._player_cache[0])
        genau_status_path = self._app_config.dashboard_state_file.parent / "genau_status.txt"
        genau_status = read_genau_status(genau_status_path)
        self._do_render(snapshot, self._compute_pressed(), genau_status=genau_status)


def build_dashboard_window(
    app_config: DashboardAppConfig,
    *,
    launch_geometry: DashboardLaunchGeometry | None = None,
    rfb_rect: Rect | None = None,
) -> DashboardWindow:
    main_monitor, secondary_monitor = get_preview_monitor_sizes(app_config)
    preview_layout = compute_dashboard_preview_layout(
        main_monitor, secondary_monitor, app_config.layout,
    )
    return DashboardWindow(
        app_config, preview_layout,
        launch_geometry=launch_geometry,
        rfb_rect=rfb_rect,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Fun Time dashboard preview app")
    parser.add_argument(
        "manifest_path",
        nargs="?",
        default=str(Path("state") / WINDOWS_BRIDGE_MANIFEST_FILENAME),
        help="Path to the Windows bridge launch manifest",
    )
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    # The Random Favs Browser's rect — the reference popup opens over it.
    parser.add_argument("--rfb-x", type=int)
    parser.add_argument("--rfb-y", type=int)
    parser.add_argument("--rfb-width", type=int)
    parser.add_argument("--rfb-height", type=int)
    parser.add_argument(
        "--start-minimized",
        action="store_true",
        help="Start minimized (used while the loading screen covers startup)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Set AppUserModelID before any window creation so the taskbar can group
    # this process's windows with the pinned "Fun Time" shortcut.
    from .win32 import APP_USER_MODEL_ID, set_app_user_model_id
    try:
        set_app_user_model_id(APP_USER_MODEL_ID)
    except OSError:
        pass  # Non-fatal — taskbar grouping just won't work

    app = QApplication.instance() or QApplication([])

    app_config = load_dashboard_app_config(Path(args.manifest_path))
    launch_geometry = None
    if None not in {args.x, args.y, args.width, args.height}:
        launch_geometry = DashboardLaunchGeometry(
            x=args.x,
            y=args.y,
            width=args.width,
            height=args.height,
        )
    rfb_rect = None
    if None not in {args.rfb_x, args.rfb_y, args.rfb_width, args.rfb_height}:
        rfb_rect = Rect(args.rfb_x, args.rfb_y, args.rfb_width, args.rfb_height)
    _window = build_dashboard_window(
        app_config,
        launch_geometry=launch_geometry,
        rfb_rect=rfb_rect,
    )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


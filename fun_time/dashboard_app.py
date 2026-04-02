from __future__ import annotations

import argparse
import configparser
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from dataclasses import replace
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
    BORDER_PANEL,
    CABLE_ACTIVE,
    CABLE_INACTIVE,
    GREEN,
    PINK,
    RED,
    TEXT_PRIMARY,
)
from shared_ui.fonts import (
    FONT_EMOJI,
    FONT_SYMBOL,
    FONT_UI,
    SIZE_SMALL,
    SIZE_TINY,
    make_font,
)

from fun_time.config import LayoutConfig
from fun_time.manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.vlc_actions import get_current_file_path, vlc_http_req
from fun_time.dashboard_actions import (
    BROKER_PANEL,
    CLIPPER_SAVE,
    LANDSCAPE_LOCK,
    LANDSCAPE_NEXT,
    LANDSCAPE_PREV,
    LANDSCAPE_TRASH,
    FMODE_PANEL,
    LINK_TOGGLE,
    OMNIPAUSE_TOGGLE,
    OPEN_FILE_DIALOG,
    PORTRAIT_LOCK,
    PORTRAIT_NEXT,
    PORTRAIT_PREV,
    PORTRAIT_TRASH,
    PRIMARY_NEXT,
    PRIMARY_PREV,
    QUARTER_BUTTON,
    QUIT_BUTTON,
    VLC_NUDGE_NEXT,
    VLC_NUDGE_PREV,
)
from fun_time.dashboard_layout import DashboardPreviewLayout, Rect, Size, compute_dashboard_preview_layout
from fun_time.dashboard_runtime import DashboardSnapshot, is_broker_heartbeat_fresh, load_dashboard_snapshot
from fun_time.dashboard_state import (
    LABEL_LANDSCAPE_VLC,
    LABEL_MFP,
    LABEL_OSR2,
    LABEL_PORTRAIT_VLC,
    LABEL_PRIMARY_ROBOT,
    LABEL_PRIMARY_VLC,
    has_matching_funscript,
    is_favorite_path,
    primary_panel_should_highlight,
    read_favs_content,
    satellite_panel_should_highlight,
)

# Semantic aliases — map old Dashboard names to shared_ui tokens.
COLOR_BG = BG_PRIMARY
COLOR_PANEL = BG_SECONDARY
COLOR_TEXT = TEXT_PRIMARY
COLOR_CABLE = CABLE_ACTIVE
COLOR_CABLE_DIM = CABLE_INACTIVE
COLOR_GREEN = GREEN
COLOR_PINK = PINK
COLOR_RED = RED
COLOR_YELLOW = AMBER

ICON_LOCK = "\U0001F512"
ICON_TRASH = "\U0001F5D1"

SIZE_CHIP = 7  # below SIZE_TINY — used for broker/fmode chip labels


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
    primary_sources: str
    favs_file: Path
    primary_vlc_port: int
    portrait_vlc_port: int
    landscape_vlc_port: int
    vlc_password: str
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
        mfp_width_ratio=parser.getfloat("layout", "mfp_width_ratio"),
        mfp_height_ratio=parser.getfloat("layout", "mfp_height_ratio"),
        left_partition_top_ratio=parser.getfloat("layout", "left_partition_top_ratio", fallback=0.0),
        left_partition_bottom_ratio=parser.getfloat("layout", "left_partition_bottom_ratio", fallback=0.0),
    )
    return DashboardAppConfig(
        layout=layout,
        manifest_path=manifest_path,
        primary_sources=parser.get("media", "primary_vlc_sources", fallback=""),
        favs_file=Path(parser.get("media", "favs_file", fallback="favs.csv")),
        primary_vlc_port=parser.getint("vlc", "primary_vlc_port", fallback=8090),
        portrait_vlc_port=parser.getint("vlc", "vlc2_port", fallback=8091),
        landscape_vlc_port=parser.getint("vlc", "vlc3_port", fallback=8092),
        vlc_password=parser.get("vlc", "vlc_pass", fallback=""),
        broker_heartbeat_file=Path(parser.get("commands", "broker_heartbeat_file", fallback="broker_heartbeat.txt")),
        dashboard_state_file=Path(parser.get("commands", "dashboard_state_file", fallback="dashboard_state.ini")),
        dashboard_cmd_file=Path(parser.get("commands", "dashboard_cmd_file", fallback="dashboard_cmd.txt")),
    )


def is_process_alive(pid: int) -> bool:
    """Check whether *pid* refers to a running process.

    Uses OpenProcess on Windows because os.kill(pid, 0) raises
    WinError 87 (ERROR_INVALID_PARAMETER) for valid PIDs on
    Python 3.14 / Windows 11.
    """
    if pid <= 0:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
    )
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


@dataclass(frozen=True)
class VlcHydration:
    primary_path: str = ""
    portrait_path: str = ""
    landscape_path: str = ""
    primary_responsive: bool = False


def poll_vlc(app_config: DashboardAppConfig) -> VlcHydration:
    primary_path = get_current_file_path(app_config.primary_vlc_port, app_config.vlc_password)
    portrait_path = get_current_file_path(app_config.portrait_vlc_port, app_config.vlc_password)
    landscape_path = get_current_file_path(app_config.landscape_vlc_port, app_config.vlc_password)
    status, xml = vlc_http_req(app_config.primary_vlc_port, "/requests/status.xml", app_config.vlc_password)
    return VlcHydration(
        primary_path=primary_path,
        portrait_path=portrait_path,
        landscape_path=landscape_path,
        primary_responsive=status == 200 and "<state>" in xml,
    )


def hydrate_dashboard_snapshot(snapshot: DashboardSnapshot, vlc: VlcHydration, *, mfp_pid: int = 0) -> DashboardSnapshot:
    return replace(
        snapshot,
        primary_responsive=vlc.primary_responsive,
        mfp_alive=is_process_alive(mfp_pid),
        primary=replace(snapshot.primary, path=vlc.primary_path),
        portrait=replace(snapshot.portrait, path=vlc.portrait_path),
        landscape=replace(snapshot.landscape, path=vlc.landscape_path),
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


def build_dashboard_scene(
    layout: DashboardPreviewLayout,
    snapshot: DashboardSnapshot | None = None,
    *,
    favs_file: Path | None = None,
    broker_heartbeat_file: Path | None = None,
    pressed_actions: frozenset[str] = frozenset(),
) -> DashboardScene:
    primary_label = LABEL_PRIMARY_VLC
    portrait_label = LABEL_PORTRAIT_VLC
    landscape_label = LABEL_LANDSCAPE_VLC
    osr2_label = LABEL_OSR2
    mfp_label = LABEL_MFP
    cable_connected = True
    broker_chip = "b"
    fmode_chip = "f"
    primary_fill = COLOR_PANEL
    portrait_fill = COLOR_PANEL
    landscape_fill = COLOR_PANEL
    osr2_fill = COLOR_PANEL
    mfp_fill = COLOR_PANEL
    broker_fill = COLOR_PANEL
    fmode_fill = COLOR_PANEL
    portrait_lock_fill = COLOR_PANEL
    landscape_lock_fill = COLOR_PANEL

    if snapshot is not None:
        favs_content = read_favs_content(favs_file) if favs_file is not None else ""
        broker_running = is_broker_heartbeat_fresh(broker_heartbeat_file) if broker_heartbeat_file is not None else False
        mfp_connected = snapshot.mfp_alive and snapshot.primary_responsive and broker_running
        primary_label_name = LABEL_PRIMARY_ROBOT if snapshot.primary_uses_robot_hand else LABEL_PRIMARY_VLC
        primary_label = primary_label_name
        portrait_label = LABEL_PORTRAIT_VLC
        landscape_label = LABEL_LANDSCAPE_VLC
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
        mfp_label = LABEL_MFP
        cable_connected = snapshot.robot_link_enabled
        if primary_panel_should_highlight(
            f_mode_enabled=snapshot.f_mode_enabled,
            primary_path=snapshot.primary.path,
            has_matching_funscript=primary_funscript_exists,
        ):
            primary_fill = COLOR_GREEN
        elif snapshot.primary_uses_robot_hand:
            primary_fill = COLOR_PINK
        else:
            primary_fill = COLOR_PANEL
        portrait_fill = COLOR_GREEN if satellite_panel_should_highlight(
            f_mode_enabled=snapshot.f_mode_enabled,
            is_favorite=is_favorite_path(snapshot.portrait.path, favs_content),
        ) else COLOR_PANEL
        landscape_fill = COLOR_GREEN if satellite_panel_should_highlight(
            f_mode_enabled=snapshot.f_mode_enabled,
            is_favorite=is_favorite_path(snapshot.landscape.path, favs_content),
        ) else COLOR_PANEL
        if snapshot.osr2_mode == "off":
            osr2_fill = COLOR_PANEL
        elif snapshot.osr2_mode == "auto":
            osr2_fill = COLOR_PINK
        elif funscript_active:
            osr2_fill = COLOR_GREEN
        else:
            osr2_fill = COLOR_PANEL
        mfp_fill = COLOR_GREEN if mfp_connected else COLOR_RED
        broker_fill = COLOR_GREEN if broker_running else COLOR_RED
        fmode_fill = COLOR_GREEN if snapshot.f_mode_enabled else COLOR_PANEL
        portrait_lock_fill = COLOR_GREEN if snapshot.portrait.locked else COLOR_PANEL
        landscape_lock_fill = COLOR_GREEN if snapshot.landscape.locked else COLOR_PANEL

    omni_paused = snapshot is not None and snapshot.omni_paused
    omnipause_icon = "\u25B6" if omni_paused else "\u23F8"
    omnipause_fill = COLOR_PANEL

    def _press_fill(fill: QColor, action_id: str) -> QColor:
        return lighten_color(fill) if action_id in pressed_actions else fill

    rects = (
        DashboardRectItem(layout.main_monitor, fill=COLOR_PANEL),
        DashboardRectItem(layout.secondary_monitor, fill=COLOR_PANEL),
        DashboardRectItem(layout.rfb_panel, fill=COLOR_PANEL),
        DashboardRectItem(layout.main_status_strip, fill=COLOR_PANEL),
        DashboardRectItem(layout.quit_button, fill=_press_fill(COLOR_PANEL, QUIT_BUTTON)),
        DashboardRectItem(layout.omnipause_button, fill=_press_fill(omnipause_fill, OMNIPAUSE_TOGGLE)),
        DashboardRectItem(layout.mfp_panel, fill=mfp_fill),
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
            (DashboardRectItem(layout.quarter_button, fill=_press_fill(COLOR_PANEL, QUARTER_BUTTON)),)
            if snapshot is not None and snapshot.primary_uses_robot_hand else (
                DashboardRectItem(layout.vlc_nudge_prev, fill=_press_fill(COLOR_PANEL, VLC_NUDGE_PREV)),
                DashboardRectItem(layout.vlc_nudge_next, fill=_press_fill(COLOR_PANEL, VLC_NUDGE_NEXT)),
                DashboardRectItem(layout.open_file_dialog, fill=_press_fill(COLOR_PANEL, OPEN_FILE_DIALOG)),
                DashboardRectItem(layout.clipper_save, fill=_press_fill(COLOR_PANEL, CLIPPER_SAVE)),
            )
        ),
        DashboardRectItem(layout.landscape_prev, fill=_press_fill(COLOR_PANEL, LANDSCAPE_PREV)),
        DashboardRectItem(layout.landscape_next, fill=_press_fill(COLOR_PANEL, LANDSCAPE_NEXT)),
        DashboardRectItem(layout.landscape_lock, fill=_press_fill(landscape_lock_fill, LANDSCAPE_LOCK)),
        DashboardRectItem(layout.landscape_trash, fill=_press_fill(COLOR_PANEL, LANDSCAPE_TRASH)),
        DashboardRectItem(layout.broker_panel, fill=_press_fill(broker_fill, BROKER_PANEL)),
        DashboardRectItem(layout.fmode_panel, fill=_press_fill(fmode_fill, FMODE_PANEL)),
    )
    _font_symbol = make_font(FONT_SYMBOL, 10, bold=True)
    _font_ui_sm = make_font(FONT_UI, SIZE_SMALL, bold=True)
    _font_emoji = make_font(FONT_EMOJI, SIZE_SMALL)
    _font_ui_tiny = make_font(FONT_UI, SIZE_TINY, bold=True)
    _font_chip = make_font(FONT_UI, SIZE_CHIP, bold=True)
    texts = (
        DashboardTextItem("\u23FB", layout.quit_button, font=_font_symbol),
        DashboardTextItem(omnipause_icon, layout.omnipause_button, font=_font_symbol),
        DashboardTextItem(mfp_label, layout.mfp_panel, anchor="n"),
        DashboardTextItem(landscape_label, layout.landscape_panel, anchor="n"),
        DashboardTextItem(portrait_label, layout.portrait_panel, anchor="n"),
        DashboardTextItem(primary_label, layout.primary_panel, anchor="n"),
        DashboardTextItem(osr2_label, layout.osr2_panel, anchor="n"),
        DashboardTextItem("<", layout.portrait_prev, font=_font_ui_sm),
        DashboardTextItem(">", layout.portrait_next, font=_font_ui_sm),
        DashboardTextItem(ICON_LOCK, layout.portrait_lock, font=_font_emoji),
        DashboardTextItem(ICON_TRASH, layout.portrait_trash, font=_font_emoji),
        DashboardTextItem("<", layout.primary_prev, font=_font_ui_sm),
        DashboardTextItem(">", layout.primary_next, font=_font_ui_sm),
        *(
            (DashboardTextItem("1/4", layout.quarter_button, font=_font_ui_tiny),)
            if snapshot is not None and snapshot.primary_uses_robot_hand else (
                DashboardTextItem("\u2212", layout.vlc_nudge_prev, font=_font_ui_sm),
                DashboardTextItem("+", layout.vlc_nudge_next, font=_font_ui_sm),
                DashboardTextItem("\U0001F4C2", layout.open_file_dialog, font=_font_emoji),
                DashboardTextItem("[ ]", layout.clipper_save, font=_font_ui_tiny),
            )
        ),
        DashboardTextItem("<", layout.landscape_prev, font=_font_ui_sm),
        DashboardTextItem(">", layout.landscape_next, font=_font_ui_sm),
        DashboardTextItem(ICON_LOCK, layout.landscape_lock, font=_font_emoji),
        DashboardTextItem(ICON_TRASH, layout.landscape_trash, font=_font_emoji),
        DashboardTextItem(broker_chip, layout.broker_panel, font=_font_chip),
        DashboardTextItem(fmode_chip, layout.fmode_panel, font=_font_chip),
    )
    # Cable visual connecting OSR2 to Primary panel (schematic style)
    cable_y = layout.link_toggle.y + layout.link_toggle.height // 2
    cable_start_x = layout.osr2_panel.x + layout.osr2_panel.width
    cable_end_x = layout.primary_panel.x
    mid_x = (cable_start_x + cable_end_x) // 2
    cable_color = COLOR_CABLE if cable_connected else COLOR_CABLE_DIM
    if LINK_TOGGLE in pressed_actions:
        cable_color = lighten_color(cable_color)
    cable_w = 2
    socket_r = 3
    node_r = 4
    dot_r = 2
    arc_r = 3
    cable_arcs: tuple[DashboardArcItem, ...] = ()
    if cable_connected:
        # Two line halves meeting at midpoint node
        cable_lines: tuple[DashboardLineItem, ...] = (
            DashboardLineItem(
                points=((cable_start_x, cable_y), (mid_x - node_r, cable_y)),
                color=cable_color, width=cable_w,
            ),
            DashboardLineItem(
                points=((mid_x + node_r, cable_y), (cable_end_x, cable_y)),
                color=cable_color, width=cable_w,
            ),
        )
        # Sockets at endpoints + midpoint node (outer ring + inner dot)
        cable_ovals: tuple[DashboardOvalItem, ...] = (
            DashboardOvalItem(cx=cable_start_x, cy=cable_y, r=socket_r, fill=COLOR_BG, outline=cable_color),
            DashboardOvalItem(cx=cable_end_x, cy=cable_y, r=socket_r, fill=COLOR_BG, outline=cable_color),
            DashboardOvalItem(cx=mid_x, cy=cable_y, r=node_r, fill=COLOR_PANEL, outline=cable_color),
            DashboardOvalItem(cx=mid_x, cy=cable_y, r=dot_r, fill=cable_color, outline=cable_color),
        )
    else:
        # Two fragments curling apart at the break
        curl = 6
        gap = 8
        cable_lines = (
            DashboardLineItem(
                points=(
                    (cable_start_x, cable_y),
                    (mid_x - gap - 4, cable_y),
                    (mid_x - gap, cable_y),
                    (mid_x - gap + 2, cable_y - curl),
                ),
                color=cable_color, width=cable_w, smooth=True,
            ),
            DashboardLineItem(
                points=(
                    (cable_end_x, cable_y),
                    (mid_x + gap + 4, cable_y),
                    (mid_x + gap, cable_y),
                    (mid_x + gap - 2, cable_y + curl),
                ),
                color=cable_color, width=cable_w, smooth=True,
            ),
        )
        cable_ovals = (
            DashboardOvalItem(cx=cable_start_x, cy=cable_y, r=socket_r, fill=COLOR_BG, outline=cable_color),
            DashboardOvalItem(cx=cable_end_x, cy=cable_y, r=socket_r, fill=COLOR_BG, outline=cable_color),
        )
        # Half-circle remnants of broken midpoint node
        cable_arcs = (
            DashboardArcItem(cx=mid_x - gap + 2, cy=cable_y - curl, r=arc_r, start=270, extent=180, outline=cable_color),
            DashboardArcItem(cx=mid_x + gap - 2, cy=cable_y + curl, r=arc_r, start=90, extent=180, outline=cable_color),
        )

    return DashboardScene(
        width=layout.dashboard_width,
        height=layout.dashboard_height,
        rects=rects,
        texts=texts,
        hover_texts=(
            (layout.quit_button, "Quit"),
            (layout.omnipause_button, "Pause all"),
            (layout.broker_panel, "Broker"),
            (layout.fmode_panel, "F-Mode"),
        ),
        lines=cable_lines,
        ovals=cable_ovals,
        arcs=cable_arcs,
        actions=(
            (QUIT_BUTTON, layout.quit_button),
            (OMNIPAUSE_TOGGLE, layout.omnipause_button),
            (PORTRAIT_PREV, layout.portrait_prev),
            (PORTRAIT_NEXT, layout.portrait_next),
            (PORTRAIT_LOCK, layout.portrait_lock),
            (PORTRAIT_TRASH, layout.portrait_trash),
            (PRIMARY_PREV, layout.primary_prev),
            (PRIMARY_NEXT, layout.primary_next),
            *(
                ((QUARTER_BUTTON, layout.quarter_button),)
                if snapshot is not None and snapshot.primary_uses_robot_hand else (
                    (VLC_NUDGE_PREV, layout.vlc_nudge_prev),
                    (VLC_NUDGE_NEXT, layout.vlc_nudge_next),
                    (OPEN_FILE_DIALOG, layout.open_file_dialog),
                    (CLIPPER_SAVE, layout.clipper_save),
                )
            ),
            (LANDSCAPE_PREV, layout.landscape_prev),
            (LANDSCAPE_NEXT, layout.landscape_next),
            (LANDSCAPE_LOCK, layout.landscape_lock),
            (LANDSCAPE_TRASH, layout.landscape_trash),
            (LINK_TOGGLE, layout.link_toggle),
            (BROKER_PANEL, layout.broker_panel),
            (FMODE_PANEL, layout.fmode_panel),
        ),
    )


# ---------------------------------------------------------------------------
# PyQt6 rendering widget
# ---------------------------------------------------------------------------
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtWidgets import QWidget, QToolTip
from PyQt6.QtGui import QPainter, QPen, QBrush, QPainterPath


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
            p.drawText(rect, flags, item.text)

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


def write_dashboard_command(path: Path, action_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(action_id, encoding="utf-8")


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
        mfp_pid: int = 0,
    ) -> None:
        super().__init__()
        self._app_config = app_config
        self._preview_layout = preview_layout
        self._launch_geometry = launch_geometry
        self._mfp_pid = mfp_pid

        self._pressed: dict[str, float] = {}
        self._last_snapshot: DashboardSnapshot | None = None
        self._press_queue: queue.Queue[str] = queue.Queue()
        self._vlc_cache: list[VlcHydration] = [VlcHydration()]

        self.setWindowTitle("Fun Time")
        icon_path = Path(__file__).resolve().parent.parent / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )

        self._widget = DashboardWidget(self)
        self.setCentralWidget(self._widget)
        self._widget.action_triggered.connect(self._on_action)

        if launch_geometry is not None:
            self.setGeometry(
                launch_geometry.x, launch_geometry.y,
                launch_geometry.width, launch_geometry.height,
            )

        # Remove minimize/maximize/close buttons via Win32, keep title bar.
        # Show in taskbar via WS_EX_APPWINDOW.
        # The subprocess is launched with SW_HIDE (hidden_subprocess_kwargs),
        # which PyQt6 inherits.  An explicit ShowWindow(SW_SHOW) overrides it.
        self.show()
        _hwnd = int(self.winId())
        SW_SHOW = 5
        ctypes.windll.user32.ShowWindow(_hwnd, SW_SHOW)
        _style = ctypes.windll.user32.GetWindowLongW(_hwnd, -16)  # GWL_STYLE
        ctypes.windll.user32.SetWindowLongW(_hwnd, -16, _style & ~0x00080000)  # ~WS_SYSMENU
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

        # VLC poller thread
        threading.Thread(target=self._vlc_poller, daemon=True, name="vlc-poller").start()

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
        event.accept()

    def _compute_pressed(self) -> frozenset[str]:
        now = time.monotonic()
        active = frozenset(aid for aid, t in self._pressed.items() if now - t < PRESS_FLASH_S)
        for aid in [a for a, t in self._pressed.items() if now - t >= PRESS_FLASH_S]:
            del self._pressed[aid]
        return active

    def _do_render(self, snapshot: DashboardSnapshot | None, pressed_actions: frozenset[str]) -> None:
        self._last_snapshot = snapshot
        scene = build_dashboard_scene(
            self._preview_layout,
            snapshot,
            favs_file=self._app_config.favs_file,
            broker_heartbeat_file=self._app_config.broker_heartbeat_file,
            pressed_actions=pressed_actions,
        )
        apply_dashboard_window_geometry(self, snapshot, scene, launch_geometry=self._launch_geometry)
        self._widget.set_scene(scene)

    def _on_action(self, action_id: str) -> None:
        self._pressed[action_id] = time.monotonic()
        write_dashboard_command(self._app_config.dashboard_cmd_file, action_id)
        self._do_render(self._last_snapshot, self._compute_pressed())
        QTimer.singleShot(
            int(PRESS_FLASH_S * 1000) + 10,
            lambda: self._do_render(self._last_snapshot, self._compute_pressed()),
        )

    def _handle_press_event(self) -> None:
        while True:
            try:
                action = self._press_queue.get_nowait()
                self._pressed[action] = time.monotonic()
            except queue.Empty:
                break
        self._do_render(self._last_snapshot, self._compute_pressed())
        QTimer.singleShot(
            int(PRESS_FLASH_S * 1000) + 10,
            lambda: self._do_render(self._last_snapshot, self._compute_pressed()),
        )

    def _press_listener(self) -> None:
        while True:
            try:
                data, _ = self._press_sock.recvfrom(256)
                self._press_queue.put(data.decode("utf-8").strip())
                self._press_received.emit()
            except OSError:
                break

    def _vlc_poller(self) -> None:
        while True:
            self._vlc_cache[0] = poll_vlc(self._app_config)
            time.sleep(0.5)

    def _refresh(self) -> None:
        snapshot = load_dashboard_snapshot(self._app_config.dashboard_state_file)
        if snapshot is not None:
            snapshot = hydrate_dashboard_snapshot(snapshot, self._vlc_cache[0], mfp_pid=self._mfp_pid)
        self._do_render(snapshot, self._compute_pressed())


def build_dashboard_window(
    app_config: DashboardAppConfig,
    *,
    launch_geometry: DashboardLaunchGeometry | None = None,
    mfp_pid: int = 0,
) -> DashboardWindow:
    main_monitor, secondary_monitor = get_preview_monitor_sizes(app_config)
    preview_layout = compute_dashboard_preview_layout(main_monitor, secondary_monitor, app_config.layout)
    return DashboardWindow(
        app_config, preview_layout,
        launch_geometry=launch_geometry, mfp_pid=mfp_pid,
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
    parser.add_argument("--mfp-pid", type=int, default=0)
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
    _window = build_dashboard_window(app_config, launch_geometry=launch_geometry, mfp_pid=args.mfp_pid)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


from __future__ import annotations

import argparse
import configparser
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from dataclasses import replace
import os
from pathlib import Path
import queue
import socket
import threading
import time
import tkinter as tk

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


COLOR_BG = "#20262C"
COLOR_PANEL = "#2A3038"
COLOR_TEXT = "#F4F7FA"
COLOR_CABLE = "#A0A8B4"
COLOR_CABLE_DIM = "#505860"
COLOR_ACTIVE = "#1F6F52"
COLOR_ACTIVE_ALT = "#2C8A65"
COLOR_OSR2 = "#8A2C6A"
COLOR_DISABLED = "#6C1F1F"
COLOR_WARNING = "#8A6A2C"

ICON_LOCK = "\U0001F512"
ICON_TRASH = "\U0001F5D1"


def lighten_color(hex_color: str, amount: int = 50) -> str:
    r = min(255, int(hex_color[1:3], 16) + amount)
    g = min(255, int(hex_color[3:5], 16) + amount)
    b = min(255, int(hex_color[5:7], 16) + amount)
    return f"#{r:02X}{g:02X}{b:02X}"


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
    color: str
    width: int = 2
    smooth: bool = False


@dataclass(frozen=True)
class DashboardOvalItem:
    cx: int
    cy: int
    r: int
    fill: str
    outline: str = ""
    outline_width: int = 1


@dataclass(frozen=True)
class DashboardArcItem:
    cx: int
    cy: int
    r: int
    start: float
    extent: float
    outline: str
    width: int = 1


@dataclass(frozen=True)
class DashboardTextItem:
    text: str
    rect: Rect
    color: str = COLOR_TEXT
    anchor: str = "center"
    font: tuple[str, int, str] = ("Segoe UI", 9, "bold")


@dataclass(frozen=True)
class DashboardRectItem:
    rect: Rect
    outline: str = "#707780"
    fill: str = COLOR_PANEL


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


def hydrate_dashboard_snapshot(snapshot: DashboardSnapshot, app_config: DashboardAppConfig, *, mfp_pid: int = 0) -> DashboardSnapshot:
    primary_path = get_current_file_path(app_config.primary_vlc_port, app_config.vlc_password)
    portrait_path = get_current_file_path(app_config.portrait_vlc_port, app_config.vlc_password)
    landscape_path = get_current_file_path(app_config.landscape_vlc_port, app_config.vlc_password)
    primary_status, primary_xml = vlc_http_req(app_config.primary_vlc_port, "/requests/status.xml", app_config.vlc_password)
    primary_responsive = primary_status == 200 and "<state>" in primary_xml
    return replace(
        snapshot,
        primary_responsive=primary_responsive,
        mfp_alive=is_process_alive(mfp_pid),
        primary=replace(snapshot.primary, path=primary_path),
        portrait=replace(snapshot.portrait, path=portrait_path),
        landscape=replace(snapshot.landscape, path=landscape_path),
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
        if snapshot.osr2_mode == "auto":
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
            primary_fill = COLOR_ACTIVE_ALT
        elif snapshot.primary_uses_robot_hand:
            primary_fill = COLOR_OSR2
        else:
            primary_fill = COLOR_PANEL
        portrait_fill = COLOR_ACTIVE_ALT if satellite_panel_should_highlight(
            f_mode_enabled=snapshot.f_mode_enabled,
            is_favorite=is_favorite_path(snapshot.portrait.path, favs_content),
        ) else COLOR_PANEL
        landscape_fill = COLOR_ACTIVE_ALT if satellite_panel_should_highlight(
            f_mode_enabled=snapshot.f_mode_enabled,
            is_favorite=is_favorite_path(snapshot.landscape.path, favs_content),
        ) else COLOR_PANEL
        if snapshot.osr2_mode == "auto":
            osr2_fill = COLOR_OSR2
        elif funscript_active:
            osr2_fill = COLOR_ACTIVE_ALT
        else:
            osr2_fill = COLOR_PANEL
        mfp_fill = COLOR_ACTIVE if mfp_connected else COLOR_DISABLED
        broker_fill = COLOR_ACTIVE_ALT if broker_running else COLOR_DISABLED
        fmode_fill = COLOR_ACTIVE_ALT if snapshot.f_mode_enabled else COLOR_PANEL
        portrait_lock_fill = COLOR_ACTIVE_ALT if snapshot.portrait.locked else COLOR_PANEL
        landscape_lock_fill = COLOR_ACTIVE_ALT if snapshot.landscape.locked else COLOR_PANEL

    omni_paused = snapshot is not None and snapshot.omni_paused
    omnipause_icon = "\u25B6" if omni_paused else "\u23F8"
    omnipause_fill = COLOR_PANEL

    def _press_fill(fill: str, action_id: str) -> str:
        return lighten_color(fill) if action_id in pressed_actions else fill

    rects = (
        DashboardRectItem(layout.main_monitor, fill=COLOR_PANEL),
        DashboardRectItem(layout.secondary_monitor, fill=COLOR_PANEL),
        DashboardRectItem(layout.main_status_strip, fill=COLOR_PANEL),
        DashboardRectItem(layout.quit_button, fill=_press_fill(COLOR_PANEL, QUIT_BUTTON)),
        DashboardRectItem(layout.omnipause_button, fill=_press_fill(omnipause_fill, OMNIPAUSE_TOGGLE)),
        DashboardRectItem(layout.mfp_panel, fill=mfp_fill),
        DashboardRectItem(layout.landscape_panel, fill=landscape_fill),
        DashboardRectItem(layout.portrait_panel, fill=portrait_fill),
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
    texts = (
        DashboardTextItem("\u23FB", layout.quit_button, font=("Segoe UI Symbol", 10, "bold")),
        DashboardTextItem(omnipause_icon, layout.omnipause_button, font=("Segoe UI Symbol", 10, "bold")),
        DashboardTextItem("Fun Time", layout.title, anchor="w"),
        DashboardTextItem(mfp_label, layout.mfp_panel, anchor="n"),
        DashboardTextItem(landscape_label, layout.landscape_panel, anchor="n"),
        DashboardTextItem(portrait_label, layout.portrait_panel, anchor="n"),
        DashboardTextItem(primary_label, layout.primary_panel, anchor="n"),
        DashboardTextItem(osr2_label, layout.osr2_panel, anchor="n"),
        DashboardTextItem("<", layout.portrait_prev, font=("Segoe UI", 9, "bold")),
        DashboardTextItem(">", layout.portrait_next, font=("Segoe UI", 9, "bold")),
        DashboardTextItem(ICON_LOCK, layout.portrait_lock, font=("Segoe UI Emoji", 9, "normal")),
        DashboardTextItem(ICON_TRASH, layout.portrait_trash, font=("Segoe UI Emoji", 9, "normal")),
        DashboardTextItem("<", layout.primary_prev, font=("Segoe UI", 9, "bold")),
        DashboardTextItem(">", layout.primary_next, font=("Segoe UI", 9, "bold")),
        *(
            (DashboardTextItem("1/4", layout.quarter_button, font=("Segoe UI", 8, "bold")),)
            if snapshot is not None and snapshot.primary_uses_robot_hand else (
                DashboardTextItem("\u2212", layout.vlc_nudge_prev, font=("Segoe UI", 9, "bold")),
                DashboardTextItem("+", layout.vlc_nudge_next, font=("Segoe UI", 9, "bold")),
                DashboardTextItem("\U0001F4C2", layout.open_file_dialog, font=("Segoe UI Emoji", 9, "normal")),
                DashboardTextItem("[ ]", layout.clipper_save, font=("Segoe UI", 8, "bold")),
            )
        ),
        DashboardTextItem("<", layout.landscape_prev, font=("Segoe UI", 9, "bold")),
        DashboardTextItem(">", layout.landscape_next, font=("Segoe UI", 9, "bold")),
        DashboardTextItem(ICON_LOCK, layout.landscape_lock, font=("Segoe UI Emoji", 9, "normal")),
        DashboardTextItem(ICON_TRASH, layout.landscape_trash, font=("Segoe UI Emoji", 9, "normal")),
        DashboardTextItem(broker_chip, layout.broker_panel, font=("Segoe UI", 7, "bold")),
        DashboardTextItem(fmode_chip, layout.fmode_panel, font=("Segoe UI", 7, "bold")),
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
            DashboardOvalItem(cx=mid_x, cy=cable_y, r=dot_r, fill=cable_color),
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


def render_dashboard_scene(canvas: tk.Canvas, scene: DashboardScene) -> None:
    canvas.delete("all")
    canvas.configure(width=scene.width, height=scene.height, bg=COLOR_BG, highlightthickness=0)
    for item in scene.rects:
        canvas.create_rectangle(
            item.rect.x,
            item.rect.y,
            item.rect.x + item.rect.width,
            item.rect.y + item.rect.height,
            outline=item.outline,
            fill=item.fill,
            width=1,
        )
    for item in scene.lines:
        if len(item.points) >= 2:
            flat = [c for pt in item.points for c in pt]
            canvas.create_line(
                *flat, fill=item.color, width=item.width,
                capstyle="round", joinstyle="round", smooth=item.smooth,
            )
    for item in scene.ovals:
        outline = item.outline if item.outline else item.fill
        canvas.create_oval(
            item.cx - item.r, item.cy - item.r,
            item.cx + item.r, item.cy + item.r,
            fill=item.fill, outline=outline, width=item.outline_width,
        )
    for item in scene.arcs:
        canvas.create_arc(
            item.cx - item.r, item.cy - item.r,
            item.cx + item.r, item.cy + item.r,
            start=item.start, extent=item.extent,
            style="arc", outline=item.outline, width=item.width,
        )
    for item in scene.texts:
        if item.rect.width == 0 and item.rect.height == 0:
            continue
        x = item.rect.x + item.rect.width / 2
        y = item.rect.y + item.rect.height / 2
        if item.anchor == "w":
            x = item.rect.x
        elif item.anchor == "n":
            y = item.rect.y + 2
        canvas.create_text(x, y, text=item.text, fill=item.color, font=item.font, anchor=item.anchor, justify="center")


def write_dashboard_command(path: Path, action_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(action_id, encoding="utf-8")


def apply_dashboard_window_geometry(
    root: tk.Tk,
    snapshot: DashboardSnapshot | None,
    scene: DashboardScene,
    *,
    launch_geometry: DashboardLaunchGeometry | None = None,
) -> None:
    if launch_geometry is not None:
        root.geometry(
            f"{launch_geometry.width}x{launch_geometry.height}"
            f"+{launch_geometry.x}+{launch_geometry.y}"
        )
        return
    if snapshot is None or snapshot.window.width <= 0 or snapshot.window.height <= 0:
        root.geometry(f"{scene.width}x{scene.height}")
        return
    root.geometry(f"{snapshot.window.width}x{snapshot.window.height}+{snapshot.window.x}+{snapshot.window.y}")


def bind_dashboard_actions(canvas: tk.Canvas, scene: DashboardScene, on_action: object) -> None:
    for action_id, rect in scene.actions:
        tag = f"action:{action_id}"
        canvas.create_rectangle(
            rect.x,
            rect.y,
            rect.x + rect.width,
            rect.y + rect.height,
            outline="",
            fill="",
            tags=(tag,),
        )
        canvas.tag_bind(tag, "<Button-1>", lambda _event, action=action_id: on_action(action))


def bind_dashboard_hover_texts(canvas: tk.Canvas, scene: DashboardScene) -> None:
    tooltip_id: list[int | None] = [None]

    def _show(event: object, text: str) -> None:
        _hide(event)
        tooltip_id[0] = canvas.create_text(
            event.x + 10, event.y - 10,
            text=text, fill="#FFFFFF", font=("Segoe UI", 8, "normal"), anchor="sw",
        )

    def _hide(_event: object) -> None:
        if tooltip_id[0] is not None:
            canvas.delete(tooltip_id[0])
            tooltip_id[0] = None

    for rect, text in scene.hover_texts:
        tag = f"hover:{id(rect)}"
        canvas.create_rectangle(
            rect.x, rect.y,
            rect.x + rect.width, rect.y + rect.height,
            outline="", fill="", tags=(tag,),
        )
        canvas.tag_bind(tag, "<Enter>", lambda e, t=text: _show(e, t))
        canvas.tag_bind(tag, "<Leave>", _hide)


PRESS_FLASH_S = 0.2


def build_dashboard_window(
    app_config: DashboardAppConfig,
    *,
    launch_geometry: DashboardLaunchGeometry | None = None,
    mfp_pid: int = 0,
) -> tk.Tk:
    main_monitor, secondary_monitor = get_preview_monitor_sizes(app_config)
    preview_layout = compute_dashboard_preview_layout(main_monitor, secondary_monitor, app_config.layout)

    root = tk.Tk()
    root.title("Fun Time")
    root.iconbitmap(str(Path(__file__).resolve().parent.parent / "icon.ico"))
    root.configure(bg=COLOR_BG)
    root.resizable(False, False)
    root.attributes("-topmost", True)

    # Remove minimize/maximize/close buttons, keep title bar.
    root.update_idletasks()
    _hwnd = int(root.frame(), 16)
    _style = ctypes.windll.user32.GetWindowLongW(_hwnd, -16)  # GWL_STYLE
    ctypes.windll.user32.SetWindowLongW(_hwnd, -16, _style & ~0x00080000)  # ~WS_SYSMENU
    ctypes.windll.user32.SetWindowPos(
        _hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020,  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
    )
    canvas = tk.Canvas(root, bg=COLOR_BG, highlightthickness=0, bd=0)
    canvas.pack()

    _pressed: dict[str, float] = {}
    _last_snapshot: list[DashboardSnapshot | None] = [None]
    _press_queue: queue.Queue[str] = queue.Queue()

    def _compute_pressed() -> frozenset[str]:
        now = time.monotonic()
        active = frozenset(aid for aid, t in _pressed.items() if now - t < PRESS_FLASH_S)
        for aid in [a for a, t in _pressed.items() if now - t >= PRESS_FLASH_S]:
            del _pressed[aid]
        return active

    def _do_render(snapshot: DashboardSnapshot | None, pressed_actions: frozenset[str]) -> None:
        _last_snapshot[0] = snapshot
        scene = build_dashboard_scene(
            preview_layout,
            snapshot,
            favs_file=app_config.favs_file,
            broker_heartbeat_file=app_config.broker_heartbeat_file,
            pressed_actions=pressed_actions,
        )
        apply_dashboard_window_geometry(root, snapshot, scene, launch_geometry=launch_geometry)
        render_dashboard_scene(canvas, scene)
        bind_dashboard_hover_texts(canvas, scene)
        bind_dashboard_actions(canvas, scene, _on_action)

    def _on_action(action_id: str) -> None:
        _pressed[action_id] = time.monotonic()
        write_dashboard_command(app_config.dashboard_cmd_file, action_id)
        _do_render(_last_snapshot[0], _compute_pressed())
        root.after(int(PRESS_FLASH_S * 1000) + 10, lambda: _do_render(_last_snapshot[0], _compute_pressed()))

    def _handle_press_event(_event: object) -> None:
        while True:
            try:
                action = _press_queue.get_nowait()
                _pressed[action] = time.monotonic()
            except queue.Empty:
                break
        _do_render(_last_snapshot[0], _compute_pressed())
        root.after(int(PRESS_FLASH_S * 1000) + 10, lambda: _do_render(_last_snapshot[0], _compute_pressed()))

    root.bind("<<Press>>", _handle_press_event)

    press_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    press_sock.bind(("127.0.0.1", 0))
    press_port = press_sock.getsockname()[1]
    port_file = app_config.dashboard_state_file.parent / "dashboard_press_port.txt"
    port_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.write_text(str(press_port), encoding="utf-8")

    def _press_listener() -> None:
        while True:
            try:
                data, _ = press_sock.recvfrom(256)
                _press_queue.put(data.decode("utf-8").strip())
                root.event_generate("<<Press>>", when="tail")
            except OSError:
                break

    threading.Thread(target=_press_listener, daemon=True, name="press-listener").start()

    def refresh() -> None:
        snapshot = load_dashboard_snapshot(app_config.dashboard_state_file)
        if snapshot is not None:
            snapshot = hydrate_dashboard_snapshot(snapshot, app_config, mfp_pid=mfp_pid)
        _do_render(snapshot, _compute_pressed())
        root.after(500, refresh)

    refresh()
    return root


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
    app_config = load_dashboard_app_config(Path(args.manifest_path))
    launch_geometry = None
    if None not in {args.x, args.y, args.width, args.height}:
        launch_geometry = DashboardLaunchGeometry(
            x=args.x,
            y=args.y,
            width=args.width,
            height=args.height,
        )
    root = build_dashboard_window(app_config, launch_geometry=launch_geometry, mfp_pid=args.mfp_pid)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


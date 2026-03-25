from __future__ import annotations

import argparse
import configparser
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk

from fun_time.config import LayoutConfig
from fun_time.controller_manifest import CONTROLLER_MANIFEST_FILENAME
from fun_time.dashboard_actions import (
    LANDSCAPE_LOCK,
    LANDSCAPE_NEXT,
    LANDSCAPE_PREV,
    LANDSCAPE_TRASH,
    LINK_TOGGLE,
    PORTRAIT_LOCK,
    PORTRAIT_NEXT,
    PORTRAIT_PREV,
    PORTRAIT_TRASH,
    PRIMARY_NEXT,
    PRIMARY_PREV,
    QUARTER_BUTTON,
)
from fun_time.dashboard_layout import DashboardPreviewLayout, Rect, Size, compute_dashboard_preview_layout
from fun_time.dashboard_runtime import DashboardSnapshot, load_dashboard_snapshot
from fun_time.dashboard_state import (
    LABEL_BROKER,
    LABEL_CONTROLLER,
    LABEL_F_MODE,
    LABEL_LANDSCAPE_VLC,
    LABEL_MFP,
    LABEL_OSR2,
    LABEL_PORTRAIT_VLC,
    LABEL_PRIMARY_VLC,
)


COLOR_BG = "#20262C"
COLOR_PANEL = "#2A3038"
COLOR_TEXT = "#F4F7FA"
COLOR_MUTED = "#AEB7C2"
COLOR_LINK = "#3A7AFE"
COLOR_ACTIVE = "#1F6F52"
COLOR_ACTIVE_ALT = "#2C8A65"
COLOR_DISABLED = "#6C1F1F"
COLOR_WARNING = "#8A6A2C"


@dataclass(frozen=True)
class DashboardAppConfig:
    layout: LayoutConfig
    manifest_path: Path
    dashboard_state_file: Path
    dashboard_cmd_file: Path


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
    )
    return DashboardAppConfig(
        layout=layout,
        manifest_path=manifest_path,
        dashboard_state_file=Path(parser.get("commands", "dashboard_state_file", fallback="dashboard_state.ini")),
        dashboard_cmd_file=Path(parser.get("commands", "dashboard_cmd_file", fallback="dashboard_cmd.txt")),
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


def build_dashboard_scene(layout: DashboardPreviewLayout, snapshot: DashboardSnapshot | None = None) -> DashboardScene:
    primary_label = LABEL_PRIMARY_VLC
    portrait_label = LABEL_PORTRAIT_VLC
    landscape_label = LABEL_LANDSCAPE_VLC
    osr2_label = LABEL_OSR2
    mfp_label = LABEL_MFP
    link_label = "Robot Link"
    broker_chip = "b"
    controller_chip = "c"
    fmode_chip = "f"
    primary_fill = COLOR_PANEL
    portrait_fill = COLOR_PANEL
    landscape_fill = COLOR_PANEL
    osr2_fill = COLOR_PANEL
    mfp_fill = COLOR_PANEL
    broker_fill = COLOR_PANEL
    controller_fill = COLOR_ACTIVE
    fmode_fill = COLOR_PANEL
    portrait_lock_fill = COLOR_PANEL
    landscape_lock_fill = COLOR_PANEL

    if snapshot is not None:
        primary_label = f"{snapshot.primary.label}\n{snapshot.primary.clip or '(none)'}"
        portrait_label = f"{snapshot.portrait.label}\n{snapshot.portrait.clip or '(none)'}"
        landscape_label = f"{snapshot.landscape.label}\n{snapshot.landscape.clip or '(none)'}"
        osr2_label = f"{LABEL_OSR2}\n{snapshot.osr2_mode}"
        mfp_label = f"{LABEL_MFP}\n{'connected' if snapshot.mfp_connected else 'disconnected'}"
        link_label = "Robot Link" if snapshot.robot_link_enabled else "Broken Link"
        primary_fill = COLOR_ACTIVE_ALT if snapshot.primary.accent == "osr2" else (COLOR_ACTIVE_ALT if snapshot.primary.highlight else COLOR_PANEL)
        portrait_fill = COLOR_ACTIVE_ALT if snapshot.portrait.highlight else COLOR_PANEL
        landscape_fill = COLOR_ACTIVE_ALT if snapshot.landscape.highlight else COLOR_PANEL
        osr2_fill = COLOR_ACTIVE if snapshot.osr2_mode == "auto" else COLOR_WARNING
        mfp_fill = COLOR_ACTIVE if snapshot.mfp_connected else COLOR_DISABLED
        broker_fill = COLOR_ACTIVE if snapshot.broker_running else COLOR_DISABLED
        fmode_fill = COLOR_ACTIVE_ALT if snapshot.f_mode_enabled else COLOR_PANEL
        portrait_lock_fill = COLOR_ACTIVE if snapshot.portrait.locked else COLOR_PANEL
        landscape_lock_fill = COLOR_ACTIVE if snapshot.landscape.locked else COLOR_PANEL

    rects = (
        DashboardRectItem(layout.main_monitor, fill=COLOR_PANEL),
        DashboardRectItem(layout.secondary_monitor, fill=COLOR_PANEL),
        DashboardRectItem(layout.main_status_strip, fill=COLOR_PANEL),
        DashboardRectItem(layout.mfp_panel, fill=mfp_fill),
        DashboardRectItem(layout.landscape_panel, fill=landscape_fill),
        DashboardRectItem(layout.portrait_panel, fill=portrait_fill),
        DashboardRectItem(layout.primary_panel, fill=primary_fill),
        DashboardRectItem(layout.osr2_panel, fill=osr2_fill),
        DashboardRectItem(layout.link_toggle, fill=COLOR_LINK, outline=COLOR_LINK),
        DashboardRectItem(layout.portrait_prev, fill=COLOR_PANEL),
        DashboardRectItem(layout.portrait_next, fill=COLOR_PANEL),
        DashboardRectItem(layout.portrait_lock, fill=portrait_lock_fill),
        DashboardRectItem(layout.portrait_trash, fill=COLOR_WARNING),
        DashboardRectItem(layout.primary_prev, fill=COLOR_PANEL),
        DashboardRectItem(layout.primary_next, fill=COLOR_PANEL),
        DashboardRectItem(layout.quarter_button, fill=osr2_fill if snapshot is not None and snapshot.primary.accent == "osr2" else COLOR_PANEL),
        DashboardRectItem(layout.landscape_prev, fill=COLOR_PANEL),
        DashboardRectItem(layout.landscape_next, fill=COLOR_PANEL),
        DashboardRectItem(layout.landscape_lock, fill=landscape_lock_fill),
        DashboardRectItem(layout.landscape_trash, fill=COLOR_WARNING),
        DashboardRectItem(layout.broker_panel, fill=broker_fill),
        DashboardRectItem(layout.controller_panel, fill=controller_fill),
        DashboardRectItem(layout.fmode_panel, fill=fmode_fill),
    )
    texts = (
        DashboardTextItem("Fun Time", layout.title, anchor="w"),
        DashboardTextItem(mfp_label, layout.mfp_panel),
        DashboardTextItem(landscape_label, layout.landscape_panel),
        DashboardTextItem(portrait_label, layout.portrait_panel),
        DashboardTextItem(primary_label, layout.primary_panel),
        DashboardTextItem(osr2_label, layout.osr2_panel),
        DashboardTextItem(link_label, layout.link_toggle, color=COLOR_TEXT, font=("Segoe UI", 8, "bold")),
        DashboardTextItem("<", layout.portrait_prev, font=("Segoe UI", 9, "bold")),
        DashboardTextItem(">", layout.portrait_next, font=("Segoe UI", 9, "bold")),
        DashboardTextItem("Lock", layout.portrait_lock, font=("Segoe UI", 8, "bold")),
        DashboardTextItem("Trash", layout.portrait_trash, font=("Segoe UI", 8, "bold")),
        DashboardTextItem("<", layout.primary_prev, font=("Segoe UI", 9, "bold")),
        DashboardTextItem(">", layout.primary_next, font=("Segoe UI", 9, "bold")),
        DashboardTextItem("1/4", layout.quarter_button, font=("Segoe UI", 8, "bold")),
        DashboardTextItem("<", layout.landscape_prev, font=("Segoe UI", 9, "bold")),
        DashboardTextItem(">", layout.landscape_next, font=("Segoe UI", 9, "bold")),
        DashboardTextItem("Lock", layout.landscape_lock, font=("Segoe UI", 8, "bold")),
        DashboardTextItem("Trash", layout.landscape_trash, font=("Segoe UI", 8, "bold")),
        DashboardTextItem(broker_chip, layout.broker_panel, font=("Segoe UI", 7, "bold")),
        DashboardTextItem(controller_chip, layout.controller_panel, font=("Segoe UI", 7, "bold")),
        DashboardTextItem(fmode_chip, layout.fmode_panel, font=("Segoe UI", 7, "bold")),
    )
    return DashboardScene(
        width=layout.dashboard_width,
        height=layout.dashboard_height,
        rects=rects,
        texts=texts,
        actions=(
            (PORTRAIT_PREV, layout.portrait_prev),
            (PORTRAIT_NEXT, layout.portrait_next),
            (PORTRAIT_LOCK, layout.portrait_lock),
            (PORTRAIT_TRASH, layout.portrait_trash),
            (PRIMARY_PREV, layout.primary_prev),
            (PRIMARY_NEXT, layout.primary_next),
            (QUARTER_BUTTON, layout.quarter_button),
            (LANDSCAPE_PREV, layout.landscape_prev),
            (LANDSCAPE_NEXT, layout.landscape_next),
            (LANDSCAPE_LOCK, layout.landscape_lock),
            (LANDSCAPE_TRASH, layout.landscape_trash),
            (LINK_TOGGLE, layout.link_toggle),
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
    for item in scene.texts:
        if item.rect.width == 0 and item.rect.height == 0:
            continue
        x = item.rect.x + item.rect.width / 2
        y = item.rect.y + item.rect.height / 2
        if item.anchor == "w":
            x = item.rect.x
        canvas.create_text(x, y, text=item.text, fill=item.color, font=item.font, anchor=item.anchor)


def write_dashboard_command(path: Path, action_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(action_id, encoding="utf-8")


def apply_dashboard_window_geometry(root: tk.Tk, snapshot: DashboardSnapshot | None, scene: DashboardScene) -> None:
    if snapshot is None or snapshot.window.width <= 0 or snapshot.window.height <= 0:
        root.geometry(f"{scene.width}x{scene.height}")
        return
    root.geometry(f"{snapshot.window.width}x{snapshot.window.height}+{snapshot.window.x}+{snapshot.window.y}")


def bind_dashboard_actions(canvas: tk.Canvas, scene: DashboardScene, command_file: Path) -> None:
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
        canvas.tag_bind(tag, "<Button-1>", lambda _event, action=action_id: write_dashboard_command(command_file, action))


def build_dashboard_window(app_config: DashboardAppConfig) -> tk.Tk:
    main_monitor, secondary_monitor = get_preview_monitor_sizes(app_config)
    preview_layout = compute_dashboard_preview_layout(main_monitor, secondary_monitor, app_config.layout)

    root = tk.Tk()
    root.title("Fun Time Dashboard")
    root.configure(bg=COLOR_BG)
    root.resizable(False, False)
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, bg=COLOR_BG, highlightthickness=0, bd=0)
    canvas.pack()

    def refresh() -> None:
        snapshot = load_dashboard_snapshot(app_config.dashboard_state_file)
        scene = build_dashboard_scene(preview_layout, snapshot)
        apply_dashboard_window_geometry(root, snapshot, scene)
        render_dashboard_scene(canvas, scene)
        bind_dashboard_actions(canvas, scene, app_config.dashboard_cmd_file)
        root.after(500, refresh)

    refresh()
    return root


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Fun Time dashboard preview app")
    parser.add_argument(
        "manifest_path",
        nargs="?",
        default=str(Path("state") / CONTROLLER_MANIFEST_FILENAME),
        help="Path to the controller launch manifest",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app_config = load_dashboard_app_config(Path(args.manifest_path))
    root = build_dashboard_window(app_config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

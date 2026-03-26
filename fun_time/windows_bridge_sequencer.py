"""Startup sequencer for the Python orchestrator.

Replaces AHK's ``StartWindowsBridge()`` — runs the full startup sequence
in Python: core session launch, window positioning, UI companion launch.
"""
from __future__ import annotations

import configparser
import time
from dataclasses import dataclass
from pathlib import Path

from .config import LayoutConfig
from .dashboard_layout import Size
from .windows_bridge_monitors import enumerate_monitors
from .windows_bridge_startup import start_core_session, launch_ui_companions
from .windows_bridge_win32 import (
    activate_window,
    get_window_rect,
    move_window,
    set_always_on_top,
    wait_for_window,
)
from .windows_bridge_window_layout import (
    MonitorRect,
    WindowLayoutPlan,
    compute_window_layout,
)


@dataclass(frozen=True)
class StartupResult:
    primary_pid: int
    mfp_pid: int
    portrait_pid: int
    landscape_pid: int
    dashboard_pid: int
    robot_hand_pid: int
    audio_pid: int
    layout_plan: WindowLayoutPlan


def _read_manifest(path: str | Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(path), encoding="utf-8")
    return parser


def _read_result_pids(result_file: str | Path) -> dict[str, int]:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(result_file), encoding="utf-8")
    return {key: int(value) for key, value in parser["result"].items()}


def _build_unique_result_path(state_dir: Path, prefix: str) -> Path:
    return state_dir / f"{prefix}_{int(time.monotonic() * 1000)}.ini"


def run_startup_sequence(
    *,
    manifest_path: str | Path,
    state_dir: str | Path,
) -> StartupResult:
    """Run the full startup sequence, returning all PIDs and the layout plan."""
    manifest_path = Path(manifest_path)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    m = _read_manifest(manifest_path)

    # --- Phase 1: Launch core media stack ---
    core_result_file = _build_unique_result_path(state_dir, "core_session")
    start_core_session(
        project_dir=m["runtime"]["project_dir"],
        config_path=m["runtime"]["config_path"],
        random_favs_browser_manifest_file=m["random_favs_browser"]["manifest_file"],
        enabled_file=m["commands"]["robot_hand_enabled_file"],
        paused_file=m["commands"]["robot_hand_paused_file"],
        audio_paused_file=m["commands"]["audio_paused_file"],
        vlc_exe=m["executables"]["vlc_exe"],
        mfp_exe=m["executables"]["mfp_exe"],
        primary_sources=m["media"]["primary_vlc_sources"],
        portrait_sources=m["media"]["portrait_dirs"],
        landscape_sources=m["media"]["landscape_dirs"],
        primary_port=int(m["controller"]["primary_vlc_port"]),
        portrait_port=int(m["controller"]["vlc2_port"]),
        landscape_port=int(m["controller"]["vlc3_port"]),
        password=m["controller"]["vlc_pass"],
        result_file=str(core_result_file),
    )
    core_pids = _read_result_pids(core_result_file)
    primary_pid = core_pids["primary_pid"]
    mfp_pid = core_pids["mfp_pid"]
    portrait_pid = core_pids["portrait_pid"]
    landscape_pid = core_pids["landscape_pid"]

    # --- Phase 2: Wait for MFP window and position everything ---
    mfp_hwnd = wait_for_window(mfp_pid, timeout_s=15.0)
    if not mfp_hwnd:
        raise RuntimeError(f"MFP window did not appear (pid={mfp_pid})")
    time.sleep(5.0)

    # Get MFP actual size for layout computation
    _, _, mfp_w, mfp_h = get_window_rect(mfp_hwnd)
    if mfp_w <= 0 or mfp_h <= 0:
        # Fall back to config-based estimate
        layout_cfg = _layout_config_from_manifest(m)
        monitors = enumerate_monitors()
        from .windows_bridge_monitors import get_logical_monitor_rects
        main_rect, _ = get_logical_monitor_rects(
            monitors, main_index=layout_cfg.main_monitor, secondary_index=layout_cfg.secondary_monitor,
        )
        landscape_w = int(main_rect.width * layout_cfg.landscape_width_ratio)
        left_w = main_rect.width - landscape_w
        mfp_w = int(left_w * layout_cfg.mfp_width_ratio)
        mfp_h = int(main_rect.height * layout_cfg.mfp_height_ratio)

    layout_cfg = _layout_config_from_manifest(m)
    monitors = enumerate_monitors()
    plan = compute_window_layout(
        main_monitor=_main_monitor_rect(monitors, layout_cfg),
        secondary_monitor=_secondary_monitor_rect(monitors, layout_cfg),
        layout_config=layout_cfg,
        mfp_size=Size(mfp_w, mfp_h),
    )

    # Position core windows
    _position_pid_window(portrait_pid, plan.portrait)
    _position_pid_window(primary_pid, plan.primary)
    _position_pid_window(landscape_pid, plan.landscape)
    _position_mfp_window(mfp_hwnd, plan.mfp)

    # Set topmost on core windows
    for pid in [primary_pid, portrait_pid, landscape_pid, mfp_pid]:
        hwnd = wait_for_window(pid, timeout_s=2.0)
        if hwnd:
            set_always_on_top(hwnd, True)
            activate_window(hwnd)

    # --- Phase 3: Launch UI companions ---
    time.sleep(1.2)

    dashboard_enabled = m["dashboard"]["enabled"].strip() not in {"", "0", "false", "False"}
    ui_result_file = _build_unique_result_path(state_dir, "ui_companions")
    launch_ui_companions(
        python_exe=m["executables"]["python_exe"],
        dashboard_module=m["modules"]["dashboard_module"],
        dashboard_enabled=dashboard_enabled,
        windows_bridge_manifest_path=str(manifest_path),
        dashboard_x=plan.dashboard.x,
        dashboard_y=plan.dashboard.y,
        dashboard_width=plan.dashboard.width,
        dashboard_height=plan.dashboard.height,
        mfp_pid=mfp_pid,
        robot_hand_module=m["modules"]["robot_hand_module"],
        audio_module=m["modules"]["audio_module"],
        config_path=m["runtime"]["config_path"],
        clips_folder=m["media"]["robot_hand_clips"],
        audio_folder=m["media"]["robot_hand_audio"],
        robot_x=plan.robot_hand.x,
        robot_y=plan.robot_hand.y,
        robot_width=plan.robot_hand.width,
        robot_height=plan.robot_hand.height,
        result_file=str(ui_result_file),
    )
    ui_pids = _read_result_pids(ui_result_file)

    return StartupResult(
        primary_pid=primary_pid,
        mfp_pid=mfp_pid,
        portrait_pid=portrait_pid,
        landscape_pid=landscape_pid,
        dashboard_pid=ui_pids["dashboard_pid"],
        robot_hand_pid=ui_pids["robot_hand_pid"],
        audio_pid=ui_pids["audio_pid"],
        layout_plan=plan,
    )


def _layout_config_from_manifest(m: configparser.ConfigParser) -> LayoutConfig:
    return LayoutConfig(
        main_monitor=int(m["layout"]["main_monitor"]),
        secondary_monitor=int(m["layout"]["secondary_monitor"]),
        primary_top_ratio=float(m["layout"]["primary_top_ratio"]),
        landscape_width_ratio=float(m["layout"]["landscape_width_ratio"]),
        mfp_width_ratio=float(m["layout"]["mfp_width_ratio"]),
        mfp_height_ratio=float(m["layout"]["mfp_height_ratio"]),
    )


def _main_monitor_rect(monitors: list, layout_cfg: LayoutConfig) -> MonitorRect:
    from .windows_bridge_monitors import get_logical_monitor_rects
    main, _ = get_logical_monitor_rects(
        monitors, main_index=layout_cfg.main_monitor, secondary_index=layout_cfg.secondary_monitor,
    )
    return main


def _secondary_monitor_rect(monitors: list, layout_cfg: LayoutConfig) -> MonitorRect:
    from .windows_bridge_monitors import get_logical_monitor_rects
    _, secondary = get_logical_monitor_rects(
        monitors, main_index=layout_cfg.main_monitor, secondary_index=layout_cfg.secondary_monitor,
    )
    return secondary


def _position_pid_window(pid: int, rect) -> None:
    hwnd = wait_for_window(pid, timeout_s=10.0)
    if hwnd:
        move_window(hwnd, rect.x, rect.y, rect.width, rect.height)


def _position_mfp_window(hwnd: int, target_rect) -> None:
    move_window(hwnd, target_rect.x, target_rect.y, target_rect.width, target_rect.height)

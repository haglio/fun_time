"""Startup sequencer for the Python orchestrator.

Replaces AHK's ``StartWindowsBridge()`` — runs the full startup sequence
in Python: core session launch, window positioning, UI companion launch.
"""
from __future__ import annotations

import configparser
import ctypes
import ctypes.wintypes
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import LayoutConfig
from .dashboard_layout import Size
from .monitors import enumerate_monitors, get_logical_monitor_rects
from .startup_progress import NullProgress, ProgressReporter
from .vlc_actions import vlc_http_cmd
from .windows_bridge_random_favs_browser import launch_random_favs_browser
from .windows_bridge_startup import start_core_session, launch_ui_companions
from .win32 import (
    activate_window,
    find_window_by_pid,
    get_window_rect,
    move_window,
    set_always_on_top,
    wait_for_window,
)
from .window_layout import (
    MonitorRect,
    WindowLayoutPlan,
    WindowRect,
    compute_window_layout,
)

logger = logging.getLogger(__name__)


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
    core_hwnds: list[int] = field(default_factory=list)


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
    progress: ProgressReporter | None = None,
    hide_windows: bool = False,
) -> StartupResult:
    """Run the full startup sequence, returning all PIDs and the layout plan.

    When *hide_windows* is True, VLC windows are moved offscreen during
    launch (preserving D3D11 init) and all positioning is deferred to the
    end so everything appears at once.  The window handles are returned
    in ``StartupResult.core_hwnds``.
    """
    if progress is None:
        progress = NullProgress()

    manifest_path = Path(manifest_path)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    m = _read_manifest(manifest_path)

    # --- Phase 1: Launch core media stack ---
    progress.advance("Preparing services...")
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
        hide_windows=hide_windows,
    )
    core_pids = _read_result_pids(core_result_file)
    primary_pid = core_pids["primary_pid"]
    mfp_pid = core_pids["mfp_pid"]
    portrait_pid = core_pids["portrait_pid"]
    landscape_pid = core_pids["landscape_pid"]
    logger.info(
        "Core session launched: primary=%d mfp=%d portrait=%d landscape=%d",
        primary_pid, mfp_pid, portrait_pid, landscape_pid,
    )

    # --- Phase 2: Wait for MFP window and compute layout ---
    progress.advance("Waiting for media player window...")
    mfp_hwnd = wait_for_window(mfp_pid, timeout_s=15.0)
    if not mfp_hwnd:
        raise RuntimeError(f"MFP window did not appear (pid={mfp_pid})")
    time.sleep(5.0)
    logger.info("MFP window ready")

    progress.advance("Computing window layout...")
    layout_cfg = _layout_config_from_manifest(m)
    monitors = enumerate_monitors()
    main_rect, secondary_rect = get_logical_monitor_rects(
        monitors, main_index=layout_cfg.main_monitor, secondary_index=layout_cfg.secondary_monitor,
    )

    # Get MFP actual size for layout computation
    mfp_w, mfp_h = _get_mfp_size(mfp_hwnd, main_rect, layout_cfg)

    plan = compute_window_layout(
        main_monitor=main_rect,
        secondary_monitor=secondary_rect,
        layout_config=layout_cfg,
        mfp_size=Size(mfp_w, mfp_h),
    )

    skip_activate = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"

    if not hide_windows:
        # --- Normal mode: position immediately ---
        progress.advance("Positioning windows...")
        _position_pid_window(portrait_pid, plan.portrait, "portrait VLC", activate=not skip_activate)
        _position_pid_window(primary_pid, plan.primary, "primary VLC", activate=not skip_activate)
        _position_pid_window(landscape_pid, plan.landscape, "landscape VLC", activate=not skip_activate)
        _position_mfp_window(mfp_pid, plan.mfp, main_rect, layout_cfg, activate=not skip_activate)
        logger.info("Core windows positioned")

        progress.advance("Finalizing window layout...")
        for pid in [primary_pid, portrait_pid, landscape_pid, mfp_pid]:
            hwnd = find_window_by_pid(pid)
            if hwnd:
                set_always_on_top(hwnd, True)
                if not skip_activate:
                    activate_window(hwnd)
        logger.info("Topmost set on core windows")

    # --- Phase 2.5: Launch Random Favs Browser ---
    progress.advance("Launching browser...")
    _maybe_launch_random_favs_browser(m, plan, mfp_pid, hide_windows=hide_windows)

    # --- Phase 3: Launch UI companions ---
    progress.advance("Launching companions...")
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

    # --- Phase 4 (loading screen only): batch-position everything at once ---
    collected_hwnds: list[int] = []
    if hide_windows:
        progress.advance("Positioning windows...")

        # Restore VLC audio (muted in launch_core_apps during loading)
        primary_port = int(m["controller"]["primary_vlc_port"])
        portrait_port = int(m["controller"]["vlc2_port"])
        landscape_port = int(m["controller"]["vlc3_port"])
        password = m["controller"]["vlc_pass"]
        for port in [primary_port, portrait_port, landscape_port]:
            vlc_http_cmd(port, "volume&val=256", password)

        _position_pid_window(portrait_pid, plan.portrait, "portrait VLC", activate=False)
        _position_pid_window(primary_pid, plan.primary, "primary VLC", activate=False)
        _position_pid_window(landscape_pid, plan.landscape, "landscape VLC", activate=False)
        _position_mfp_window(mfp_pid, plan.mfp, main_rect, layout_cfg, activate=False)
        logger.info("Core windows positioned (deferred reveal)")

        for pid in [primary_pid, portrait_pid, landscape_pid, mfp_pid]:
            hwnd = find_window_by_pid(pid)
            if hwnd:
                set_always_on_top(hwnd, True)
                collected_hwnds.append(hwnd)
        logger.info("Topmost set on core windows")

        progress.advance("Finalizing...")

    return StartupResult(
        primary_pid=primary_pid,
        mfp_pid=mfp_pid,
        portrait_pid=portrait_pid,
        landscape_pid=landscape_pid,
        dashboard_pid=ui_pids["dashboard_pid"],
        robot_hand_pid=ui_pids["robot_hand_pid"],
        audio_pid=ui_pids["audio_pid"],
        layout_plan=plan,
        core_hwnds=collected_hwnds,
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


def _get_mfp_size(
    mfp_hwnd: int, main_rect: MonitorRect, layout_cfg: LayoutConfig,
) -> tuple[int, int]:
    """Get the actual MFP window size, falling back to a config-based estimate."""
    _, _, w, h = get_window_rect(mfp_hwnd)
    if w > 0 and h > 0:
        return w, h
    landscape_w = int(main_rect.width * layout_cfg.landscape_width_ratio)
    left_w = main_rect.width - landscape_w
    return (
        int(left_w * layout_cfg.mfp_width_ratio),
        int(main_rect.height * layout_cfg.mfp_height_ratio),
    )


def _position_pid_window(pid: int, rect: WindowRect, label: str, *, activate: bool = True) -> None:
    """Wait for a visible window belonging to *pid* and move it."""
    hwnd = wait_for_window(pid, timeout_s=10.0)
    if hwnd:
        move_window(hwnd, rect.x, rect.y, rect.width, rect.height, activate=activate)
        logger.info("Positioned %s (pid=%d hwnd=%d) at %d,%d %dx%d",
                     label, pid, hwnd, rect.x, rect.y, rect.width, rect.height)
    else:
        logger.warning("Could not find window for %s (pid=%d)", label, pid)


def _position_mfp_window(
    mfp_pid: int, target: WindowRect, main_rect: MonitorRect, layout_cfg: LayoutConfig,
    *, activate: bool = True,
) -> None:
    """Position MFP with a retry loop and delta correction.

    Replicates AHK's ``PositionMfpWindow`` which retries up to 3 times,
    adjusting for the difference between requested and actual position.
    """
    hwnd = find_window_by_pid(mfp_pid)
    if not hwnd:
        logger.warning("Could not find MFP window for positioning")
        return

    move_x, move_y = target.x, target.y
    move_w, move_h = target.width, target.height

    for attempt in range(3):
        move_window(hwnd, move_x, move_y, move_w, move_h, activate=activate)
        time.sleep(0.08)

        actual_x, actual_y, actual_w, actual_h = get_window_rect(hwnd)

        # Recompute the plan using the actual MFP size
        plan = compute_window_layout(
            main_monitor=main_rect,
            secondary_monitor=MonitorRect(0, 0, 1, 1),  # not used for MFP
            layout_config=layout_cfg,
            mfp_size=Size(actual_w, actual_h),
        )

        delta_x = plan.mfp.x - actual_x
        delta_y = plan.mfp.y - actual_y
        if abs(delta_x) <= 1 and abs(delta_y) <= 1:
            break
        move_x += delta_x
        move_y += delta_y
        move_w = actual_w
        move_h = actual_h

    logger.info("Positioned MFP (pid=%d) at %d,%d", mfp_pid, actual_x, actual_y)


def _resolve_shortcut(shortcut_path: str) -> tuple[str, str, str]:
    """Resolve a Windows .lnk shortcut, returning (target, work_dir, args).

    Uses the COM IShellLink interface via ctypes.
    """
    try:
        import win32com.client  # type: ignore[import-untyped]
        shell = win32com.client.Dispatch("WScript.Shell")
        link = shell.CreateShortcut(shortcut_path)
        return link.TargetPath, link.WorkingDirectory, link.Arguments
    except Exception:
        pass

    # Fallback: use PowerShell
    try:
        ps_script = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut_path}'); "
            f"Write-Output $s.TargetPath; Write-Output $s.WorkingDirectory; Write-Output $s.Arguments"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, check=False,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 3:
            return lines[0], lines[1], lines[2]
        if len(lines) >= 1:
            return lines[0], lines[1] if len(lines) > 1 else "", ""
    except Exception:
        pass

    return "", "", ""


def _maybe_launch_random_favs_browser(
    m: configparser.ConfigParser,
    plan: WindowLayoutPlan,
    mfp_pid: int,
    *,
    hide_windows: bool = False,
) -> None:
    """Launch the Random Favs Browser if enabled, position it, and restore MFP topmost."""
    if m["random_favs_browser"]["enabled"] != "1":
        return

    shortcut_path = m["random_favs_browser"]["shortcut_path"]
    manifest_file = m["random_favs_browser"]["manifest_file"]

    target, work_dir, args = _resolve_shortcut(shortcut_path)
    if not target:
        logger.warning("Random Favs Browser skipped: could not resolve shortcut %s", shortcut_path)
        return

    # Take a Chrome window snapshot before launch
    before_hwnds = _get_chrome_window_hwnds()

    result = launch_random_favs_browser(
        manifest_file,
        shortcut_target=target,
        shortcut_work_dir=work_dir,
        shortcut_args=args,
    )
    if not result.should_launch:
        logger.info("Random Favs Browser skipped: launch plan was empty")
        return

    # Wait for a new Chrome window to appear
    new_hwnd = _wait_for_new_chrome_window(before_hwnds, timeout_ms=8000)
    if not new_hwnd:
        logger.warning("Random Favs Browser skipped: no new Chrome window appeared")
        return

    # Position the browser window
    rect = plan.random_favs_browser
    no_activate = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
    move_window(new_hwnd, rect.x, rect.y, rect.width, rect.height, activate=not no_activate)
    set_always_on_top(new_hwnd, False)

    # Restore MFP above browser — toggle topmost off/on to force z-order
    # recalculation (re-setting topmost on an already-topmost window is a no-op).
    # Skip during loading screen: Phase 4 handles z-order after the overlay closes.
    if not hide_windows:
        mfp_hwnd = find_window_by_pid(mfp_pid)
        if mfp_hwnd:
            set_always_on_top(mfp_hwnd, False)
            set_always_on_top(mfp_hwnd, True)
            if not no_activate:
                activate_window(mfp_hwnd)

    logger.info("Random Favs Browser positioned")


def _get_chrome_window_hwnds() -> set[int]:
    """Get the set of visible Chrome window handles."""
    hwnds: set[int] = set()

    _user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )

    def callback(hwnd: int, _lparam: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True
        # Check process name via PID
        pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # Check title is non-empty
        title_len = _user32.GetWindowTextLengthW(hwnd)
        if title_len > 0:
            # Get the window class name to identify Chrome
            class_name = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, class_name, 256)
            if "Chrome" in class_name.value:
                hwnds.add(hwnd)
        return True

    _user32.EnumWindows(WNDENUMPROC(callback), 0)
    return hwnds


def _wait_for_new_chrome_window(before: set[int], timeout_ms: int = 8000) -> int:
    """Wait for a new Chrome window that wasn't in the 'before' set."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        current = _get_chrome_window_hwnds()
        new_windows = current - before
        if new_windows:
            return next(iter(new_windows))
        time.sleep(0.2)
    return 0

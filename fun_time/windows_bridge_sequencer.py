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
from .runtime_flow import write_flag_file
from .windows_bridge_startup import launch_genau, launch_nau, start_core_session, launch_ui_companions
from .window_roles import LOG_PANEL_WINDOW_TITLE, role_topmost
from .win32 import (
    disable_window_transitions,
    find_window_by_pid,
    minimize_window,
    move_window,
    set_always_on_top,
    wait_for_window,
    wait_for_window_by_title,
)
from .window_layout import (
    MonitorRect,
    WindowLayoutPlan,
    WindowRect,
    clamp01,
    compute_primary_media_rect,
    compute_window_layout,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartupResult:
    nau_pid: int
    portrait_pid: int
    landscape_pid: int
    dashboard_pid: int
    genau_pid: int
    audio_pid: int
    layout_plan: WindowLayoutPlan
    # Defaulted so existing constructors need not pass it; 0 means "not launched"
    # (HUD disabled / integration), which kill_process_tree treats as a no-op.
    lock_hud_pid: int = 0
    core_hwnds: list[int] = field(default_factory=list)
    rfb_hwnd: int = 0
    # HWNDs resolved while every window was still visible; the dispatch
    # loop's role cache is seeded from this (hidden windows cannot be
    # re-resolved by pid/title lookups).
    role_hwnds: dict[str, int] = field(default_factory=dict)


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


def _startup_role_hwnds(
    *,
    portrait_hwnd: int,
    landscape_hwnd: int,
    genau_hwnd: int,
    nau_hwnd: int,
    dashboard_hwnd: int = 0,
    logs_hwnd: int = 0,
    rfb_hwnd: int = 0,
) -> dict[str, int]:
    """The managed windows by role, as resolved at startup."""
    return {
        "portrait": portrait_hwnd,
        "landscape": landscape_hwnd,
        "genau": genau_hwnd,
        "nau": nau_hwnd,
        "dashboard": dashboard_hwnd,
        "logs": logs_hwnd,
        "rfb": rfb_hwnd,
    }


def _apply_topmost_bands(role_hwnds: dict[str, int]) -> None:
    """Give each managed window its topmost flag from the shared ``role_topmost``
    policy for nau mode — the same policy omnipause and mode switches honor, so
    they can never disagree.

    Never call this while the loading overlay is up.  ``HWND_TOPMOST`` inserts a
    window at the *top* of the topmost band, and the overlay is itself topmost,
    so each promotion draws that window over the overlay until the overlay's next
    poll re-asserts itself — the flashing the overlay exists to prevent.
    """
    for role, hwnd in role_hwnds.items():
        if hwnd:
            set_always_on_top(hwnd, role_topmost(role, "nau"))


def _apply_primary_slot_visibility(nau_hwnd: int, genau_hwnd: int) -> None:
    """Park the idle slot-mate for nau startup mode.

    Nau and Genau share the primary rect; the slot swaps by minimizing the idle
    one (which keeps its taskbar button) and restoring the active one.  Disable
    both windows' DWM transitions first so those minimize/restores are instant —
    no visible animation.  Startup mode is nau, so Genau starts minimized.

    Safe behind the loading overlay: minimizing moves no window into the topmost
    band, so nothing can flash over it.
    """
    for hwnd in (nau_hwnd, genau_hwnd):
        if hwnd:
            disable_window_transitions(hwnd)
    if genau_hwnd:
        minimize_window(genau_hwnd, activate=False)


def _apply_startup_window_state(
    *,
    portrait_hwnd: int,
    landscape_hwnd: int,
    genau_hwnd: int,
    nau_hwnd: int,
    dashboard_hwnd: int = 0,
    logs_hwnd: int = 0,
    rfb_hwnd: int = 0,
) -> dict[str, int]:
    """Set the full window state for the nau startup mode: bands, then visibility.

    Only for callers with no loading overlay on screen — the integration path,
    which has nothing to hide behind, and ``_fix_post_loading_windows``, which
    runs after the overlay process has exited.
    """
    role_hwnds = _startup_role_hwnds(
        portrait_hwnd=portrait_hwnd,
        landscape_hwnd=landscape_hwnd,
        genau_hwnd=genau_hwnd,
        nau_hwnd=nau_hwnd,
        dashboard_hwnd=dashboard_hwnd,
        logs_hwnd=logs_hwnd,
        rfb_hwnd=rfb_hwnd,
    )
    _apply_topmost_bands(role_hwnds)
    _apply_primary_slot_visibility(nau_hwnd, genau_hwnd)
    return role_hwnds


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
    broker_launcher_raw = m["commands"].get("broker_tray_launcher", "").strip()
    provider_media_raw = m.get("provider_regen", "media_root", fallback="").strip()
    provider_metadata_raw = m.get("provider_regen", "metadata_root", fallback="").strip()
    start_core_session(
        project_dir=m["runtime"]["project_dir"],
        config_path=m["runtime"]["config_path"],
        broker_tray_launcher=Path(broker_launcher_raw) if broker_launcher_raw else None,
        random_favs_browser_manifest_file=m["random_favs_browser"]["manifest_file"],
        genau_paused_file=m["commands"]["genau_paused_file"],
        audio_paused_file=m["commands"]["audio_paused_file"],
        nau_paused_file=m["commands"]["nau_paused_file"],
        audio_volume_file=m["commands"]["audio_volume_file"],
        vlc_exe=m["executables"]["vlc_exe"],
        primary_sources=m["media"]["nau_library_sources"],
        portrait_sources=m["media"]["portrait_dirs"],
        landscape_sources=m["media"]["landscape_dirs"],
        favs_file=m["media"]["favs_file"],
        state_dir=state_dir,
        portrait_port=int(m["vlc"]["vlc2_port"]),
        landscape_port=int(m["vlc"]["vlc3_port"]),
        password=m["vlc"]["vlc_pass"],
        result_file=str(core_result_file),
        hide_windows=hide_windows,
        provider_media_root=Path(provider_media_raw) if provider_media_raw else None,
        provider_metadata_root=Path(provider_metadata_raw) if provider_metadata_raw else None,
    )
    core_pids = _read_result_pids(core_result_file)
    portrait_pid = core_pids["portrait_pid"]
    landscape_pid = core_pids["landscape_pid"]
    logger.info(
        "Core session launched: portrait=%d landscape=%d",
        portrait_pid, landscape_pid,
    )

    # Launch Genau and Nau as early as possible so they can initialise
    # pygame, scan media, and decode first frames while the rest of startup
    # continues.  Both share the Primary slot's rect, which depends only on
    # the secondary monitor + primary_top_ratio.
    primary_media_rect = _compute_primary_media_rect(m)
    genau_pid = launch_genau(
        python_exe=m["executables"]["genau_python_exe"],
        genau_module=m["modules"]["genau_module"],
        config_path=m["runtime"]["genau_config_path"],
        clips_folder=m["media"]["genau_clips"],
        genau_x=primary_media_rect.x,
        genau_y=primary_media_rect.y,
        genau_width=primary_media_rect.width,
        genau_height=primary_media_rect.height,
        command_file=m["commands"]["genau_cmd_file"],
        paused_file=m["commands"]["genau_paused_file"],
    )
    nau_pid = launch_nau(
        python_exe=m["executables"]["genau_python_exe"],
        nau_module=m["modules"]["nau_module"],
        config_path=m["runtime"]["genau_config_path"],
        playlist_file=m["commands"]["nau_playlist_file"],
        command_file=m["commands"]["nau_cmd_file"],
        paused_file=m["commands"]["nau_paused_file"],
        status_file=m["commands"]["nau_status_file"],
        nau_x=primary_media_rect.x,
        nau_y=primary_media_rect.y,
        nau_width=primary_media_rect.width,
        nau_height=primary_media_rect.height,
        metadata_dir=provider_metadata_raw or None,
    )

    # --- Phase 2: Compute window layout ---
    progress.advance("Computing window layout...")
    layout_cfg = _layout_config_from_manifest(m)
    monitors = enumerate_monitors()
    main_rect, secondary_rect = get_logical_monitor_rects(
        monitors, main_index=layout_cfg.main_monitor, secondary_index=layout_cfg.secondary_monitor,
    )

    plan = compute_window_layout(
        main_monitor=main_rect,
        secondary_monitor=secondary_rect,
        layout_config=layout_cfg,
    )

    skip_activate = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
    role_hwnds: dict[str, int] = {}

    if not hide_windows:
        # --- Normal mode: position immediately ---
        progress.advance("Positioning windows...")
        _position_pid_window(portrait_pid, plan.portrait, "portrait VLC", activate=not skip_activate)
        _position_pid_window(landscape_pid, plan.landscape, "landscape VLC", activate=not skip_activate)
        logger.info("Core windows positioned")

        progress.advance("Finalizing window layout...")
        role_hwnds = _apply_startup_window_state(
            portrait_hwnd=find_window_by_pid(portrait_pid),
            landscape_hwnd=find_window_by_pid(landscape_pid),
            genau_hwnd=wait_for_window_by_title("Genau", timeout_s=3.0),
            nau_hwnd=wait_for_window(nau_pid, timeout_s=3.0)
            or wait_for_window_by_title("Nau", timeout_s=3.0, exact=True),
        )
        logger.info("Startup window state applied")

    # --- Phase 2.5: Launch Random Favs Browser ---
    progress.advance("Launching browser...")
    rfb_hwnd = _maybe_launch_random_favs_browser(m, plan)

    # --- Phase 3: Launch UI companions ---
    progress.advance("Launching companions...")
    time.sleep(1.2)

    dashboard_enabled = m["dashboard"]["enabled"].strip() not in {"", "0", "false", "False"}
    ui_result_file = _build_unique_result_path(state_dir, "ui_companions")
    launch_ui_companions(
        python_exe=m["executables"]["python_exe"],
        dashboard_module=m["modules"]["dashboard_module"],
        dashboard_enabled=dashboard_enabled,
        lock_hud_module=m["modules"]["lock_hud_module"],
        # The HUD rides the dashboard's enable gate so integration's
        # FUN_TIME_DISABLE_DASHBOARD keeps both always-on-top overlays off.
        hud_enabled=dashboard_enabled,
        windows_bridge_manifest_path=str(manifest_path),
        dashboard_x=plan.dashboard.x,
        dashboard_y=plan.dashboard.y,
        dashboard_width=plan.dashboard.width,
        dashboard_height=plan.dashboard.height,
        # The reference popup opens over the RFB's rect, so the dashboard needs it.
        rfb_x=plan.random_favs_browser.x,
        rfb_y=plan.random_favs_browser.y,
        rfb_width=plan.random_favs_browser.width,
        rfb_height=plan.random_favs_browser.height,
        # The log panel is a second window the dashboard process owns.
        log_x=plan.log_panel.x,
        log_y=plan.log_panel.y,
        log_width=plan.log_panel.width,
        log_height=plan.log_panel.height,
        audio_module=m["modules"]["audio_module"],
        config_path=m["runtime"]["config_path"],
        audio_folder=m["media"]["genau_audio"],
        result_file=str(ui_result_file),
    )
    ui_pids = _read_result_pids(ui_result_file)

    # --- Phase 4 (loading screen only): batch-position everything at once ---
    collected_hwnds: list[int] = []
    if hide_windows:
        progress.advance("Positioning windows...")

        # Start the two satellites playing.  Their playlists were enqueued but
        # never played during loading.  They launch with --no-audio, so nothing
        # here can be heard and VLC's volume is never touched.
        portrait_port = int(m["vlc"]["vlc2_port"])
        landscape_port = int(m["vlc"]["vlc3_port"])
        password = m["vlc"]["vlc_pass"]
        for port in [portrait_port, landscape_port]:
            vlc_http_cmd(port, "pl_play", password)

        _position_pid_window(portrait_pid, plan.portrait, "portrait VLC", activate=False)
        _position_pid_window(landscape_pid, plan.landscape, "landscape VLC", activate=False)
        logger.info("Core windows positioned (deferred reveal)")

        # Collect core window handles for StartupResult
        for pid in [portrait_pid, landscape_pid, nau_pid]:
            hwnd = find_window_by_pid(pid)
            if hwnd:
                collected_hwnds.append(hwnd)

        # Resolve every managed window and park the idle slot-mate.  The topmost
        # bands are deliberately NOT applied here: the overlay is topmost, and
        # HWND_TOPMOST inserts above it, so each promotion would flash its window
        # over the overlay.  _fix_post_loading_windows applies them once the
        # overlay process has exited.  This is still the last moment the dashboard
        # and log panel are resolvable, so their handles are captured now.
        dashboard_pid = ui_pids["dashboard_pid"]
        dash_hwnd = 0
        logs_hwnd = 0
        if dashboard_pid:
            # The dashboard is hidden (SW_HIDE) behind the loading overlay here,
            # so both lookups must include hidden windows — a visible-only lookup
            # returns 0 and leaves the dispatch loop unable to manage it.  The
            # window PID also differs from the venv-launcher PID, so the exact
            # title lookup is the path that actually resolves it in production.
            dash_hwnd = find_window_by_pid(dashboard_pid, include_hidden=True)
            if not dash_hwnd:
                dash_hwnd = wait_for_window_by_title(
                    "Fun Time", timeout_s=5.0, exact=True, include_hidden=True
                )
            # The log panel shares the dashboard's process, so only its title
            # tells the two windows apart.
            logs_hwnd = wait_for_window_by_title(
                LOG_PANEL_WINDOW_TITLE, timeout_s=5.0, exact=True, include_hidden=True
            )

        role_hwnds = _startup_role_hwnds(
            rfb_hwnd=rfb_hwnd,
            portrait_hwnd=find_window_by_pid(portrait_pid),
            landscape_hwnd=find_window_by_pid(landscape_pid),
            genau_hwnd=wait_for_window_by_title("Genau", timeout_s=5.0),
            nau_hwnd=wait_for_window(nau_pid, timeout_s=5.0)
            or wait_for_window_by_title("Nau", timeout_s=5.0, exact=True),
            dashboard_hwnd=dash_hwnd,
            logs_hwnd=logs_hwnd,
        )
        _apply_primary_slot_visibility(role_hwnds["nau"], role_hwnds["genau"])
        logger.info("Startup windows resolved and parked (bands deferred past the overlay)")

        progress.advance("Finalizing...")

    # The reveal: startup mode is nau, so Nau starts playing once startup
    # completes. This runs in both paths — the loading-screen (hide_windows)
    # path reveals everything at once, and the no-loading-screen path
    # (integration) has nothing to hide behind but must still start Nau.
    write_flag_file(m["commands"]["nau_paused_file"], False)

    return StartupResult(
        nau_pid=nau_pid,
        portrait_pid=portrait_pid,
        landscape_pid=landscape_pid,
        dashboard_pid=ui_pids["dashboard_pid"],
        genau_pid=genau_pid,
        audio_pid=ui_pids["audio_pid"],
        lock_hud_pid=ui_pids.get("lock_hud_pid", 0),
        layout_plan=plan,
        core_hwnds=collected_hwnds,
        role_hwnds=role_hwnds,
        rfb_hwnd=rfb_hwnd,
    )


def _compute_primary_media_rect(m: configparser.ConfigParser) -> WindowRect:
    """The Primary display slot shared by Genau and Nau.

    Depends only on the secondary monitor dimensions and primary_top_ratio,
    so both apps can launch before the full layout is computed.
    """
    layout_cfg = _layout_config_from_manifest(m)
    monitors = enumerate_monitors()
    _, secondary_rect = get_logical_monitor_rects(
        monitors, main_index=layout_cfg.main_monitor,
        secondary_index=layout_cfg.secondary_monitor,
    )
    return compute_primary_media_rect(secondary_monitor=secondary_rect, layout_config=layout_cfg)


def _layout_config_from_manifest(m: configparser.ConfigParser) -> LayoutConfig:
    return LayoutConfig(
        main_monitor=int(m["layout"]["main_monitor"]),
        secondary_monitor=int(m["layout"]["secondary_monitor"]),
        primary_top_ratio=float(m["layout"]["primary_top_ratio"]),
        landscape_width_ratio=float(m["layout"]["landscape_width_ratio"]),
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


def resolve_shortcut(shortcut_path: str) -> tuple[str, str, str]:
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
) -> int:
    """Launch the Random Favs Browser if enabled and position it.

    Returns the browser window handle (0 if not launched).  The handle is
    needed so the dispatch loop can include RFB in omnipause topmost management.
    """
    if m["random_favs_browser"]["enabled"] != "1":
        return 0

    shortcut_path = m["random_favs_browser"]["shortcut_path"]
    manifest_file = m["random_favs_browser"]["manifest_file"]

    target, work_dir, args = resolve_shortcut(shortcut_path)
    if not target:
        logger.warning("Random Favs Browser skipped: could not resolve shortcut %s", shortcut_path)
        return 0

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
        return 0

    # Wait for a new Chrome window to appear
    new_hwnd = _wait_for_new_chrome_window(before_hwnds, timeout_ms=8000)
    if not new_hwnd:
        logger.warning("Random Favs Browser skipped: no new Chrome window appeared")
        return 0

    # Position the browser window
    rect = plan.random_favs_browser
    no_activate = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
    move_window(new_hwnd, rect.x, rect.y, rect.width, rect.height, activate=not no_activate)

    # The RFB's static topmost flag is applied by Phase 4's
    # _apply_startup_window_state; nothing window-related to do here.

    logger.info("Random Favs Browser positioned")
    return new_hwnd


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

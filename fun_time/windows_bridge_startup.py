from __future__ import annotations

import configparser
import logging
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from app_support import osr2_hold
from app_support.siblings import primary_checkout_of
from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.win32 import process_started
from funestra import broker
from funestra.file_channel import append_command
from funestra.modes import MainMode
from funestra.robot_hand import FULL_INTENSITY

from main_funestra.genau_verbs import orientation_words
from satellite.contract import SatelliteChannels, WindowPlacement

from . import preview_marker
from .audio_volume import MAX_VOLUME, publish_audio_level
from .broker_control import write_broker_command
from .checkout_overrides import is_a_worktree, sibling_checkouts_kwargs
from .child_launch import no_child_log, open_child_log
from .config import load_config
from .content import load_web_providers
from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import take_back_the_list
from .funestra_settings import reports_published_in
from .funestra_status import read_main_funestra_status
from .hosted_contract import the_args_it_declares
from .inclusion_pairs import BOTH, Included
from .main_list import MainList, MainListSettings
from .max_intensity import publish_max_intensity
from .mode_plan import STARTUP_MAIN_MODE, show_verb
from .modes import SatelliteBuild, build_satellite_playlists
from .process_identity import NAMER
from .process_sweep import processes_matching
from .process_tree import kill_process_tree
from .project_paths import PROJECT_ICON
from .random_favs_browser import build_manifest, write_manifest
from .rfb_tab_page import tabs_dir, write_tab_pages
from .runtime_flow import write_flag_file
from .satellite_control import read_satellite_status
from .satellite_launch import SatelliteLaunch, for_funestra
from .session_resume import (
    open_the_main_list,
    resume_main_loop,
    resume_playlists,
    resume_satellite_locks,
    resume_shared_state,
    resume_what_lives_in_a_funestra,
)
from .shared_state import shared_state_path
from .win32_process import unix_seconds

logger = logging.getLogger(__name__)


SATELLITE_PORTRAIT_TITLE = "Portrait AI Funestra"
SATELLITE_LANDSCAPE_TITLE = "Landscape AI Funestra"

def taskbar_identity_args() -> tuple[str, str]:
    return ("--taskbar-identity", preview_marker.session_identity(preview_marker.shown_as()))


def _the_session_icon() -> str:
    return str(preview_marker.icon_file(PROJECT_ICON, preview_marker.shown_as()))


def _write_result_file(result_file: str | Path, values: dict[str, int | str]) -> None:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["result"] = {key: str(value) for key, value in values.items()}
    result_path = Path(result_file)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


def stop_broker_processes(broker_tray_launcher: Path | str | None) -> None:
    contract = broker.read_contract(broker_tray_launcher)
    if contract is None:
        return
    for found in (*processes_matching(contract.image_pattern, contract.command_line_pattern),
                  *processes_matching(r"^wscript\.exe$", contract.launcher_pattern)):
        kill_process_tree(found.pid)


def reap_orphaned_satellites(
    satellite_module: str, status_files: Sequence[str | Path],
) -> None:
    if not status_files:
        return
    claimed = [str(path) for path in status_files]
    for found in processes_matching(NAMER.process_name_pattern,
                                    rf"-m\s+{re.escape(satellite_module)}(\s|$)"):
        if any(path in found.command_line for path in claimed):
            kill_process_tree(found.pid)


def broker_source_mtime(broker_tray_launcher: Path | None) -> float | None:
    contract = broker.read_contract(broker_tray_launcher)
    if contract is None:
        return None
    package = broker_tray_launcher.parent / contract.package_dir
    try:
        return max((source.stat().st_mtime for source in package.rglob("*.py")), default=None)
    except OSError:
        return None


def broker_process_started_at(broker_tray_launcher: Path | str | None) -> float | None:
    contract = broker.read_contract(broker_tray_launcher)
    if contract is None:
        return None
    started = [created for found in processes_matching(
                   contract.image_pattern, contract.broker_command_line_pattern)
               if (created := process_started(found.pid)) is not None]
    return unix_seconds(min(started)) if started else None


def ensure_broker(
    broker_state_dir: str | Path | None,
    broker_tray_launcher: Path | None = None,
) -> None:
    source_mtime = broker_source_mtime(broker_tray_launcher)
    if source_mtime is not None:
        started_at = broker_process_started_at(broker_tray_launcher)
        if started_at is not None and source_mtime > started_at:
            logger.info(
                "Broker started %.0fs before its own code was last written; restarting it",
                source_mtime - started_at,
            )
            stop_broker_processes(broker_tray_launcher)
            broker.start(broker_tray_launcher)
            return
    if broker_state_dir is not None and broker.is_running(Path(broker_state_dir)):
        return
    broker.start(broker_tray_launcher)


def prepare_random_favs_browser_manifest(config_path: str | Path, output_path: str | Path) -> None:
    config = load_config(config_path)
    profile_directory, targets = build_manifest(config, load_web_providers())
    urls = (
        write_tab_pages(
            tabs_dir(config.paths.state_dir), targets, loopback_port=config.loopback_port)
        if config.random_favs_browser.lazy_load
        else [target.url for target in targets]
    )
    write_manifest(Path(output_path), profile_directory, urls)


def seed_startup_states(
    genau_paused_file: str | Path,
    audio_paused_file: str | Path,
    main_funestra_paused_file: str | Path,
    audio_volume_file: str | Path,
    genau_cmd_file: str | Path,
    *,
    main_funestra_cmd_file: str | Path,
    volume: int = MAX_VOLUME,
    muted: bool = False,
    max_intensity: int = FULL_INTENSITY,
    satellite_cmd_files: tuple[str | Path, ...],
    main_list: MainListSettings | None = None,
    mode: MainMode = STARTUP_MAIN_MODE,
) -> None:
    Path(genau_cmd_file).parent.mkdir(parents=True, exist_ok=True)
    Path(genau_cmd_file).write_text("PAUSE\n", encoding="utf-8")
    append_command(Path(main_funestra_cmd_file), show_verb(mode))
    for path in (genau_paused_file, audio_paused_file, main_funestra_paused_file):
        write_flag_file(path, True)
    publish_audio_level(
        main_funestra_cmd_file=Path(main_funestra_cmd_file),
        genau_cmd_file=Path(genau_cmd_file),
        audio_volume_file=Path(audio_volume_file),
        volume=volume,
        muted=muted,
    )
    for label in (main_list or MainListSettings()).labels:
        append_command(Path(main_funestra_cmd_file), label)
    if max_intensity < FULL_INTENSITY:
        publish_max_intensity(
            (Path(command_file) for command_file in
             (genau_cmd_file, main_funestra_cmd_file, *satellite_cmd_files)),
            max_intensity=max_intensity)


def reset_satellite_paused_states(
    portrait_paused_file: str | Path,
    landscape_paused_file: str | Path,
) -> None:
    for path in (Path(portrait_paused_file), Path(landscape_paused_file)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("0", encoding="utf-8")


def start_core_session(
    *,
    config_path: str | Path,
    broker_cmd_file: str | Path,
    broker_tray_launcher: Path | None = None,
    broker_state_dir: str | Path | None = None,
    random_favs_browser_manifest_file: str | Path,
    genau_paused_file: str | Path,
    genau_cmd_file: str | Path | None = None,
    audio_paused_file: str | Path,
    main_funestra_paused_file: str | Path,
    audio_volume_file: str | Path,
    main_funestra_cmd_file: str | Path,
    satellite_python_exe: str | Path,
    satellite_module: str,
    portrait: SatelliteLaunch,
    landscape: SatelliteLaunch,
    main_funestra_status_file: str | Path,
    main_sources: str,
    state_dir: str | Path,
    result_file: str | Path,
    dashboard_cmd_file: str | Path | None = None,
    regen_metadata_root: Path | None = None,
    sibling_checkouts: str | None = None,
) -> str:
    portrait = for_funestra(portrait, FunTimeFunestra.PORTRAIT)
    landscape = for_funestra(landscape, FunTimeFunestra.LANDSCAPE)
    reap_orphaned_satellites(
        satellite_module, [portrait.channels.status, landscape.channels.status],
    )
    write_broker_command(broker_cmd_file, osr2_hold.PARK)
    ensure_broker(broker_state_dir, broker_tray_launcher)
    state_path = Path(state_dir)
    main_funestra_status = read_main_funestra_status(Path(main_funestra_status_file))
    portrait_status = read_satellite_status(Path(portrait.channels.status))
    landscape_status = read_satellite_status(Path(landscape.channels.status))
    for launch in (portrait, landscape):
        if take_back_the_list(Path(launch.channels.playlist)):
            logger.info("Took %s's own clips back from the hosted app", launch.funestra.label)
    resumed = resume_playlists([
        (Path(portrait.channels.playlist), portrait_status.video),
        (Path(landscape.channels.playlist), landscape_status.video),
    ])
    carried = resume_shared_state(
        shared_state_path(state_path), resumed=resumed,
        last_reported=reports_published_in(
            state_path, main_status_file=Path(main_funestra_status_file),
            satellite_status_files={launch.funestra: Path(launch.channels.status)
                                    for launch in (portrait, landscape)}))
    main_list = MainList(sources=main_sources, state_dir=state_path,
                         metadata_root=regen_metadata_root)
    carried = open_the_main_list(main_list, carried, state_file=shared_state_path(state_path),
                                 on_screen=main_funestra_status.video)
    seed_startup_states(
        genau_paused_file, audio_paused_file, main_funestra_paused_file, audio_volume_file,
        genau_cmd_file, main_funestra_cmd_file=main_funestra_cmd_file,
        volume=carried.volume, muted=carried.muted, max_intensity=carried.max_intensity,
        satellite_cmd_files=(portrait.channels.command, landscape.channels.command),
        main_list=MainListSettings.of(carried), mode=carried.main_mode,
    )
    reset_satellite_paused_states(portrait.channels.paused, landscape.channels.paused)
    prepare_random_favs_browser_manifest(config_path, random_favs_browser_manifest_file)
    if not resumed:
        build_satellite_playlists(
            portrait=SatelliteBuild(sources=portrait.sources),
            landscape=SatelliteBuild(sources=landscape.sources),
            state_dir=state_path,
            metadata_root=regen_metadata_root,
        )
    logger.info(
        "Resumed last session's playlists"
        if resumed
        else "Nothing to resume; built fresh playlists"
    )
    resume_satellite_locks([
        (Path(portrait.channels.command), carried.satellite(FunTimeFunestra.PORTRAIT).locked),
        (Path(landscape.channels.command), carried.satellite(FunTimeFunestra.LANDSCAPE).locked),
    ])
    resume_main_loop(Path(main_funestra_cmd_file), carried, main_list.file)
    resume_what_lives_in_a_funestra(
        carried, main_funestra_cmd_file=Path(main_funestra_cmd_file),
        satellite_cmd_files={launch.funestra: Path(launch.channels.command)
                             for launch in (portrait, landscape)},
        genau_cmd_file=Path(genau_cmd_file),
    )
    launch_core_apps(
        python_exe=satellite_python_exe,
        satellite_module=satellite_module,
        portrait=portrait,
        landscape=landscape,
        result_file=result_file,
        dashboard_cmd_file=dashboard_cmd_file,
        sibling_checkouts=sibling_checkouts,
    )
    return carried.main_mode


def _start_a_funestra(cmd: list[str], *, log_file: str | Path, sibling_checkouts: str | None) -> int:
    with open_child_log(log_file, cmd) as log:
        proc = subprocess.Popen(
            cmd, stdout=log, stderr=log,
            **sibling_checkouts_kwargs(sibling_checkouts), **hidden_subprocess_kwargs())
    return proc.pid


@dataclass(frozen=True)
class HandedFunestra:
    playlist_file: str | Path
    cmd_file: str | Path
    status_file: str | Path
    hud_file: str | Path


def _rect_args(prefix: str, rect) -> list[str]:
    return [word
            for field in ("x", "y", "width", "height")
            for word in (f"--{prefix}{field}", str(getattr(rect, field)))]


def origenerator_session_args(
    *,
    layout_plan,
    command_file: str | Path,
    paused_file: str | Path,
    status_file: str | Path,
    dashboard_cmd_file: str | Path,
    funestras: Mapping[str, HandedFunestra],
    frames_file: str | Path | None = None,
    input_file: str | Path | None = None,
) -> list[str]:
    args = ["--fun-time", *_rect_args("", layout_plan.random_favs_browser)]
    args.extend(_rect_args("portrait-", layout_plan.portrait))
    args.extend(_rect_args("landscape-", layout_plan.landscape))
    for satellite, funestra in funestras.items():
        args.extend([
            f"--{satellite}-playlist", str(funestra.playlist_file),
            f"--{satellite}-cmd-file", str(funestra.cmd_file),
            f"--{satellite}-status-file", str(funestra.status_file),
            f"--{satellite}-hud-file", str(funestra.hud_file),
        ])
    if frames_file is not None and input_file is not None:
        args.extend(["--frames-file", str(frames_file),
                     "--input-file", str(input_file)])
    args.extend(taskbar_identity_args())
    args.extend([
        "--command-file", str(command_file),
        "--paused-file", str(paused_file),
        "--status-file", str(status_file),
        "--dashboard-cmd-file", str(dashboard_cmd_file),
    ])
    return args


ORIGENERATOR_MODULE = "origenerator"


def origenerator_launch_command(
        *, python_exe: str | Path, origenerator_dir: str | Path, **contract) -> list[str]:
    return [NAMER.named_exe(python_exe, "Origenerator"), "-m", ORIGENERATOR_MODULE,
            *the_args_it_declares(origenerator_dir, origenerator_session_args(**contract))]


def origenerator_interpreter(origenerator_dir: str | Path) -> Path:
    checkout = Path(origenerator_dir)
    installed_in = primary_checkout_of(checkout) if is_a_worktree(checkout) else checkout
    return installed_in / ".venv" / "Scripts" / "python.exe"


def origenerator_launch_kwargs(
    *,
    origenerator_dir: str | Path,
    sibling_checkouts: str | None = None,
) -> dict:
    kwargs: dict = {"cwd": str(origenerator_dir),
                    **sibling_checkouts_kwargs(sibling_checkouts),
                    **hidden_subprocess_kwargs()}
    checkout = Path(origenerator_dir)
    if is_a_worktree(checkout):
        env = kwargs.get("env") or {**os.environ}
        kwargs["env"] = {**env, "ORIGENERATOR_BRANCH_SESSION": "1"}
    return kwargs


def launch_origenerator(
    *,
    python_exe: str | Path,
    origenerator_dir: str | Path,
    layout_plan,
    command_file: str | Path,
    paused_file: str | Path,
    status_file: str | Path,
    dashboard_cmd_file: str | Path,
    funestras: Mapping[str, HandedFunestra],
    frames_file: str | Path | None = None,
    input_file: str | Path | None = None,
    sibling_checkouts: str | None = None,
) -> int:
    cmd = origenerator_launch_command(
        python_exe=python_exe, origenerator_dir=origenerator_dir,
        layout_plan=layout_plan, command_file=command_file,
        paused_file=paused_file, status_file=status_file,
        dashboard_cmd_file=dashboard_cmd_file, funestras=funestras,
        frames_file=frames_file, input_file=input_file,
    )
    kwargs = origenerator_launch_kwargs(
        origenerator_dir=origenerator_dir, sibling_checkouts=sibling_checkouts)
    proc = subprocess.Popen(cmd, **no_child_log(), **kwargs)
    return proc.pid


def launch_main_funestra(
    *,
    python_exe: str | Path,
    main_funestra_module: str,
    config_path: str | Path,
    playlist_file: str | Path,
    command_file: str | Path,
    paused_file: str | Path,
    status_file: str | Path,
    console_file: str | Path,
    drive_file: str | Path,
    dashboard_cmd_file: str | Path,
    log_file: str | Path,
    state_dir: str | Path,
    main_funestra_x: int,
    main_funestra_y: int,
    main_funestra_width: int,
    main_funestra_height: int,
    genau_command_file: str | Path,
    genau_paused_file: str | Path,
    genau_status_file: str | Path,
    flicks_dir: str | Path | None = None,
    metadata_dir: str | Path | None = None,
    genau_start_flick: str = "",
    genau_latest: bool = False,
    genau_f_mode: bool = False,
    genau_orientations: Included = BOTH,
    notice_file: str | Path | None = None,
    osr2_port: str | None = None,
    sibling_checkouts: str | None = None,
) -> int:
    cmd = [
        NAMER.named_exe(python_exe, "MainFunestra"),
        "-m",
        main_funestra_module,
        "--config",
        str(config_path),
        "--playlist",
        str(playlist_file),
        "--command-file",
        str(command_file),
        "--paused-file",
        str(paused_file),
        "--status-file",
        str(status_file),
        "--state-dir",
        str(state_dir),
        "--console-file",
        str(console_file),
        "--drive-file",
        str(drive_file),
        "--dashboard-cmd-file",
        str(dashboard_cmd_file),
        "--x",
        str(main_funestra_x),
        "--y",
        str(main_funestra_y),
        "--width",
        str(main_funestra_width),
        "--height",
        str(main_funestra_height),
        "--icon",
        _the_session_icon(),
        *taskbar_identity_args(),
        "--genau-command-file",
        str(genau_command_file),
        "--genau-paused-file",
        str(genau_paused_file),
        "--genau-status-file",
        str(genau_status_file),
    ]
    if flicks_dir:
        cmd += ["--flicks-dir", str(flicks_dir)]
    if genau_start_flick:
        cmd += ["--genau-start-flick", genau_start_flick]
    if genau_latest:
        cmd.append("--genau-latest")
    if genau_f_mode:
        cmd.append("--genau-f-mode")
    if genau_orientations != BOTH:
        cmd += ["--genau-orientations", *orientation_words(genau_orientations)]
    if notice_file:
        cmd += ["--notice-file", str(notice_file)]
    if osr2_port:
        cmd += ["--osr2-port", osr2_port]
    if metadata_dir:
        cmd += ["--metadata-dir", str(metadata_dir)]
    return _start_a_funestra(cmd, log_file=log_file, sibling_checkouts=sibling_checkouts)


def launch_audio_companion(
    *,
    python_exe: str | Path,
    audio_module: str,
    config_path: str | Path,
    audio_folder: str | Path,
    audio_device: str | None = None,
    sibling_checkouts: str | None = None,
) -> subprocess.Popen:
    cmd = [
        NAMER.named_exe(str(python_exe), "AudioCompanion"),
        "-m",
        audio_module,
        "--config",
        str(config_path),
        "--audio-folder",
        str(audio_folder),
    ]
    if audio_device:
        cmd.extend(["--audio-device", audio_device])
    return subprocess.Popen(cmd, **no_child_log(), **sibling_checkouts_kwargs(sibling_checkouts),
                            **hidden_subprocess_kwargs())


def launch_ui_companions(
    *,
    python_exe: str | Path,
    dashboard_module: str,
    dashboard_enabled: bool,
    windows_bridge_manifest_path: str | Path,
    dashboard_x: int,
    dashboard_y: int,
    dashboard_width: int,
    dashboard_height: int,
    rfb_x: int,
    rfb_y: int,
    rfb_width: int,
    rfb_height: int,
    dashboard_log_file: str | Path | None = None,
    audio_module: str,
    config_path: str | Path,
    audio_folder: str | Path,
    result_file: str | Path,
    sibling_checkouts: str | None = None,
) -> None:
    python_exe = str(python_exe)
    windows_bridge_manifest_path = str(windows_bridge_manifest_path)
    config_path = str(config_path)
    audio_folder = str(audio_folder)

    dashboard_pid = 0
    if dashboard_enabled:
        dashboard_cmd = [
            NAMER.named_exe(python_exe, "Dashboard"),
            "-m",
            dashboard_module,
            windows_bridge_manifest_path,
            "--x",
            str(dashboard_x),
            "--y",
            str(dashboard_y),
            "--width",
            str(dashboard_width),
            "--height",
            str(dashboard_height),
            "--rfb-x",
            str(rfb_x),
            "--rfb-y",
            str(rfb_y),
            "--rfb-width",
            str(rfb_width),
            "--rfb-height",
            str(rfb_height),
        ]
        if dashboard_log_file is not None:
            with open_child_log(dashboard_log_file, dashboard_cmd) as log:
                dashboard_proc = subprocess.Popen(
                    dashboard_cmd, stdout=log, stderr=subprocess.STDOUT,
                    **sibling_checkouts_kwargs(sibling_checkouts),
                    **hidden_subprocess_kwargs(),
                )
        else:
            dashboard_proc = subprocess.Popen(
                dashboard_cmd, **no_child_log(),
                **sibling_checkouts_kwargs(sibling_checkouts),
                **hidden_subprocess_kwargs())
        dashboard_pid = dashboard_proc.pid

    audio_proc = launch_audio_companion(
        python_exe=python_exe, audio_module=audio_module,
        config_path=config_path, audio_folder=audio_folder,
        sibling_checkouts=sibling_checkouts,
    )

    _write_result_file(
        result_file,
        {
            "dashboard_pid": dashboard_pid,
            "audio_pid": audio_proc.pid,
        },
    )


def launch_core_apps(
    *,
    python_exe: str | Path,
    satellite_module: str,
    portrait: SatelliteLaunch,
    landscape: SatelliteLaunch,
    result_file: str | Path,
    dashboard_cmd_file: str | Path | None = None,
    sibling_checkouts: str | None = None,
) -> None:
    portrait = for_funestra(portrait, FunTimeFunestra.PORTRAIT)
    landscape = for_funestra(landscape, FunTimeFunestra.LANDSCAPE)

    def _launch(launch: SatelliteLaunch, title: str, role: str, *, tiles: bool) -> int:
        return launch_satellite(
            python_exe=python_exe,
            satellite_module=satellite_module,
            channels=replace(launch.channels, dashboard_cmd=dashboard_cmd_file),
            placement=WindowPlacement(
                x=launch.rect.x, y=launch.rect.y,
                width=launch.rect.width, height=launch.rect.height,
                title=title,
                taskbar_identity=taskbar_identity_args()[1],
                icon=Path(_the_session_icon()),
                tiles=tiles),
            role=role,
            log_file=launch.log_file,
            sibling_checkouts=sibling_checkouts,
        )

    portrait_pid = _launch(portrait, SATELLITE_PORTRAIT_TITLE, "Portrait", tiles=True)
    landscape_pid = _launch(landscape, SATELLITE_LANDSCAPE_TITLE, "Landscape", tiles=False)
    _write_result_file(
        result_file,
        {
            "portrait_pid": portrait_pid,
            "landscape_pid": landscape_pid,
        },
    )


def _build_satellite_launch_command(
    python_exe: str | Path,
    satellite_module: str,
    *,
    channels: SatelliteChannels,
    placement: WindowPlacement,
) -> list[str]:
    return [
        str(python_exe),
        "-m",
        str(satellite_module),
        *placement.to_argv(),
        *channels.to_argv(),
    ]


def launch_satellite(
    *,
    python_exe: str | Path,
    satellite_module: str,
    channels: SatelliteChannels,
    placement: WindowPlacement,
    role: str,
    log_file: str | Path,
    sibling_checkouts: str | None = None,
) -> int:
    cmd = _build_satellite_launch_command(
        NAMER.named_exe(python_exe, role), satellite_module,
        channels=channels, placement=placement)
    return _start_a_funestra(cmd, log_file=log_file, sibling_checkouts=sibling_checkouts)

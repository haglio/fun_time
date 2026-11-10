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
from funestra.robot_hand import FULL_INTENSITY

from main_funestra.genau_verbs import orientation_words
from satellite.contract import SatelliteChannels, WindowPlacement

from . import preview_marker
from .audio_volume import publish_audio_level
from .broker_control import write_broker_command
from .checkout_overrides import is_a_worktree, sibling_checkouts_kwargs
from .child_launch import no_child_log, open_child_log
from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import take_back_the_list
from .funestra_settings import reports_published_in
from .funestra_status import read_main_funestra_status
from .hosted_contract import the_args_it_declares
from .inclusion_pairs import BOTH, Included
from .main_list import MainList, MainListSettings
from .manifest import CommandFiles, LaunchManifest
from .max_intensity import publish_max_intensity
from .mode_plan import show_verb
from .modes import SatelliteBuild, build_satellite_playlists
from .process_identity import NAMER
from .process_sweep import processes_matching
from .process_tree import kill_process_tree
from .project_paths import PROJECT_ICON
from .random_favs_browser_manifest import prepare_random_favs_browser_manifest
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
from .shared_state import BridgeState, shared_state_path
from .win32_process import unix_seconds
from .window_layout import WindowLayoutPlan, WindowRect

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


def seed_startup_states(manifest: LaunchManifest, carried: BridgeState) -> None:
    commands = manifest.commands
    genau_cmd_file = Path(commands.genau_cmd_file)
    main_funestra_cmd_file = Path(commands.main_funestra_cmd_file)
    genau_cmd_file.parent.mkdir(parents=True, exist_ok=True)
    genau_cmd_file.write_text("PAUSE\n", encoding="utf-8")
    append_command(main_funestra_cmd_file, show_verb(carried.main_mode))
    for path in (commands.genau_paused_file, commands.audio_paused_file,
                 commands.main_funestra_paused_file):
        write_flag_file(path, True)
    publish_audio_level(
        main_funestra_cmd_file=main_funestra_cmd_file,
        genau_cmd_file=genau_cmd_file,
        audio_volume_file=Path(commands.audio_volume_file),
        volume=carried.volume,
        muted=carried.muted,
    )
    for label in MainListSettings.of(carried).labels:
        append_command(main_funestra_cmd_file, label)
    if carried.max_intensity < FULL_INTENSITY:
        publish_max_intensity(
            (genau_cmd_file, main_funestra_cmd_file,
             *(manifest.satellite(funestra).cmd_file for funestra in FunTimeFunestra.SATELLITES)),
            max_intensity=carried.max_intensity)


def reset_satellite_paused_states(
    portrait_paused_file: str | Path,
    landscape_paused_file: str | Path,
) -> None:
    for path in (Path(portrait_paused_file), Path(landscape_paused_file)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("0", encoding="utf-8")


def the_broker_launcher(commands: CommandFiles) -> Path | None:
    raw = commands.broker_tray_launcher.strip()
    return Path(raw) if raw else None


def start_core_session(
    m: LaunchManifest,
    *,
    portrait: SatelliteLaunch,
    landscape: SatelliteLaunch,
    state_dir: str | Path,
    result_file: str | Path,
) -> str:
    commands = m.commands
    regen_metadata_root = Path(m.regen.metadata_root.strip()) if m.regen.metadata_root.strip() else None
    portrait = for_funestra(portrait, FunTimeFunestra.PORTRAIT)
    landscape = for_funestra(landscape, FunTimeFunestra.LANDSCAPE)
    reap_orphaned_satellites(
        m.modules.satellite_module, [portrait.channels.status, landscape.channels.status],
    )
    write_broker_command(commands.broker_cmd_file, osr2_hold.PARK)
    ensure_broker(commands.broker_state_dir or None, the_broker_launcher(commands))
    state_path = Path(state_dir)
    main_funestra_status = read_main_funestra_status(Path(commands.main_funestra_status_file))
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
            state_path, main_status_file=Path(commands.main_funestra_status_file),
            satellite_status_files={launch.funestra: Path(launch.channels.status)
                                    for launch in (portrait, landscape)}))
    main_list = MainList(sources=m.media.main_funestra_library_sources, state_dir=state_path,
                         metadata_root=regen_metadata_root)
    carried = open_the_main_list(main_list, carried, state_file=shared_state_path(state_path),
                                 on_screen=main_funestra_status.video)
    seed_startup_states(m, carried)
    reset_satellite_paused_states(portrait.channels.paused, landscape.channels.paused)
    prepare_random_favs_browser_manifest(
        m.runtime.config_path, m.random_favs_browser.manifest_file)
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
    resume_main_loop(Path(commands.main_funestra_cmd_file), carried, main_list.file)
    resume_what_lives_in_a_funestra(
        carried, main_funestra_cmd_file=Path(commands.main_funestra_cmd_file),
        satellite_cmd_files={launch.funestra: Path(launch.channels.command)
                             for launch in (portrait, landscape)},
        genau_cmd_file=Path(commands.genau_cmd_file),
    )
    launch_core_apps(m, portrait, landscape, result_file)
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


@dataclass(frozen=True)
class OrigeneratorSession:
    layout_plan: WindowLayoutPlan
    command_file: str | Path
    paused_file: str | Path
    status_file: str | Path
    dashboard_cmd_file: str | Path
    funestras: Mapping[str, HandedFunestra]
    frames_file: str | Path | None = None
    input_file: str | Path | None = None


@dataclass(frozen=True)
class ChildProgram:
    python_exe: str | Path
    module: str
    sibling_checkouts: str | None = None


def child_program(m: LaunchManifest, module: str) -> ChildProgram:
    return ChildProgram(m.executables.python_exe, module, m.runtime.sibling_checkouts)


@dataclass(frozen=True)
class GenauStart:
    start_flick: str = ""
    latest: bool = False
    f_mode: bool = False
    orientations: Included = BOTH


def _rect_args(prefix: str, rect) -> list[str]:
    return [word
            for field in ("x", "y", "width", "height")
            for word in (f"--{prefix}{field}", str(getattr(rect, field)))]


def origenerator_session_args(session: OrigeneratorSession) -> list[str]:
    args = ["--fun-time", *_rect_args("", session.layout_plan.random_favs_browser)]
    args.extend(_rect_args("portrait-", session.layout_plan.portrait))
    args.extend(_rect_args("landscape-", session.layout_plan.landscape))
    for satellite, funestra in session.funestras.items():
        args.extend([
            f"--{satellite}-playlist", str(funestra.playlist_file),
            f"--{satellite}-cmd-file", str(funestra.cmd_file),
            f"--{satellite}-status-file", str(funestra.status_file),
            f"--{satellite}-hud-file", str(funestra.hud_file),
        ])
    if session.frames_file is not None and session.input_file is not None:
        args.extend(["--frames-file", str(session.frames_file),
                     "--input-file", str(session.input_file)])
    args.extend(taskbar_identity_args())
    args.extend([
        "--command-file", str(session.command_file),
        "--paused-file", str(session.paused_file),
        "--status-file", str(session.status_file),
        "--dashboard-cmd-file", str(session.dashboard_cmd_file),
    ])
    return args


ORIGENERATOR_MODULE = "origenerator"


def origenerator_launch_command(
        *, python_exe: str | Path, origenerator_dir: str | Path,
        session: OrigeneratorSession) -> list[str]:
    return [NAMER.named_exe(python_exe, "Origenerator"), "-m", ORIGENERATOR_MODULE,
            *the_args_it_declares(origenerator_dir, origenerator_session_args(session))]


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
    session: OrigeneratorSession,
    sibling_checkouts: str | None = None,
) -> int:
    cmd = origenerator_launch_command(
        python_exe=python_exe, origenerator_dir=origenerator_dir, session=session)
    kwargs = origenerator_launch_kwargs(
        origenerator_dir=origenerator_dir, sibling_checkouts=sibling_checkouts)
    proc = subprocess.Popen(cmd, **no_child_log(), **kwargs)
    return proc.pid


def launch_main_funestra(
    m: LaunchManifest,
    *,
    state_dir: str | Path,
    log_file: str | Path,
    rect: WindowRect,
    genau: GenauStart,
) -> int:
    commands = m.commands
    cmd = [
        NAMER.named_exe(m.executables.python_exe, "MainFunestra"),
        "-m",
        m.modules.main_funestra_module,
        "--config",
        str(m.runtime.config_path),
        "--playlist",
        str(commands.main_funestra_playlist_file),
        "--command-file",
        str(commands.main_funestra_cmd_file),
        "--paused-file",
        str(commands.main_funestra_paused_file),
        "--status-file",
        str(commands.main_funestra_status_file),
        "--state-dir",
        str(state_dir),
        "--console-file",
        str(commands.main_funestra_console_file),
        "--drive-file",
        str(commands.genau_drive_file),
        "--dashboard-cmd-file",
        str(commands.dashboard_cmd_file),
        "--x",
        str(rect.x),
        "--y",
        str(rect.y),
        "--width",
        str(rect.width),
        "--height",
        str(rect.height),
        "--icon",
        _the_session_icon(),
        *taskbar_identity_args(),
        "--genau-command-file",
        str(commands.genau_cmd_file),
        "--genau-paused-file",
        str(commands.genau_paused_file),
        "--genau-status-file",
        str(commands.genau_status_file),
    ]
    if m.media.genau_flicks:
        cmd += ["--flicks-dir", str(m.media.genau_flicks)]
    if genau.start_flick:
        cmd += ["--genau-start-flick", genau.start_flick]
    if genau.latest:
        cmd.append("--genau-latest")
    if genau.f_mode:
        cmd.append("--genau-f-mode")
    if genau.orientations != BOTH:
        cmd += ["--genau-orientations", *orientation_words(genau.orientations)]
    if m.runtime.hosts_origenerator:
        cmd += ["--origenerator-command-file", str(commands.origenerator_cmd_file)]
    if commands.main_funestra_notice_file:
        cmd += ["--notice-file", str(commands.main_funestra_notice_file)]
    if commands.osr2_slot_port:
        cmd += ["--osr2-port", commands.osr2_slot_port]
    if m.regen.metadata_root.strip():
        cmd += ["--metadata-dir", m.regen.metadata_root.strip()]
    return _start_a_funestra(cmd, log_file=log_file,
                             sibling_checkouts=m.runtime.sibling_checkouts)


def launch_audio_companion(m: LaunchManifest, *, audio_device: str | None = None) -> subprocess.Popen:
    program = child_program(m, m.modules.audio_module)
    cmd = [
        NAMER.named_exe(str(program.python_exe), "AudioCompanion"),
        "-m",
        program.module,
        "--config",
        str(m.runtime.config_path),
        "--audio-folder",
        str(m.media.genau_audio),
    ]
    if audio_device:
        cmd.extend(["--audio-device", audio_device])
    return subprocess.Popen(cmd, **no_child_log(),
                            **sibling_checkouts_kwargs(program.sibling_checkouts),
                            **hidden_subprocess_kwargs())


def _launch_the_dashboard(
    m: LaunchManifest, plan: WindowLayoutPlan, manifest_path: str | Path,
    log_file: str | Path | None,
) -> int:
    cmd = [
        NAMER.named_exe(m.executables.python_exe, "Dashboard"),
        "-m",
        m.modules.dashboard_module,
        str(manifest_path),
        *_rect_args("", plan.dashboard),
        *_rect_args("rfb-", plan.random_favs_browser),
    ]
    kwargs = {**sibling_checkouts_kwargs(m.runtime.sibling_checkouts),
              **hidden_subprocess_kwargs()}
    if log_file is None:
        return subprocess.Popen(cmd, **no_child_log(), **kwargs).pid
    with open_child_log(log_file, cmd) as log:
        return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, **kwargs).pid


def launch_ui_companions(
    m: LaunchManifest,
    *,
    plan: WindowLayoutPlan,
    manifest_path: str | Path,
    dashboard_log_file: str | Path | None,
    result_file: str | Path,
) -> None:
    dashboard_pid = (_launch_the_dashboard(m, plan, manifest_path, dashboard_log_file)
                     if m.dashboard_enabled else 0)
    audio_proc = launch_audio_companion(m)
    _write_result_file(
        result_file,
        {
            "dashboard_pid": dashboard_pid,
            "audio_pid": audio_proc.pid,
        },
    )


def launch_core_apps(
    m: LaunchManifest,
    portrait: SatelliteLaunch,
    landscape: SatelliteLaunch,
    result_file: str | Path,
) -> None:
    portrait = for_funestra(portrait, FunTimeFunestra.PORTRAIT)
    landscape = for_funestra(landscape, FunTimeFunestra.LANDSCAPE)
    program = child_program(m, m.modules.satellite_module)

    def _launch(launch: SatelliteLaunch, title: str, role: str, *, tiles: bool) -> int:
        return launch_satellite(
            program,
            channels=replace(launch.channels, dashboard_cmd=m.commands.dashboard_cmd_file),
            placement=WindowPlacement(
                x=launch.rect.x, y=launch.rect.y,
                width=launch.rect.width, height=launch.rect.height,
                title=title,
                taskbar_identity=taskbar_identity_args()[1],
                icon=Path(_the_session_icon()),
                tiles=tiles),
            role=role,
            log_file=launch.log_file,
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
    program: ChildProgram,
    *,
    channels: SatelliteChannels,
    placement: WindowPlacement,
    role: str,
    log_file: str | Path,
) -> int:
    cmd = _build_satellite_launch_command(
        NAMER.named_exe(program.python_exe, role), program.module,
        channels=channels, placement=placement)
    return _start_a_funestra(cmd, log_file=log_file, sibling_checkouts=program.sibling_checkouts)

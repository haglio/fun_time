from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path

from app_support import osr2_hold
from funestra.file_channel import append_command
from funestra.main_hud import HOLD_VERB

from .bridge_records import BridgeConfig
from .broker_control import write_broker_command
from .fun_time_funestras import FunTimeFunestra
from .manifest import CommandFiles, LaunchManifest
from .runtime_flow import write_flag_file
from .shared_state import BridgeState, read_shared_state, shared_state_path
from .voice_control import VoiceController
from .windows_bridge_dispatch_loop import DispatchLoopRunner


@dataclass(frozen=True)
class HostedAppFarewell:
    command_file: Path | None = None
    keep: bool = False
    taken_over: bool = False


NO_FAREWELL = HostedAppFarewell()


@dataclass(frozen=True)
class SessionLaunch:
    manifest_path: Path
    manifest: LaunchManifest
    bridge_config: BridgeConfig
    state_dir: Path


def release_of(launch: SessionLaunch, taken_over: bool) -> Path | None:
    return launch.bridge_config.origenerator_cmd_file if taken_over else None


@dataclass(frozen=True)
class HotkeyScriptLaunch:
    ahk_exe: str | Path
    script: str | Path
    project_dir: str | Path


@dataclass(frozen=True)
class HotkeyScript:
    proc: subprocess.Popen
    cmd_file: Path


@dataclass(frozen=True)
class StartupEnd:
    reason: str
    canceled: bool = False


@dataclass(frozen=True)
class LaunchedSoFar:
    pids: list[int]
    rfb_hwnd: int
    release_origenerator_via: Path | None = None


@dataclass(frozen=True)
class RunningSession:
    children: dict
    voice: tuple[VoiceController | None, threading.Thread | None]
    dispatch: tuple[DispatchLoopRunner, threading.Thread]
    loopback_server: ThreadingHTTPServer | None


def silence_the_funestras(manifest: LaunchManifest) -> None:
    commands = manifest.commands
    for paused_file in (
        commands.main_funestra_paused_file, commands.audio_paused_file,
        commands.origenerator_paused_file,
    ):
        if paused_file.strip():
            write_flag_file(paused_file, True)
    for funestra in FunTimeFunestra.SATELLITES:
        write_flag_file(manifest.satellite(funestra).paused_file, True)


def hold_the_osr2(commands: CommandFiles) -> None:
    ending = read_shared_state(shared_state_path(Path(commands.state_dir))) or BridgeState()
    append_command(Path(commands.genau_cmd_file), "PAUSE")
    write_broker_command(commands.broker_cmd_file, HOLD_VERB.get(ending.osr2_control, osr2_hold.PARK))

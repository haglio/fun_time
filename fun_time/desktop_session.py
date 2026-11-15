from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .bridge_records import BridgeConfig
from .manifest import LaunchManifest
from .session_ending import TheControls


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
    return launch.bridge_config.origenerator.cmd_file if taken_over else None


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
    controls: TheControls

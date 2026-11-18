from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

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


class TakenOverOrigenerator(NamedTuple):
    pid: int
    command_file: Path


def taken_over_origenerator(
    launch: SessionLaunch, *, pid: int, taken_over: bool,
) -> TakenOverOrigenerator | None:
    if not taken_over:
        return None
    return TakenOverOrigenerator(pid, launch.bridge_config.origenerator.cmd_file)


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
    taken_over_origenerator: TakenOverOrigenerator | None = None


@dataclass(frozen=True)
class RunningSession:
    children: dict
    controls: TheControls

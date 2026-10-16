from __future__ import annotations

import glob
import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from funestra.file_channel import append_command
from funestra.funestra_verbs import QUIT

from fun_time.config import load_config
from fun_time.modes import write_playlist_file
from fun_time.satellite_control import SatelliteStatus, read_satellite_status
from fun_time.win32_process import get_process_creation_time, is_process_alive
from fun_time.windows_bridge_orchestrator import ChildProcess, kill_recorded_child
from fun_time.windows_bridge_startup import launch_satellite
from satellite.contract import SatelliteChannels, WindowPlacement

from .integration_support import (
    RELEASE_BUDGET_S,
    build_integration_temp_root,
    checkout_project_dirs,
    let_seconds_pass,
    published_ago,
    published_status,
    real_config_path,
    sample_library_clips,
    state_dir_of,
    the_runs_own_pythonw,
    wait_for,
)
from .run_clock import Budget


def library_clips_of(side: str, count: int) -> list[str]:
    folder = getattr(load_config(real_config_path()).paths, f"{side}_dirs")[0]
    return sample_library_clips(
        glob.glob(os.path.join(str(folder), "**", "*.mp4"), recursive=True),
        count, desc=f"{side} clips")


class LoneSatellite:
    def __init__(self, root: Path) -> None:
        self.folder = state_dir_of(root)
        self.sources = self.folder.parent / "portrait"
        for folder in (self.folder, self.sources):
            folder.mkdir(parents=True, exist_ok=True)
        self.playlist = self.folder / "portrait_playlist.tsv"
        self.cmd = self.folder / "portrait_cmd.txt"
        self.paused = self.folder / "portrait_paused.txt"
        self.status = self.folder / "portrait_status.txt"
        self.play_points = self.folder / "portrait_play_points.json"
        self.hud = self.folder / "portrait_hud.json"
        self.dashboard_cmd = self.folder / "dashboard_cmd.txt"
        self.log = self.folder / "portrait_satellite.log"
        self.process = ChildProcess(pid=0, created_at=0)

    @property
    def pid(self) -> int:
        return self.process.pid

    def send(self, verb: str) -> None:
        append_command(self.cmd, verb)

    def video(self) -> str:
        return published_status(read_satellite_status, self.status).video

    def wait_for(self, predicate, *, desc: str):
        return wait_for(predicate, desc=desc, explain=self.what_it_last_said)

    def wait_until_playing(self) -> SatelliteStatus:
        return self.wait_for(
            lambda: (lambda status: status if status.duration_ms > 0 and status.position_ms > 0
                     else None)(read_satellite_status(self.status)),
            desc="the satellite to start playing")

    def wait_for_video(self, *, other_than: str = "") -> str:
        return self.wait_for(
            lambda: (lambda video: video if video and video != other_than else None)(self.video()),
            desc="the satellite's clip to change")

    def what_it_last_said(self) -> str:
        if self.status.exists():
            status = read_satellite_status(self.status)
            said = (f"last published {published_ago(self.status, time.time())}, at "
                    f"{status.position_ms} of {status.duration_ms} ms, "
                    f"{'paused' if status.paused else 'not paused'}, "
                    f"{status.playlist_length} in its list")
        else:
            said = "never published"
        running = "is still running" if _still_running(self.process) else "has ended"
        return f"--- the satellite {said}; its process {running}; its own log is {self.log} ---"

    @contextmanager
    def launched(self, items: Sequence[str | Path], *, width: int, height: int,
                 hud: bool = False) -> Iterator[LoneSatellite]:
        write_playlist_file(self.playlist, [str(item) for item in items])
        pid = launch_satellite(
            python_exe=str(the_runs_own_pythonw()),
            satellite_module="satellite",
            channels=SatelliteChannels(
                playlist=self.playlist,
                command=self.cmd,
                paused=self.paused,
                status=self.status,
                play_points=self.play_points,
                hud=self.hud if hud else None,
                dashboard_cmd=self.dashboard_cmd if hud else None),
            placement=WindowPlacement(x=0, y=0, width=width, height=height,
                                      title="Portrait AI Funestra"),
            role="Portrait",
            log_file=self.log,
            project_dirs=checkout_project_dirs(),
        )
        self.process = identify_child(pid)
        try:
            yield self
        finally:
            self.send(QUIT)
            let_seconds_pass(1.0)
            end_satellite(self.process, self.log)


def a_lone_satellite() -> LoneSatellite:
    return LoneSatellite(build_integration_temp_root())


def identify_child(pid: int) -> ChildProcess:
    return ChildProcess(pid=pid, created_at=get_process_creation_time(pid) or 0)


def _still_running(process: ChildProcess) -> bool:
    return (get_process_creation_time(process.pid) == process.created_at
            and is_process_alive(process.pid))


def end_satellite(satellite: ChildProcess, log: Path, *, budget_s: float = RELEASE_BUDGET_S,
                  sleep=time.sleep) -> None:
    kill_recorded_child(satellite)
    budget = Budget(budget_s)
    while not _nothing_holds(log):
        if budget.expired():
            raise AssertionError(
                f"the satellite (pid {satellite.pid}) still holds {log} "
                f"{budget_s:g}s after its process tree was ended")
        sleep(0.5)


def _nothing_holds(path: Path) -> bool:
    try:
        os.replace(path, path)
    except FileNotFoundError:
        return True
    except PermissionError:
        return False
    return True

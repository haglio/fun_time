from __future__ import annotations

import configparser
import contextlib
import dataclasses
import json
import logging
import os
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest
from app_support import haglio_settings, kino_folder
from app_support.osr2_slot import Osr2Slot
from app_support.siblings import primary_checkout_of
from app_support.state_files import OSR2_SERIAL_RX
from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.threading_utils import start_daemon_thread
from funestra.flick_folder import SUPPORTED_VIDEO_EXTS
from funestra.playlist import PlaylistItem

from fun_time.branch_session import _apply_sibling_checkouts_override, as_a_filename
from fun_time.checkout_overrides import STATE_DIRNAME
from fun_time.config import DEFAULT_CONFIG_PATH, PROJECT_DIR, SIBLING_CHECKOUTS_KEY, load_config
from fun_time.dashboard_app import write_dashboard_command as press_as_the_dashboard_does
from fun_time.event_log import EventRecord, event_log_path, is_announcement, read_events
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.funestra_status import (
    MainFunestraStatus,
    read_main_funestra_status,
)
from fun_time.genau_config import GenauSettings
from fun_time.loopback_server import LOOPBACK_PORT
from fun_time.media_metadata import is_favorite, mark_favorite
from fun_time.modes import (
    build_mirrored_funscript_path,
    matching_funscript,
    plays_in_f_mode,
)
from fun_time.monitors import MonitorRect
from fun_time.process_identity import NAMER
from fun_time.process_sweep import processes_matching
from fun_time.process_tree import kill_process_tree
from fun_time.win32 import find_window_by_title
from fun_time.win32_process import (
    command_line_of,
    get_process_image_name,
    is_process_alive,
)
from fun_time.windows_bridge_orchestrator import ChildProcess, kill_recorded_child
from main_funestra.library import collapse_playlist_versions
from tests.made_up_genau import SETTINGS_NAME, write_settings_naming_genau
from tests.scratch import remove_scratch

from . import run_clock
from .hidden_desktop import (
    HIDDEN_DESKTOP_NAME,
    current_desktop_name,
    pids_with_window_on_current_desktop,
)
from .line_record import KEPT_FOR, forget_what_was_kept_before, this_run
from .run_clock import Budget


def read_recorded_children(state_dir: Path) -> dict[str, ChildProcess]:
    pids_file = state_dir / "bridge_pids.ini"
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(pids_file), encoding="utf-8")
    if "pids" not in parser or "created_at" not in parser:
        return {}
    created_at = parser["created_at"]
    return {
        role: ChildProcess(pid=int(pid), created_at=int(created_at[role]))
        for role, pid in parser["pids"].items()
    }


_LEFT_ALONE_SO_THE_FUNESTRAS_REPLACE_IS_NOT_REFUSED_S = 0.01


def _is_the_empty_record_a_read_inside_the_replace_gets(status) -> bool:
    return status == type(status)()


def published_status(read, path: Path, *, budget_s: float = 2.0,
                     now=run_clock.now, sleep=time.sleep):
    deadline = now() + budget_s
    status = read(path)
    while _is_the_empty_record_a_read_inside_the_replace_gets(status) and now() < deadline:
        sleep(_LEFT_ALONE_SO_THE_FUNESTRAS_REPLACE_IS_NOT_REFUSED_S)
        status = read(path)
    return status

INTEGRATION_CONFIG_NAME = "fun_time_integration_config.json"
MACHINES_SETTINGS_ENV = "FUN_TIME_THE_MACHINES_HAGLIO_SETTINGS"
_MACHINES_OWN_PORTS = frozenset({50555, 50556, 50558, LOOPBACK_PORT})


# The images the apps a session leaves actually run as: the two
# satellites, the Main Funestra/Genau/the audio companion/the dashboard (each under its own
# ``FunTime-*`` copy of pythonw, or under plain pythonw where that copy could not
# be made), and the AHK hotkey shell.  python.exe is deliberately absent — pytest
# and the orchestrator both run as python.exe, and a reap that kills a pytest
# takes down a whole integration run (this one, or one queued after it) with no
# output at all.  The orchestrator needs no killing here: it exits once its AHK
# is gone.  The set is an allow-list on purpose: an image a run never launches is
# never swept, so a third-party app of the user's is never at risk — and the
# ``FunTime-`` prefix widens it only to images this repo creates by name, which
# is a narrower promise than "pythonw" already was.
_APP_IMAGE_NAMES = frozenset({"pythonw.exe", "autohotkey64.exe"})


def _is_leftover_app(pid: int) -> bool:
    image = get_process_image_name(pid)
    if image is None:
        return False
    name = Path(image).name
    return name.lower() in _APP_IMAGE_NAMES or NAMER.owns_exe_name(name)


def _kill_every_app_left_on_the_desktop_all_runs_share() -> None:
    if current_desktop_name() != HIDDEN_DESKTOP_NAME:
        return
    window_pids = pids_with_window_on_current_desktop()
    for pid in window_pids:
        if _is_leftover_app(pid):
            kill_process_tree(pid)
    _kill_leftover_hosted_apps(window_pids)


def _kill_leftover_hosted_apps(window_pids) -> None:
    for pid in sorted(set(window_pids)):
        if re.search(r"-m +origenerator", command_line_of(pid) or "", re.IGNORECASE):
            kill_process_tree(pid)


QUIT_BUDGET_S = 60.0
START_BUDGET_S = 120.0

STARTUP_FINISHED = "Session up; startup hold released"
STARTUP_STOPPED = "Fun Time stopped starting up"

CHILDREN_GONE_TIMEOUT_S = 20.0

COMMAND_BUDGET_S = 120.0

# Windows leaves a monitor that is asleep out of the ones it lists.
SESSION_MONITORS = (MonitorRect(0, 0, 1280, 720), MonitorRect(1280, 0, 720, 1440))


def seen_within(look, *, seconds: float, every_s: float = 0.2):
    budget = Budget(seconds)
    while not (seen := look()) and not budget.expired():
        time.sleep(every_s)
    return seen


def let_seconds_pass(seconds: float) -> None:
    seen_within(lambda: None, seconds=seconds, every_s=0.05)


def wait_for(predicate, *, desc: str, timeout: float = COMMAND_BUDGET_S,
             explain: Callable[[], str] | None = None):
    if last := seen_within(predicate, seconds=timeout):
        return last
    raise AssertionError(f"Timed out waiting for {desc} (last={last!r})"
                         + ("" if explain is None else f"\n{explain()}"))


def await_window(title: str, timeout: float, *, exact: bool = False) -> int:
    return seen_within(lambda: find_window_by_title(title, exact=exact), seconds=timeout)


def ended_within(process: subprocess.Popen, seconds: float) -> bool:
    return seen_within(lambda: process.poll() is not None, seconds=seconds)


def joined_within(thread: threading.Thread, seconds: float) -> bool:
    return seen_within(lambda: not thread.is_alive(), seconds=seconds)


def run_within(argv: list[str], seconds: float, *, check: bool = False,
               capture_output: bool = False, **popen) -> subprocess.CompletedProcess:
    if capture_output:
        popen.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process = subprocess.Popen(argv, **popen)
    budget = Budget(seconds)
    while True:
        try:
            out, err = process.communicate(timeout=min(1.0, budget.left()))
            break
        except subprocess.TimeoutExpired:
            if budget.expired():
                process.kill()
                process.communicate()
                raise subprocess.TimeoutExpired(argv, seconds) from None
    done = subprocess.CompletedProcess(argv, process.returncode, out, err)
    if check:
        done.check_returncode()
    return done


def video_of_a_test_pattern(video: Path, *, size: str, seconds: float, rate: int = 30) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is what makes this test's clips, and it is not on PATH")
    run_within([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}",
                "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
               60.0, check=True, **hidden_subprocess_kwargs())
    return video


def _holding(needle: str, text: str) -> str:
    return text if needle in text else ""


def published_ago(status_file: Path, now: float) -> str:
    try:
        return f"{now - status_file.stat().st_mtime:.0f}s ago"
    except OSError:
        return "never"


def settings_of_the_run_at(config_path: Path) -> Path:
    return config_path.parent / SETTINGS_NAME


@contextlib.contextmanager
def on_the_runs_settings(config_path: Path) -> Iterator[None]:
    with mock.patch.dict(os.environ, {haglio_settings.SETTINGS_ENV: str(settings_of_the_run_at(config_path))}):
        yield


def load_integration_config(config_path: Path):
    with on_the_runs_settings(config_path):
        return load_config(config_path)


class FunTimeIntegrationSession:
    def __init__(self, config_path: Path):
        self.config = load_integration_config(config_path)
        self._proc: subprocess.Popen[str] | None = None
        self._log_pos = 0

    @property
    def windows_bridge_log(self) -> Path:
        return self.config.log_file("windows_bridge")

    @property
    def orchestrator_log(self) -> Path:
        return self.config.log_file("orchestrator")

    @property
    def dashboard_cmd_file(self) -> Path:
        return self.config.paths.state_dir / "dashboard_cmd.txt"

    @property
    def broker_mode_file(self) -> Path:
        return self.config.broker_mode_file


    @property
    def trash_dir(self) -> Path:
        return self.config.paths.trash_dir

    def read_main_funestra_status(self) -> MainFunestraStatus:
        return published_status(read_main_funestra_status, self.config.main_funestra_status_file)

    def read_child_pids(self) -> dict[str, int]:
        return {key: child.pid for key, child in self.read_child_processes().items()}

    def read_child_processes(self) -> dict[str, ChildProcess]:
        return read_recorded_children(self.config.paths.state_dir)

    def quit_gracefully(self, timeout: float = QUIT_BUDGET_S) -> int:
        if not self._proc or self._proc.poll() is not None:
            raise RuntimeError("Orchestrator is not running")
        ahk_cmd = self.config.paths.state_dir / "ahk_cmd.txt"
        budget = Budget(timeout)
        while True:
            ahk_cmd.write_text("exit", encoding="utf-8")
            try:
                exit_code = self._proc.wait(timeout=min(1.0, budget.left()))
                break
            except subprocess.TimeoutExpired:
                if budget.expired():
                    raise AssertionError(
                        f"Orchestrator did not exit within {timeout:g}s after it was told to quit"
                        f"\n{self._log_tail()}"
                    ) from None
        if hasattr(self, "_stderr_fh") and self._stderr_fh:
            self._stderr_fh.close()
        return exit_code

    def start(self, wait_seconds: float = START_BUDGET_S, project_dir: Path | None = None,
              env_overrides: dict[str, str] | None = None) -> None:
        already_logged = self._launch(project_dir=project_dir, env_overrides=env_overrides)
        self._wait_for_own_log(STARTUP_FINISHED, after=already_logged, timeout=wait_seconds)
        let_seconds_pass(1.0)
        self._log_pos = self.windows_bridge_log.stat().st_size if self.windows_bridge_log.exists() else 0

    def start_one_that_stops_starting_up(self, wait_seconds: float = START_BUDGET_S,
                                         env_overrides: dict[str, str] | None = None) -> str:
        already_logged = self._launch(project_dir=None, env_overrides=env_overrides)
        self._wait_for_own_log(STARTUP_STOPPED, after=already_logged, timeout=wait_seconds)
        return self._read_windows_bridge_log()[already_logged:]

    def _launch(self, *, project_dir: Path | None, env_overrides: dict[str, str] | None) -> int:
        self._reap_leftover_runtime_processes()
        env = os.environ.copy()
        env["FUN_TIME_DISABLE_DASHBOARD"] = "1"
        env["FUN_TIME_MUTE_AUDIO"] = "1"
        env["FUN_TIME_RUN_INTEGRATION"] = "1"
        env["FUN_TIME_FAKE_MONITORS"] = ";".join(
            f"{m.x},{m.y},{m.width},{m.height}" for m in SESSION_MONITORS)
        env[haglio_settings.SETTINGS_ENV] = str(settings_of_the_run_at(self.config.config_path))
        env.update(env_overrides or {})
        self._stderr_file = self.config.paths.state_dir / "orchestrator_stderr.log"
        self._stderr_file.parent.mkdir(parents=True, exist_ok=True)
        self._stderr_fh = self._stderr_file.open("w", encoding="utf-8")
        already_logged = len(self._read_windows_bridge_log())
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "fun_time.orchestrator", "--config", str(self.config.config_path)],
            cwd=project_dir or self.config.project_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_fh,
            text=True,
        )
        return already_logged

    def _wait_for_own_log(self, needle: str, *, after: int, timeout: float) -> None:
        if seen_within(lambda: needle in self._read_windows_bridge_log()[after:], seconds=timeout):
            return
        raise AssertionError(
            f"Did not find log line containing {needle!r} from this session\n{self._log_tail()}"
        )

    def stop(self) -> None:
        if os.environ.get("COVERAGE_PROCESS_START") and self._proc and self._proc.poll() is None:
            with contextlib.suppress(AssertionError, RuntimeError):
                self.quit_gracefully()
        self.end_as_a_crash_does()

    def end_as_a_crash_does(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            if not ended_within(self._proc, 5):
                self._proc.kill()
        if hasattr(self, "_stderr_fh") and self._stderr_fh:
            self._stderr_fh.close()
        self._kill_the_children_the_terminated_orchestrator_orphaned()
        self._reap_leftover_runtime_processes()

    def write_dashboard_command(self, action: str) -> None:
        press_as_the_dashboard_does(self.dashboard_cmd_file, action)

    def is_favorite(self, path: Path) -> bool:
        return is_favorite(path, self.config.regen.metadata_root)

    def messages_flashed_from_the_log(self) -> list[EventRecord]:
        records, _offset = read_events(event_log_path(self.config.paths.state_dir))
        return [record for record in records if is_announcement(record)]

    def wait_until(self, predicate, *, timeout: float = COMMAND_BUDGET_S,
                   description: str | Callable[[], str] = "condition") -> None:
        if seen_within(predicate, seconds=timeout):
            return
        described = description() if callable(description) else description
        raise AssertionError(
            f"Timed out waiting for {described}\n{self._log_tail()}"
        )

    def wait_for_log(self, needle: str, timeout: float = COMMAND_BUDGET_S) -> str:
        if text := seen_within(lambda: _holding(needle, self._read_windows_bridge_log()),
                               seconds=timeout):
            return text
        raise AssertionError(
            f"Did not find log line containing {needle!r}\n{self._log_tail()}"
        )

    def wait_for_new_log(self, needle: str, timeout: float = COMMAND_BUDGET_S) -> str:
        if chunk := seen_within(lambda: _holding(needle, self._read_windows_bridge_log_chunk()),
                                seconds=timeout):
            return chunk
        raise AssertionError(
            f"Did not find new log line containing {needle!r}\n{self._log_tail()}"
        )

    def wait_for_any_log(self, needles: list[str], timeout: float = COMMAND_BUDGET_S) -> str:
        def found() -> str:
            text = self._read_windows_bridge_log()
            return next((needle for needle in needles if needle in text), "")

        if needle := seen_within(found, seconds=timeout):
            return needle
        raise AssertionError(
            f"Did not find any log line containing one of {needles!r}\n{self._log_tail()}"
        )

    def _log_tail(self, lines: int = 30) -> str:
        parts: list[str] = [f"--- last published: {self._last_published()} ---"]
        text = self._read_windows_bridge_log()
        if text:
            tail = "\n".join(text.splitlines()[-lines:])
            parts.append(f"--- windows bridge log (last {lines}) ---\n{tail}\n--- end ---")
        else:
            parts.append("[windows bridge log is empty or missing]")
        orch_text = self._read_log_file(self.orchestrator_log)
        if orch_text:
            tail = "\n".join(orch_text.splitlines()[-lines:])
            parts.append(f"--- orchestrator log (last {lines}) ---\n{tail}\n--- end ---")
        stderr_text = self._read_log_file(getattr(self, "_stderr_file", None))
        if stderr_text:
            tail = "\n".join(stderr_text.splitlines()[-lines:])
            parts.append(f"--- stderr (last {lines}) ---\n{tail}\n--- end ---")
        return "\n".join(parts)

    def _last_published(self) -> str:
        now = time.time()
        return "; ".join(
            f"{name} {published_ago(status_file, now)}"
            for name, status_file in (
                ("Main Funestra", self.config.main_funestra_status_file),
                ("portrait", self.config.satellite(FunTimeFunestra.PORTRAIT).status_file),
                ("landscape", self.config.satellite(FunTimeFunestra.LANDSCAPE).status_file)))

    def _read_log_file(self, path: Path | None) -> str:
        if path is None or not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _read_windows_bridge_log(self) -> str:
        if not self.windows_bridge_log.exists():
            return ""
        try:
            return self.windows_bridge_log.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _read_windows_bridge_log_chunk(self) -> str:
        if not self.windows_bridge_log.exists():
            return ""
        try:
            with self.windows_bridge_log.open("r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(self._log_pos)
                chunk = fh.read()
                self._log_pos = fh.tell()
            return chunk
        except OSError:
            return ""

    def _kill_the_children_the_terminated_orchestrator_orphaned(self) -> None:
        try:
            children = self.read_child_processes()
        except (KeyError, OSError, ValueError):
            return
        for child in children.values():
            kill_recorded_child(child)
        self._wait_for_the_children_to_be_gone(children.values())

    def _wait_for_the_children_to_be_gone(
        self, children, timeout_s: float = CHILDREN_GONE_TIMEOUT_S,
    ) -> None:
        budget = Budget(timeout_s)
        for child in children:
            if not child.pid:
                continue
            while not budget.expired() and is_process_alive(child.pid):
                time.sleep(0.2)

    def _reap_leftover_runtime_processes(self) -> None:
        _kill_every_app_left_on_the_desktop_all_runs_share()
        self._wait_for_orchestrators_to_exit()

    def _wait_for_orchestrators_to_exit(self, timeout: float = 15.0) -> None:
        an_integration_orchestrator = rf"fun_time\.orchestrator .*{re.escape(INTEGRATION_CONFIG_NAME)}"
        budget = Budget(timeout)
        while not budget.expired():
            if not processes_matching(r"^pythonw?\.exe$", an_integration_orchestrator):
                return
            time.sleep(0.5)


def _free_port(socket_kind: int) -> int:
    while True:
        with socket.socket(socket.AF_INET, socket_kind) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in _MACHINES_OWN_PORTS:
            return port


def _free_udp_port() -> int:
    return _free_port(socket.SOCK_DGRAM)


def _free_tcp_port() -> int:
    return _free_port(socket.SOCK_STREAM)


A_POWERED_OSR2_REPORTS_EVERY_S = 1.0


class TheRunsOsr2:
    def __init__(self) -> None:
        self._heard: queue.Queue[tuple[float, str, str]] = queue.Queue()
        self._slot = Osr2Slot(port=0, logger=logging.getLogger(__name__),
                              start_thread=start_daemon_thread)
        self._slot.write_through(
            lambda sender, line: self._heard.put((run_clock.now(), sender, line)))
        self._slot.start()
        self.port = self._slot.address[1]
        self._closed = threading.Event()

    def reports_switched_on_to(self, session_state_dir: Path) -> None:
        self._report_switched_on(session_state_dir)
        start_daemon_thread(target=self._keep_reporting_switched_on, args=(session_state_dir,),
                            name="the-runs-osr2-reports")

    def _report_switched_on(self, state_dir: Path) -> None:
        stamp = state_dir / OSR2_SERIAL_RX
        fresh = stamp.with_suffix(".tmp")
        fresh.write_text(str(time.time()), encoding="utf-8")
        os.replace(fresh, stamp)

    def _keep_reporting_switched_on(self, state_dir: Path) -> None:
        while not self._closed.is_set():
            time.sleep(A_POWERED_OSR2_REPORTS_EVERY_S)
            with contextlib.suppress(OSError):
                self._report_switched_on(state_dir)

    def take(self) -> list[tuple[float, str, str]]:
        lines = []
        while True:
            try:
                lines.append(self._heard.get_nowait())
            except queue.Empty:
                return lines

    def close(self) -> None:
        self._closed.set()
        self._slot.close()


_the_runs_osr2s: dict[int, TheRunsOsr2] = {}


def _a_slot_of_the_runs_own() -> int:
    osr2 = TheRunsOsr2()
    while osr2.port in _MACHINES_OWN_PORTS:
        osr2.close()
        osr2 = TheRunsOsr2()
    _the_runs_osr2s[osr2.port] = osr2
    return osr2.port


def the_runs_osr2(port: int) -> TheRunsOsr2:
    return _the_runs_osr2s[port]


def tcode_heard(port: int, *, seconds: float) -> list[tuple[float, str, float]]:
    osr2 = the_runs_osr2(port)
    osr2.take()
    heard: list[tuple[float, str, float]] = []
    budget = Budget(seconds)
    started = run_clock.now()
    while not budget.expired():
        lines = osr2.take()
        if not lines:
            time.sleep(0.02)
        heard += [(round(at - started, 2), sender, int(digits) / (10 ** len(digits) - 1))
                  for at, sender, line in lines for digits in re.findall(r"L0(\d+)", line)]
    return heard


def senders(heard: list[tuple[float, str, float]]) -> dict[str, tuple]:
    return {sender: (len(moves), min(at for at, _h in moves), max(at for at, _h in moves),
                     round(min(h for _at, h in moves), 2), round(max(h for _at, h in moves), 2))
            for sender in {sender for _at, sender, _h in heard}
            for moves in [[(at, h) for at, s, h in heard if s == sender]]}


def close_the_runs_osr2s() -> None:
    while _the_runs_osr2s:
        _port, osr2 = _the_runs_osr2s.popitem()
        osr2.close()


def _leave_his_audio_companion_its_port(config: dict) -> None:
    config["audio_companion"]["port"] = _free_udp_port()


def _give_genau_a_socket_of_the_runs_own(config: dict) -> None:
    genaus_own = {field.name for field in dataclasses.fields(GenauSettings)}
    config["genau"] = {key: value for key, value in config.get("genau", {}).items()
                       if key in genaus_own} | {"udp_port": _free_udp_port()}


def _never_drive_the_osr2_he_is_using(config: dict) -> None:
    config["osr2_slot_port"] = _a_slot_of_the_runs_own()


def _leave_his_userscript_and_tab_pages_their_loopback_port(config: dict) -> None:
    config["loopback_port"] = _free_tcp_port()


def _start_and_stop_no_broker_by_naming_no_launcher(config: dict) -> None:
    config["paths"]["broker_tray_launcher"] = ""


def _never_command_the_broker_he_is_using_through_a_copied_pin(config: dict) -> None:
    config["paths"].pop("broker_state_dir", None)


def the_machines_config_path() -> Path:
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return primary_checkout_of(PROJECT_DIR) / DEFAULT_CONFIG_PATH.name


def load_the_machines_config():
    machines = os.environ.get(MACHINES_SETTINGS_ENV)
    with mock.patch.dict(os.environ, {haglio_settings.SETTINGS_ENV: machines} if machines else {}):
        return load_config(the_machines_config_path())


def strip_what_the_desktop_does_not_isolate(config: dict) -> None:
    _leave_his_audio_companion_its_port(config)
    _give_genau_a_socket_of_the_runs_own(config)
    _never_drive_the_osr2_he_is_using(config)
    _leave_his_userscript_and_tab_pages_their_loopback_port(config)
    _start_and_stop_no_broker_by_naming_no_launcher(config)
    _never_command_the_broker_he_is_using_through_a_copied_pin(config)

    # There is one microphone.  Windows shares an input device between listeners
    # rather than refusing the second, so a run that opened it would not fail —
    # it would listen in on the user and act on what they said to their own
    # session.
    config["voice_control"]["enabled"] = False

    # The hosted Origenerator brings a second app with machine ends of its own:
    # the one ComfyUI server on its fixed port (which it would START if absent),
    # the GPU that server generates on, and the app's one database.  None has a
    # per-desktop or per-directory version, so a run hosts none — with the key
    # gone the session simply has no origenerator mode.
    config["paths"].pop("origenerator_dir", None)
    config["paths"].pop("origenerator_python_exe", None)


def the_runs_own_pythonw() -> Path:
    return Path(sys.executable).with_name("pythonw.exe")


def run_from_this_installation(config: dict) -> None:
    config["paths"]["python_exe"] = str(the_runs_own_pythonw())


def apply_this_checkouts_siblings(config: dict) -> None:
    _apply_sibling_checkouts_override(config, PROJECT_DIR / STATE_DIRNAME)


def this_checkouts_siblings() -> str:
    raw: dict = {}
    apply_this_checkouts_siblings(raw)
    return os.pathsep.join(raw.get("paths", {}).get(SIBLING_CHECKOUTS_KEY, []))


def environment_with_this_checkouts_siblings() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": this_checkouts_siblings()}


def point_the_main_funestra_at(config: dict, videos_dir: Path) -> None:
    config.setdefault("main_player", {})["videos_dir"] = str(videos_dir)


def state_dir_of(root: Path) -> Path:
    return root.resolve() / "integration_runtime" / "state"


def build_integration_config(tmp_path: Path) -> Path:
    real = load_the_machines_config()
    state_dir = state_dir_of(tmp_path)
    integration_root = state_dir.parent
    library = integration_root / "videos"
    kino = library / "videos" / "kino"
    trash_dir = kino_folder.trash_folder_in(kino)
    portrait_dir = kino_folder.upscales_in(kino) / "portrait"
    landscape_dir = kino_folder.upscales_in(kino) / "landscape"
    primary_dir = library / "videos" / "primary"
    metadata_root = library / "metadata"
    for path in (state_dir, trash_dir, portrait_dir, landscape_dir, primary_dir):
        path.mkdir(parents=True, exist_ok=True)

    _link_primary_samples(real, primary_dir, metadata_root=metadata_root)
    portrait_paths = _link_sample_files(real.paths.portrait_dirs, portrait_dir, count=2)
    landscape_paths = _link_sample_files(real.paths.landscape_dirs, landscape_dir, count=2)

    _every_satellite_sample_starts_as_a_favorite([*portrait_paths, *landscape_paths], metadata_root)

    config = json.loads(real.config_path.read_text(encoding="utf-8"))
    config["paths"]["main_player_library_dirs"] = [str(primary_dir)]
    config["paths"]["state_dir"] = str(state_dir)
    config["paths"].pop("genau_dir", None)
    config["random_favs_browser"]["enabled"] = False
    run_from_this_installation(config)
    apply_this_checkouts_siblings(config)

    point_the_main_funestra_at(config, primary_dir)
    config.setdefault("regen", {})["metadata_root"] = str(metadata_root)
    strip_what_the_desktop_does_not_isolate(config)
    the_runs_osr2(config["osr2_slot_port"]).reports_switched_on_to(state_dir)

    config_path = integration_root / INTEGRATION_CONFIG_NAME
    config_path.write_text(json.dumps(config), encoding="utf-8")
    settings_of_the_run_at(config_path).write_text(
        json.dumps({**haglio_settings.load(), "kino_dir": str(kino)}), encoding="utf-8")
    return config_path


RUN_ROOTS: list[Path] = []


def build_integration_temp_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="fun_time_integration_")).resolve()
    RUN_ROOTS.append(root)
    return root


@contextlib.contextmanager
def on_the_settings_this_machine_has() -> Iterator[None]:
    with mock.patch.dict(os.environ):
        os.environ.pop(haglio_settings.SETTINGS_ENV, None)
        yield


@contextlib.contextmanager
def genau_where_this_machine_keeps_it() -> Iterator[None]:
    with on_the_settings_this_machine_has():
        machines = haglio_settings.load()
    settings = write_settings_naming_genau(
        build_integration_temp_root(), Path(machines["genau_dir"]), Path(machines["genau_audio_dir"]),
        kino=Path(machines["kino_dir"]))
    with mock.patch.dict(os.environ, {haglio_settings.SETTINGS_ENV: str(settings), MACHINES_SETTINGS_ENV: str(settings)}):
        yield


def where_failed_runs_are_kept(checkout: Path) -> Path:
    return primary_checkout_of(checkout) / STATE_DIRNAME / "integration_failures"


def keep_what_the_run_logged(keep_in: Path) -> Path | None:
    if not RUN_ROOTS:
        return None
    now = datetime.now().astimezone()
    forget_what_was_kept_before(keep_in.glob("*"), now - KEPT_FOR)
    name = f"{now:%Y%m%d-%H%M%S}"
    if run := this_run():
        name = "_".join([name, run.checkout, as_a_filename(run.branch)])
    kept = keep_in / name
    suffix = 1
    while kept.exists():
        suffix += 1
        kept = keep_in / f"{name}.{suffix}"
    for number, root in enumerate(RUN_ROOTS, 1):
        state = state_dir_of(root)
        if state.is_dir():
            shutil.copytree(state, kept / f"{number:02d}-{root.name}")
    return kept


class RootsLeftBehind(OSError):
    pass


RELEASE_BUDGET_S = 180.0


def clear_run_roots(*, budget_s: float = RELEASE_BUDGET_S, sleep=time.sleep) -> None:
    refused = _refused_until_the_budget_ran_out(
        RUN_ROOTS, budget_s=budget_s, sleep=sleep)
    RUN_ROOTS.clear()
    if refused:
        raise RootsLeftBehind(
            "this run's temp roots are still in the system temp dir:\n"
            + "\n".join(f"{root}: {refusal}" for root, refusal in refused))


def remove_once_windows_releases_it(folder: Path) -> None:
    if not folder.exists():
        return
    for _folder, refusal in _refused_until_the_budget_ran_out(
            [folder], budget_s=RELEASE_BUDGET_S, sleep=time.sleep):
        raise refusal


def _refused_until_the_budget_ran_out(
        roots, *, budget_s: float, sleep) -> list[tuple[Path, OSError]]:
    budget = Budget(budget_s)
    refused = _remove_each(roots)
    while refused and not budget.expired():
        sleep(0.5)
        refused = _remove_each(root for root, _refusal in refused)
    return refused


def _remove_each(roots) -> list[tuple[Path, OSError]]:
    refused = []
    for root in roots:
        try:
            remove_scratch(root)
        except OSError as refusal:
            refused.append((root, refusal))
    return refused


# How long a draw may spend probing what it drew before it gives up.  Every
# skipped video costs a real wait, so a slow library could otherwise outlast
# pytest's own per-test timeout -- and that timeout runs on a thread, which
# cannot interrupt a blocked read.
PROBE_BUDGET_S = 90.0


def library_videos(roots) -> list[Path]:
    return [
        Path(dirpath) / name
        for root in roots
        for dirpath, _dirs, filenames in os.walk(root)
        for name in filenames
        if Path(name).suffix.lower() in SUPPORTED_VIDEO_EXTS
    ]


@dataclasses.dataclass(frozen=True)
class ReadProbe:
    readable: Callable
    budget_s: float = PROBE_BUDGET_S
    now: Callable = run_clock.now


def sample_library_videos(candidates, count: int, *, desc: str, probe: ReadProbe | None = None) -> list:
    candidates = sorted(candidates, key=str)
    assert len(candidates) >= count, (
        f"need {count} {desc}, found {len(candidates)}"
    )
    seed = os.environ.get("FUN_TIME_INTEGRATION_SEED") or str(random.randrange(10 ** 8))
    rng = random.Random(seed)
    print(f"[integration] sample seed {seed} for {count} of {len(candidates)} {desc}")
    if probe is None:
        chosen = rng.sample(candidates, count)
    else:
        chosen = []
        stop_at = probe.now() + probe.budget_s
        for video in rng.sample(candidates, len(candidates)):
            if len(chosen) == count or probe.now() >= stop_at:
                break
            if probe.readable(video):
                chosen.append(video)
            else:
                print("[integration]   skipped one not readable at speed")
        assert len(chosen) == count, (
            f"need {count} {desc} that read at speed, found {len(chosen)} "
            f"within {probe.budget_s:g}s -- the library is cold on this machine"
        )
    return chosen


def one_video_per_video(videos, version_index) -> list[Path]:
    return [item.path for item in collapse_playlist_versions(
        [PlaylistItem(video) for video in videos], version_index)]


def readable_at_speed(path, *, budget_s: float = 2.0, size: int = 16 << 20, read=None) -> bool:
    read = _read_head if read is None else read
    arrived = threading.Event()

    def read_however_long_the_drive_takes() -> None:
        try:
            read(path, size)
        except OSError:
            return
        arrived.set()

    threading.Thread(target=read_however_long_the_drive_takes, daemon=True).start()
    budget = Budget(budget_s)
    while not arrived.wait(min(0.1, budget.left())):
        if budget.expired():
            return False
    return True


def _read_head(path, size: int) -> None:
    with open(path, "rb") as fh:
        fh.read(size)


def _every_satellite_sample_starts_as_a_favorite(samples: list[Path], metadata_root: Path) -> None:
    for path in samples:
        mark_favorite(path.resolve(), metadata_root, True)


def no_longer_the_favorite_every_satellite_sample_starts_as(video: str, metadata_root: Path) -> None:
    mark_favorite(video, metadata_root, False)


def _link_primary_samples(real_config, dest_dir: Path, *, metadata_root: Path,
                          count: int = 5) -> list[Path]:
    machines_metadata_root = real_config.regen.metadata_root
    candidates = [(candidate, source_root)
                  for source_root in real_config.paths.main_funestra_library_dirs
                  for candidate in library_videos([source_root])
                  if plays_in_f_mode(str(candidate), machines_metadata_root)]
    if not candidates:
        raise FileNotFoundError(
            "Could not find a main-library video F-mode plays for integration config "
            "-- a session built without one would come up empty"
        )
    chosen = sample_library_videos(
        candidates, min(count, len(candidates)), desc="funscripted main-library clips")
    targets: list[Path] = []
    seen_names: set[str] = set()
    for candidate, source_root in chosen:
        relative_video = candidate.relative_to(Path(source_root))
        if relative_video.name in seen_names:
            continue
        seen_names.add(relative_video.name)
        target = dest_dir / relative_video
        target.parent.mkdir(parents=True, exist_ok=True)
        _safe_link(candidate, target)
        script = matching_funscript(str(candidate), machines_metadata_root)
        if script:
            copied = Path(build_mirrored_funscript_path(str(target), metadata_root))
            copied.parent.mkdir(parents=True, exist_ok=True)
            _safe_link(Path(script), copied)
        targets.append(target)
    return targets


def _link_sample_files(source_dirs: tuple[Path, ...], dest_dir: Path, *, count: int) -> list[Path]:
    chosen = sample_library_videos(library_videos(source_dirs), count, desc="sample media files")
    for candidate in chosen:
        _safe_link(candidate, dest_dir / candidate.name)
    return [dest_dir / candidate.name for candidate in chosen]


def _safe_link(src: Path, dest: Path) -> None:
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def window_is_quiet(frame_ms, *, budget_ms: float) -> bool:
    return _the_frame_nine_in_ten_come_in_under(frame_ms) < budget_ms


def _the_frame_nine_in_ten_come_in_under(frame_ms) -> float:
    ordered = sorted(frame_ms)
    return ordered[len(ordered) * 9 // 10]


def stall_per_transition(passes) -> float:
    the_worst_frame_of_each_pass = sorted(max(one) for one in passes)
    return the_worst_frame_of_each_pass[len(the_worst_frame_of_each_pass) // 2]

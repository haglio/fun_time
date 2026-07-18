from __future__ import annotations

import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fun_time.config import load_config
from fun_time.dashboard_runtime import NauStatus, read_nau_status
from fun_time.modes import build_mirrored_funscript_path, has_matching_funscript
from fun_time.media_actions import ensure_favs_csv_exists, ensure_in_favs
from fun_time.win32 import get_process_image_name
from fun_time.windows_bridge_orchestrator import (
    ChildProcess,
    kill_process_tree,
    kill_recorded_child,
)

from .hidden_desktop import (
    HIDDEN_DESKTOP_NAME,
    current_desktop_name,
    pids_with_window_on_current_desktop,
)
from .live_session_guard import read_recorded_children


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv")

# Every integration config is written under this name, in a temp tree of its own.
# It is also what tells an integration orchestrator apart from the user's on a
# command line, so the two uses share the constant rather than the spelling.
INTEGRATION_CONFIG_NAME = "fun_time_integration_config.json"


# The images the apps a session leaves behind actually run as: the two
# satellites, Nau/Genau/the audio companion/the dashboard (all pythonw), and the
# AHK hotkey shell.  python.exe is deliberately absent — pytest and the
# orchestrator both run as python.exe, and a reap that kills a pytest takes down
# a whole integration run (this one, or one queued behind it) with no output at
# all.  The orchestrator needs no killing here: it exits once its AHK is gone.
# The set is an allow-list on purpose: an image a run never launches is never
# swept, so a third-party app of the user's is never at risk.
_APP_IMAGE_NAMES = frozenset({"pythonw.exe", "autohotkey64.exe"})


def _is_leftover_app(pid: int) -> bool:
    image = get_process_image_name(pid)
    return image is not None and Path(image).name.lower() in _APP_IMAGE_NAMES


def _kill_leftover_app_processes() -> None:
    """Kill leftover players / AHK / pythonw from a prior session so the next
    one starts clean.

    Bounded to the hidden integration desktop's own windows.  Nothing on that
    desktop belongs to the user's real (input-desktop) session, which is what
    makes a run safe to fire unattended.  They are not all *ours*, though — the
    desktop is shared with any leftover session and with the pytest of a run
    queued behind this one — so kill only the app images, never a python.exe.

    Anywhere else there is nothing of ours to find and nothing safe to kill, so
    this does nothing at all.  It used to fall back to
    ``Get-Process pythonw,autohotkey64 | where StartTime > -5min | Stop-Process``
    — every player, companion and AHK bridge of any session started in the last
    five minutes, the user's included.  That existed to serve bare
    ``pytest tests/integration/``, the one invocation the suite forbids and now
    refuses outright.
    """
    if current_desktop_name() != HIDDEN_DESKTOP_NAME:
        return
    for pid in pids_with_window_on_current_desktop():
        if _is_leftover_app(pid):
            kill_process_tree(pid)



class FunTimeIntegrationSession:
    def __init__(self, config_path: Path):
        self.config = load_config(config_path)
        self._proc: subprocess.Popen[str] | None = None
        self._started_at = time.time()
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
    def genau_mode_file(self) -> Path:
        return self.config.genau_mode_file

    @property
    def favs_file(self) -> Path:
        return self.config.paths.favs_file

    @property
    def weird_dir(self) -> Path:
        return self.config.paths.weird_dir

    def read_genau_pid(self) -> int:
        """Read the Genau PID from the bridge pids file."""
        return self.read_child_pids()["genau_pid"]

    def read_nau_status(self) -> NauStatus:
        """Parse Nau's published status file."""
        return read_nau_status(self.config.nau_status_file)

    def read_nau_duration_ms(self) -> int:
        """Nau's current video duration in ms (published, but not carried on
        NauStatus, which only parses fields with production consumers).  A
        non-zero value means mpv has loaded the file and knows its length."""
        path = self.config.nau_status_file
        if not path.exists():
            return 0
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("duration_ms="):
                try:
                    return int(line.split("=", 1)[1].strip() or 0)
                except ValueError:
                    return 0
        return 0

    def read_child_pids(self) -> dict[str, int]:
        """Read all child PIDs from bridge_pids.ini."""
        return {key: child.pid for key, child in self.read_child_processes().items()}

    def read_child_processes(self) -> dict[str, ChildProcess]:
        """Read the children the orchestrator recorded — PID and creation time."""
        return read_recorded_children(self.config.paths.state_dir)

    def quit_gracefully(self, timeout: float = 15.0) -> int:
        """Simulate the Ctrl+Alt+Q quit path by killing the AHK process.

        Killing AHK is functionally identical to AHK's ExitApp() — both
        cause ahk_proc.wait() to return, triggering the orchestrator's
        finally block which calls _shutdown_children().

        Returns the orchestrator process exit code.
        """
        if not self._proc or self._proc.poll() is not None:
            raise RuntimeError("Orchestrator is not running")
        ahk_cmd = self.config.paths.state_dir / "ahk_cmd.txt"
        ahk_cmd.write_text("exit", encoding="utf-8")
        try:
            exit_code = self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"Orchestrator did not exit within {timeout}s after AHK was killed\n{self._log_tail()}"
            )
        if hasattr(self, "_stderr_fh") and self._stderr_fh:
            self._stderr_fh.close()
        return exit_code

    def start(self, wait_seconds: float = 45.0) -> None:
        self._reap_leftover_runtime_processes()
        env = os.environ.copy()
        env["FUN_TIME_DISABLE_DASHBOARD"] = "1"
        env["FUN_TIME_MUTE_AUDIO"] = "1"
        env["FUN_TIME_RUN_INTEGRATION"] = "1"
        self._stderr_file = self.config.paths.state_dir / "orchestrator_stderr.log"
        self._stderr_file.parent.mkdir(parents=True, exist_ok=True)
        self._stderr_fh = self._stderr_file.open("w", encoding="utf-8")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "fun_time.orchestrator", "--config", str(self.config.config_path)],
            cwd=self.config.project_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_fh,
            text=True,
        )
        self.wait_for_log("Hotkey script started", timeout=wait_seconds)
        time.sleep(1.0)
        self._log_pos = self.windows_bridge_log.stat().st_size if self.windows_bridge_log.exists() else 0

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if hasattr(self, "_stderr_fh") and self._stderr_fh:
            self._stderr_fh.close()
        # Deterministically kill the children by their recorded PIDs first —
        # hard-terminating the orchestrator above skips its graceful
        # _shutdown_children(), so the satellites would otherwise survive
        # until the racy name+StartTime sweep happens to catch them.
        self._kill_recorded_children()
        self._reap_leftover_runtime_processes()

    def write_dashboard_command(self, action: str) -> None:
        self.dashboard_cmd_file.parent.mkdir(parents=True, exist_ok=True)
        self.dashboard_cmd_file.write_text(action, encoding="utf-8")

    def favs_contains(self, path: Path) -> bool:
        if not self.favs_file.exists():
            return False
        text = self.favs_file.read_text(encoding="utf-8", errors="ignore")
        return str(path.resolve()) in text

    def wait_until(self, predicate, *, timeout: float = 10.0, description: str = "condition") -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.2)
        raise AssertionError(
            f"Timed out waiting for {description}\n{self._log_tail()}"
        )

    def wait_for_log(self, needle: str, timeout: float = 10.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self._read_windows_bridge_log()
            if needle in text:
                return text
            time.sleep(0.2)
        raise AssertionError(
            f"Did not find log line containing {needle!r}\n{self._log_tail()}"
        )

    def wait_for_new_log(self, needle: str, timeout: float = 10.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self._read_windows_bridge_log_chunk()
            if needle in chunk:
                return chunk
            time.sleep(0.2)
        raise AssertionError(
            f"Did not find new log line containing {needle!r}\n{self._log_tail()}"
        )

    def wait_for_any_log(self, needles: list[str], timeout: float = 10.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self._read_windows_bridge_log()
            for needle in needles:
                if needle in text:
                    return needle
            time.sleep(0.2)
        raise AssertionError(
            f"Did not find any log line containing one of {needles!r}\n{self._log_tail()}"
        )

    def _log_tail(self, lines: int = 30) -> str:
        parts: list[str] = []
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

    def _kill_recorded_children(self) -> None:
        """Deterministically kill this session's children by their recorded identity.

        stop() hard-terminates the orchestrator with TerminateProcess, so the
        orchestrator's own graceful _shutdown_children() never runs and the
        processes it launched (the two satellites, plus Nau/Genau/dashboard/
        audio) are orphaned.  Kill them via the production kill_recorded_child,
        which taskkills a recorded PID only while its creation time still names
        the process the orchestrator launched — a child that has already died
        and had its PID handed to some other process (this run's pytest, or a
        queued run's) is left alone.

        bridge_pids.ini is absent only when startup failed before writing it —
        then there is nothing of ours to kill.
        """
        try:
            children = self.read_child_processes()
        except (KeyError, OSError, ValueError):
            return
        for child in children.values():
            kill_recorded_child(child)

    def _reap_leftover_runtime_processes(self) -> None:
        _kill_leftover_app_processes()
        # Wait for AHK to fully exit — #SingleInstance Force in the next
        # AHK launch races with zombie processes that a force-kill has
        # signalled but the OS hasn't fully reaped yet.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq AutoHotkey64.exe", "/NH"],
                capture_output=True, text=True, check=False,
            )
            if "AutoHotkey64.exe" not in result.stdout:
                break
            time.sleep(0.3)
        self._wait_for_orchestrators_to_exit()

    def _wait_for_orchestrators_to_exit(self, timeout: float = 15.0) -> None:
        """Block until no *integration* fun_time.orchestrator processes remain.

        Killing a session's AHK wakes its orchestrator, whose shutdown then
        taskkills the PIDs it recorded at startup.  Windows recycles PIDs
        aggressively, so if a NEW session launches during that window the
        dying orchestrator can kill the new session's freshly-spawned
        processes.  Serialize the handoff: let the old orchestrator finish
        its shutdown storm before anything new starts.

        Bounded to orchestrators started from an integration config.  Matching
        every orchestrator on the machine swept in the user's live session, which
        is never going to exit for us — so both ends of every session burned the
        whole timeout, and a run's own teardown waited on a session it has
        nothing to do with.
        """
        config_pattern = INTEGRATION_CONFIG_NAME.replace(".", "\\.")
        ps = (
            "@(Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -match '^pythonw?\\.exe$' -and "
            "$_.CommandLine -match 'fun_time\\.orchestrator' -and "
            f"$_.CommandLine -match '{config_pattern}' }}).Count"
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, check=False,
            )
            if result.stdout.strip() == "0":
                return
            time.sleep(0.5)



def isolate_audio_companion_port(config: dict, genau_config: dict) -> None:
    """Move this run's audio companion off the port the user's session uses.

    The companion ``bind``s a fixed UDP port from config, and Genau notifies it
    on the matching ``notify_port``.  Both sides of that pair are rewritten here,
    to a port the OS says is free: rewriting one alone would only leave the run's
    own Genau shouting at nobody.

    Two sessions on the production port cannot both have it.  The loser dies with
    WSAEADDRINUSE, and if the run got there first the loser is the user's — they
    open Fun Time and it comes up with no companion audio and nothing to say why.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config["audio_companion"]["port"] = port
    genau_config["genau"]["notify_port"] = port


def build_integration_config(tmp_path: Path) -> Path:
    real = load_config()
    integration_root = tmp_path.resolve() / "integration_runtime"
    state_dir = integration_root / "state"
    weird_dir = integration_root / "weird"
    portrait_dir = integration_root / "portrait"
    landscape_dir = integration_root / "landscape"
    primary_dir = integration_root / "videos" / "videos" / "primary"
    for path in (state_dir, weird_dir, portrait_dir, landscape_dir, primary_dir):
        path.mkdir(parents=True, exist_ok=True)

    primary_paths = _link_primary_samples(real, primary_dir)
    portrait_paths = _link_sample_files(real.paths.portrait_dirs, portrait_dir, count=2)
    landscape_paths = _link_sample_files(real.paths.landscape_dirs, landscape_dir, count=2)

    favs_file = integration_root / "favs.csv"
    ensure_favs_csv_exists(favs_file)
    for path in [*portrait_paths, *landscape_paths]:
        ensure_in_favs(favs_file, str(path.resolve()))

    config = json.loads(real.config_path.read_text(encoding="utf-8"))
    config["paths"]["nau_library_dirs"] = [str(primary_dir)]
    config["paths"]["portrait_dirs"] = [str(portrait_dir)]
    config["paths"]["landscape_dirs"] = [str(landscape_dir)]
    config["paths"]["weird_dir"] = str(weird_dir)
    config["paths"]["favs_file"] = str(favs_file)
    config["paths"]["state_dir"] = str(state_dir)
    config["random_favs_browser"]["enabled"] = False

    # Nau builds its version-index / length-mode source from nau.videos_dir, so
    # point the genau config's Nau dirs at the copied test library — otherwise it
    # would scan the real one. Mirrors the videos->scripts layout that
    # _link_primary_samples writes the funscripts into.
    scripts_root = Path(str(primary_dir).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\"))
    nau_clips_dir = integration_root / "nau_clips"
    nau_clips_dir.mkdir(parents=True, exist_ok=True)
    genau_config = json.loads(Path(config["paths"]["genau_config_path"]).read_text(encoding="utf-8"))
    genau_config.setdefault("nau", {})
    genau_config["nau"]["videos_dir"] = str(primary_dir)
    genau_config["nau"]["scripts_dir"] = str(scripts_root)
    genau_config["nau"]["clips_dir"] = str(nau_clips_dir)
    # Paths are not the whole of what a session claims: the audio companion binds
    # a fixed UDP port, so a run and a live session would race for one socket.
    isolate_audio_companion_port(config, genau_config)
    test_genau_config = integration_root / "genau_integration_config.json"
    test_genau_config.write_text(json.dumps(genau_config), encoding="utf-8")
    config["paths"]["genau_config_path"] = str(test_genau_config)

    config_path = integration_root / INTEGRATION_CONFIG_NAME
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def build_integration_temp_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="fun_time_integration_")).resolve()


def _link_primary_samples(real_config, dest_dir: Path, *, count: int = 5) -> list[Path]:
    candidates: list[tuple[Path, Path]] = []  # (candidate, source_root)
    for source_root in real_config.paths.nau_library_dirs:
        for candidate in source_root.rglob("*"):
            if candidate.suffix.lower() not in VIDEO_EXTENSIONS or not candidate.is_file():
                continue
            if has_matching_funscript(str(candidate)):
                candidates.append((candidate, source_root))
    if not candidates:
        raise FileNotFoundError("Could not find a primary video with a matching funscript for integration config")
    chosen = random.sample(candidates, min(count, len(candidates)))
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
        mirrored = Path(build_mirrored_funscript_path(str(candidate)))
        if mirrored.exists():
            temp_mirrored_root = Path(str(dest_dir).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\"))
            mirrored_dest = (temp_mirrored_root / relative_video).with_suffix(".funscript")
            mirrored_dest.parent.mkdir(parents=True, exist_ok=True)
            _safe_link(mirrored, mirrored_dest)
        targets.append(target)
    return targets


def _link_sample_files(source_dirs: tuple[Path, ...], dest_dir: Path, *, count: int) -> list[Path]:
    candidates: list[Path] = []
    for source_dir in source_dirs:
        for candidate in source_dir.rglob("*"):
            if candidate.suffix.lower() not in VIDEO_EXTENSIONS or not candidate.is_file():
                continue
            candidates.append(candidate)
    if len(candidates) < count:
        raise FileNotFoundError(f"Could not find {count} sample media files in {source_dirs}")
    chosen = random.sample(candidates, count)
    selected: list[Path] = []
    for candidate in chosen:
        target = dest_dir / candidate.name
        _safe_link(candidate, target)
        selected.append(target)
    return selected


def _safe_link(src: Path, dest: Path) -> None:
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


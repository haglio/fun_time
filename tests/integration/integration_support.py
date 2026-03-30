from __future__ import annotations

import configparser
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fun_time.config import load_config
from fun_time.modes import build_mirrored_funscript_path, has_matching_funscript
from fun_time.media_actions import ensure_favs_csv_exists, ensure_in_favs
from fun_time.orchestrator import vlc_http_password_from_vlcrc
from fun_time.vlc_actions import restore_vlc_volume


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv")



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
    def robot_hand_mode_file(self) -> Path:
        return self.config.robot_hand_mode_file

    @property
    def favs_file(self) -> Path:
        return self.config.paths.favs_file

    @property
    def weird_dir(self) -> Path:
        return self.config.paths.weird_dir

    @property
    def robot_hand_enabled_file(self) -> Path:
        return self.config.robot_hand_enabled_file

    def read_robot_hand_pid(self) -> int:
        """Read the Robot Hand PID from the bridge pids file."""
        return self.read_child_pids()["robot_hand_pid"]

    def read_child_pids(self) -> dict[str, int]:
        """Read all child PIDs from bridge_pids.ini."""
        pids_file = self.config.paths.state_dir / "bridge_pids.ini"
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(str(pids_file), encoding="utf-8")
        return {key: int(val) for key, val in parser["pids"].items()}

    def quit_gracefully(self, timeout: float = 15.0) -> int:
        """Simulate the Ctrl+Alt+Q quit path by killing the AHK process.

        Killing AHK is functionally identical to AHK's ExitApp() — both
        cause ahk_proc.wait() to return, triggering the orchestrator's
        finally block which calls _shutdown_children().

        Returns the orchestrator process exit code.
        """
        if not self._proc or self._proc.poll() is not None:
            raise RuntimeError("Orchestrator is not running")
        self._restore_vlc_volumes()
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
        self._kill_recent_runtime_processes()
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
        self._restore_vlc_volumes()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if hasattr(self, "_stderr_fh") and self._stderr_fh:
            self._stderr_fh.close()
        self._kill_recent_runtime_processes()

    def _restore_vlc_volumes(self) -> None:
        """Restore VLC volume on all ports so VLC doesn't persist muted state."""
        password = vlc_http_password_from_vlcrc() or ""
        if not password:
            return
        for port in (
            self.config.controller.primary_vlc_http_port,
            self.config.controller.vlc2_http_port,
            self.config.controller.vlc3_http_port,
        ):
            restore_vlc_volume(port, password)

    def write_dashboard_command(self, action: str) -> None:
        self.dashboard_cmd_file.parent.mkdir(parents=True, exist_ok=True)
        self.dashboard_cmd_file.write_text(action, encoding="utf-8")

    def write_robot_hand_mode(self, enabled: bool) -> None:
        self.robot_hand_mode_file.parent.mkdir(parents=True, exist_ok=True)
        self.robot_hand_mode_file.write_text("1" if enabled else "0", encoding="utf-8")

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

    def _kill_recent_runtime_processes(self) -> None:
        ps = (
            "Get-Process AutoHotkey64,pythonw,vlc,MultiFunPlayer -ErrorAction SilentlyContinue | "
            "Where-Object { $_.StartTime -gt (Get-Date).AddMinutes(-5) } | "
            "Stop-Process -Force -ErrorAction SilentlyContinue"
        )
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], check=False)


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
    config["paths"]["primary_vlc_dirs"] = [str(primary_dir)]
    config["paths"]["portrait_dirs"] = [str(portrait_dir)]
    config["paths"]["landscape_dirs"] = [str(landscape_dir)]
    config["paths"]["weird_dir"] = str(weird_dir)
    config["paths"]["favs_file"] = str(favs_file)
    config["paths"]["state_dir"] = str(state_dir)
    config["random_favs_browser"]["enabled"] = False

    config_path = integration_root / "fun_time_integration_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def build_integration_temp_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="fun_time_integration_")).resolve()


def _link_primary_samples(real_config, dest_dir: Path) -> list[Path]:
    candidates: list[tuple[Path, Path]] = []  # (candidate, source_root)
    for source_root in real_config.paths.primary_vlc_dirs:
        for candidate in source_root.rglob("*"):
            if candidate.suffix.lower() not in VIDEO_EXTENSIONS or not candidate.is_file():
                continue
            if has_matching_funscript(str(candidate), str(source_root)):
                candidates.append((candidate, source_root))
    if not candidates:
        raise FileNotFoundError("Could not find a primary video with a matching funscript for integration config")
    candidate, source_root = random.choice(candidates)
    relative_video = candidate.relative_to(Path(source_root))
    target = dest_dir / relative_video
    target.parent.mkdir(parents=True, exist_ok=True)
    _safe_link(candidate, target)
    mirrored = Path(build_mirrored_funscript_path(str(candidate), str(source_root)))
    if mirrored.exists():
        temp_mirrored_root = Path(str(dest_dir).replace("\\videos\\videos\\", "\\videos\\scripts\\scripts\\"))
        mirrored_dest = (temp_mirrored_root / relative_video).with_suffix(".funscript")
        mirrored_dest.parent.mkdir(parents=True, exist_ok=True)
        _safe_link(mirrored, mirrored_dest)
    return [target]


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


from __future__ import annotations

import configparser
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

from fun_time.branch_session import STATE_DIRNAME, _apply_genau_checkout_override
from fun_time.config import DEFAULT_CONFIG_PATH, PROJECT_DIR, load_config
from fun_time.dashboard_runtime import NauStatus, read_nau_status
from fun_time.event_log import EventRecord, event_log_path, read_events
from fun_time.modes import build_mirrored_funscript_path, has_matching_funscript
from fun_time.media_actions import ensure_favs_csv_exists, ensure_in_favs
from fun_time.notice_overlay import is_announcement
from fun_time.process_identity import is_fun_time_exe_name
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


def read_recorded_children(state_dir: Path) -> dict[str, ChildProcess]:
    """The children the orchestrator recorded at startup, by role.

    Identity is the ``(pid, created_at)`` pair, never the PID alone: Windows
    hands freed PIDs straight back out, so a teardown that killed by PID would
    eventually shoot whatever inherited a dead child's number.

    Empty when no session has ever written ``bridge_pids.ini`` — or when startup
    failed before it got that far.
    """
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


VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv")

# Every integration config is written under this name, in a temp tree of its own.
# It is also what tells an integration orchestrator apart from the user's on a
# command line, so the two uses share the constant rather than the spelling.
INTEGRATION_CONFIG_NAME = "fun_time_integration_config.json"


# The images the apps a session leaves behind actually run as: the two
# satellites, Nau/Genau/the audio companion/the dashboard (each under its own
# ``FunTime-*`` copy of pythonw, or under plain pythonw where that copy could not
# be made), and the AHK hotkey shell.  python.exe is deliberately absent — pytest
# and the orchestrator both run as python.exe, and a reap that kills a pytest
# takes down a whole integration run (this one, or one queued behind it) with no
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
    return name.lower() in _APP_IMAGE_NAMES or is_fun_time_exe_name(name)


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
    window_pids = pids_with_window_on_current_desktop()
    for pid in window_pids:
        if _is_leftover_app(pid):
            kill_process_tree(pid)
    _kill_leftover_hosted_apps(window_pids)


def _kill_leftover_hosted_apps(window_pids) -> None:
    """Reap a leftover hosted Origenerator, which the image-name pass spares.

    It runs on its own interpreter — a plain ``python.exe`` — exactly the image
    the pass above deliberately never kills, because the pytest of a queued run
    is python.exe too.  The command line is what tells them apart: only the
    hosted app was launched ``-m origenerator``, and a leftover one owns real
    windows on this desktop that can sit over a later session's players (a
    hung boot's splash covered a satellite for a whole test run).  One WMI
    query answers for all candidate pids at once.
    """
    candidates = sorted(set(window_pids))
    if not candidates:
        return
    pid_list = ",".join(str(pid) for pid in candidates)
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { "
        f"@({pid_list}) -contains $_.ProcessId -and "
        "$_.CommandLine -match '-m +origenerator' } | "
        "ForEach-Object { $_.ProcessId }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, check=False,
    )
    for line in result.stdout.split():
        try:
            kill_process_tree(int(line))
        except ValueError:
            continue



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

        The command is re-sent until the process goes.  ``ahk_cmd.txt`` is a
        one-slot mailbox that AHK reads-and-deletes on a 150ms timer, and the
        dispatch loop writes to it too — an OmniPause enter puts
        ``suspend_hotkeys`` there — so a lone write can be overwritten before
        AHK ever reads it, and an exit lost that way never arrives.  That is
        what made ``test_fun_time_reopens_on_the_video_it_was_closed_on``
        (which quits from inside OmniPause) fail about one run in ten.  Nothing
        in production races here: the dispatch loop is the mailbox's only
        writer and returns the moment it has written "exit".

        Returns the orchestrator process exit code.
        """
        if not self._proc or self._proc.poll() is not None:
            raise RuntimeError("Orchestrator is not running")
        ahk_cmd = self.config.paths.state_dir / "ahk_cmd.txt"
        deadline = time.monotonic() + timeout
        while True:
            ahk_cmd.write_text("exit", encoding="utf-8")
            try:
                exit_code = self._proc.wait(timeout=min(1.0, max(deadline - time.monotonic(), 0.0)))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        f"Orchestrator did not exit within {timeout}s after AHK was told to quit"
                        f"\n{self._log_tail()}"
                    )
        if hasattr(self, "_stderr_fh") and self._stderr_fh:
            self._stderr_fh.close()
        return exit_code

    def start(self, wait_seconds: float = 45.0, project_dir: Path | None = None,
              env_overrides: dict[str, str] | None = None) -> None:
        """Launch the orchestrator and wait for it to report the bridge up.

        *project_dir* is the working directory the orchestrator runs in, which
        is also the checkout it resolves ``fun_time`` and ``satellite`` from —
        ``fun_time`` is not installed into the venv, so the working directory is
        what chooses the code.  It defaults to this checkout; a branch-session
        test passes the worktree, which is the whole mechanism under test.

        *env_overrides* land on top of the integration defaults — how the
        loading-screen test forces the production overlay path
        (``FUN_TIME_INTEGRATION_OVERLAYS=1``) that integration mode otherwise
        skips.
        """
        self._reap_leftover_runtime_processes()
        env = os.environ.copy()
        env["FUN_TIME_DISABLE_DASHBOARD"] = "1"
        env["FUN_TIME_MUTE_AUDIO"] = "1"
        env["FUN_TIME_RUN_INTEGRATION"] = "1"
        env.update(env_overrides or {})
        self._stderr_file = self.config.paths.state_dir / "orchestrator_stderr.log"
        self._stderr_file.parent.mkdir(parents=True, exist_ok=True)
        self._stderr_fh = self._stderr_file.open("w", encoding="utf-8")
        # The windows-bridge log outlives the session that wrote it, so a
        # whole-file search would answer with a PREVIOUS session's "Hotkey script
        # started" and hand back a session that has not launched anything yet.
        # Only what is appended from here on can satisfy the wait.
        already_logged = len(self._read_windows_bridge_log())
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "fun_time.orchestrator", "--config", str(self.config.config_path)],
            cwd=project_dir or self.config.project_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_fh,
            text=True,
        )
        self._wait_for_own_log("Hotkey script started", after=already_logged, timeout=wait_seconds)
        time.sleep(1.0)
        self._log_pos = self.windows_bridge_log.stat().st_size if self.windows_bridge_log.exists() else 0

    def _wait_for_own_log(self, needle: str, *, after: int, timeout: float) -> None:
        """Wait for *needle* among the log characters written past *after*."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in self._read_windows_bridge_log()[after:]:
                return
            time.sleep(0.2)
        raise AssertionError(
            f"Did not find log line containing {needle!r} from this session\n{self._log_tail()}"
        )

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

    def notices(self) -> list[EventRecord]:
        """Every announcement in this session's event log so far.

        The overlay flashes exactly what clears :func:`is_announcement`, so a
        record here with the right source is a toast over the right player —
        assertable without a QApplication.
        """
        records, _offset = read_events(event_log_path(self.config.paths.state_dir))
        return [record for record in records if is_announcement(record)]

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



# Ports this process binds on the run's behalf, so the run's own T-Code has
# somewhere to land.  Held for as long as any session might still be sending.
_udp_sinks: list[socket.socket] = []


def _free_udp_port() -> int:
    """A loopback port the OS says is free, for something else to bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _free_tcp_port() -> int:
    """A loopback TCP port the OS says is free, for the run's own server to bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _sink_udp_port() -> int:
    """A loopback port bound here for the rest of the run, and never read.

    Moving a stream off a shared port is only half of isolating it: it has to
    arrive somewhere.  ``UdpTCodeSink.send`` is a bare ``sendto`` with no
    handler, and a datagram sent at a port nothing has bound draws an ICMP
    port-unreachable that Windows reports back to the sender as WSAECONNRESET on
    a later call — so an unbound port would kill Genau's T-Code thread with an
    error the isolation itself invented.  A bound socket absorbs the stream the
    way the broker does; nothing reads it, so the datagrams fill the receive
    buffer and are dropped.
    """
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    _udp_sinks.append(sink)
    return sink.getsockname()[1]


def close_udp_sinks() -> None:
    """Release every sink port bound for this run."""
    while _udp_sinks:
        _udp_sinks.pop().close()


def _isolate_shared_udp_ports(config: dict, genau_config: dict) -> None:
    """Move every UDP endpoint this run would otherwise share with the machine.

    Three fixed ports are machine-global, and a socket is not per-desktop — so
    the hidden desktop does nothing for any of them:

    * The **audio companion** ``bind``s its port, and Genau notifies it on the
      matching ``notify_port``.  Two sessions cannot both have it: the loser dies
      with WSAEADDRINUSE, and if the run got there first the loser is the user's
      — they open Fun Time and it comes up with no companion audio and nothing to
      say why.  Both sides of the pair move together, or the run's own Genau is
      left shouting at nobody.
    * **Genau's inbound port** is bound with SO_REUSEADDR, which on Windows lets
      a second Genau bind it rather than refusing — so two Genaus split one
      datagram stream between them at random, and a run's Genau swallows packets
      meant for the user's.
    * The **broker's T-Code inlet** is the one output that reaches hardware.  The
      broker holds the OSR2's serial port, so a run that keeps the production
      inlet drives the user's device while they are using it.  Nau and Genau move
      to one sink together, so a run's stream stays watchable where it lands.
    """
    companion_port = _free_udp_port()
    config["audio_companion"]["port"] = companion_port
    genau_config["genau"]["notify_port"] = companion_port

    genau_config["genau"]["udp_port"] = _free_udp_port()

    tcode_port = _sink_udp_port()
    genau_config["genau"]["tcode_udp_port"] = tcode_port
    genau_config["nau"]["tcode_udp_port"] = tcode_port
    # The VR main player streams to the same broker inlet through fun_time's own
    # config (``vr.tcode_udp_port``), so it moves onto the run's sink with
    # them — set even when the section is absent, so a config written before
    # FunTimeVR existed still cannot fall back to the production default.
    config.setdefault("vr", {})["tcode_udp_port"] = tcode_port


def real_config_path() -> Path:
    """The machine's real runtime config, found from a worktree as well.

    ``fun_time_config.json`` is a private overlay now — git-ignored, with only
    the sanitized example committed — so it is checked out in the primary
    checkout and nowhere else.  Worktrees made before it stopped being tracked
    still carry a copy; ones made since have none, and every integration run in
    them died at setup on a missing file.

    A run needs the real one (it links its sample clips out of the actual
    library), and the overlay describes the machine rather than the checkout, so
    a worktree borrows the primary checkout's.  ``--git-common-dir`` is what
    names it: worktrees share one git dir, and its parent is that checkout.
    """
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    common_dir = subprocess.run(
        ["git", "-C", str(PROJECT_DIR), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return (PROJECT_DIR / common_dir).resolve().parent / DEFAULT_CONFIG_PATH.name


def isolate_shared_resources(config: dict, genau_config: dict) -> None:
    """Strip the pair of everything a run would otherwise share with the machine.

    Rewriting *paths* gives a run its own state dir, media and logs; the hidden
    desktop gives it its own windows, input queue and process sweep.  What is
    left over is everything those two do not reach — sockets, devices and
    machine singletons, none of which have a per-desktop or per-directory
    version.  Each one is named here, so there is one place to look for what a
    run is still allowed to touch, and one place to add the next.

    Removing a resource is preferred to teaching production code a test mode:
    an empty ``broker_tray_launcher`` makes every broker path inert on its own
    terms, where a ``FUN_TIME_RUN_INTEGRATION`` branch would be a second
    behavior that only the tests exercise.
    """
    _isolate_shared_udp_ports(config, genau_config)

    # The loopback server binds a fixed TCP port, and the loser of a race for it
    # loses the surface entirely: no Tampermonkey auto-update, and RFB tab pages
    # that never learn about OmniPause.  Startup treats a busy port as a warning
    # rather than a failure, so the loss is silent.
    config["loopback_port"] = _free_tcp_port()

    # The broker is a machine singleton holding the OSR2's serial port, and it
    # outlives the sessions that use it.  Without a launcher a run cannot start
    # one, and the kill path (a machine-wide sweep matched by command line)
    # needs a *fresh* heartbeat — which a run's own state dir, where no broker
    # writes, never has.
    config["paths"]["broker_tray_launcher"] = ""

    # And the broker's own directory, which a config may pin away from the
    # session's own so the two land on the machine's one broker (a branch session
    # does — see :mod:`fun_time.branch_session`).  A run copies the config whole,
    # so it would inherit that pin: park and retract written into the live
    # broker's command file, and its heartbeat read back as the run's own — the
    # fresh heartbeat that is the one thing the kill path above waits for.
    # Dropping the key puts the whole channel back inside the run's state dir,
    # where no broker writes and none is ever started.
    config["paths"].pop("broker_state_dir", None)

    # There is one microphone.  Windows shares an input device between listeners
    # rather than refusing the second, so a run that opened it would not fail —
    # it would listen in on the user and act on what they said to their own
    # session.
    config["voice_control"]["enabled"] = False

    # fun_time stopped parsing this section when Genau moved to its own repo, so
    # it does nothing — but it is still sitting in every config file written
    # before then, naming the machine's ports, and a run copies the file whole.
    # Dead config that names a live endpoint is how the next consumer of that key
    # gets pointed at the user's session, so it goes rather than rides along.
    config.pop("genau", None)

    # The hosted Origenerator brings a second app with machine ends of its own:
    # the one ComfyUI server on its fixed port (which it would START if absent),
    # the GPU that server generates on, and the app's one database.  None has a
    # per-desktop or per-directory version, so a run hosts none — with the key
    # gone the session simply has no origenerator mode.
    config["paths"].pop("origenerator_dir", None)
    config["paths"].pop("origenerator_python_exe", None)


def apply_checkout_project_dirs(config: dict) -> None:
    """Run this checkout's ``state/genau_project_dirs.txt`` over *config*, the
    way a branch session's own config generator does.

    A run launches this checkout's code, so it has to launch this checkout's
    SIBLINGS too: a branch that leans on an unlanded ``player_core`` change —
    the satellites' HUD moved there, say — otherwise starts players that import
    a name the primary's install does not have, and every one of them dies at
    import with no window and no status file, which reads as a suite of
    timeouts rather than as a path problem.  Ordinary checkouts have no
    override file and this changes nothing.

    Applied through the production function rather than re-read here, so the
    file means in a run exactly what it means in the session the run is
    standing in for.
    """
    _apply_genau_checkout_override(config, PROJECT_DIR / STATE_DIRNAME)


def checkout_project_dirs() -> str:
    """Those same directories as a ``PYTHONPATH`` string, for a test that
    launches a child itself instead of through a session's manifest."""
    raw: dict = {}
    apply_checkout_project_dirs(raw)
    return os.pathsep.join(raw.get("paths", {}).get("genau_project_dirs", []))


def build_integration_config(tmp_path: Path) -> Path:
    real = load_config(real_config_path())
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
    apply_checkout_project_dirs(config)

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
    isolate_shared_resources(config, genau_config)
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
        raise FileNotFoundError("Could not find a main-library video with a matching funscript for integration config")
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


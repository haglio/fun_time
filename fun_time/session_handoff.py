"""Crossing between Fun Time and FunTimeVR: "enter VR", "exit VR".

Why a relay rather than one orchestrator starting the other, what the crossing
carries and what it cannot: ``docs/entering-vr.md``.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from app_support.logging_utils import configure_logging, install_exception_logging
from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.win32 import is_mutex_held, mutex_name

from fun_time.child_log import open_child_log
from fun_time.config import load_config
from fun_time.process_identity import NAMER
from fun_time.single_instance import MUTEX_ORCHESTRATOR

# No genau_project_dirs override here, unlike the two orchestrators: the dispatch
# loop imports THIS, and the session started below applies its own.

# Named, not __name__: started with `-m`, where __name__ is "__main__".
logger = logging.getLogger("fun_time.session_handoff")

HANDOFF_REQUEST_NAME = "session_handoff.txt"

# Teardown is seconds, so this only expires on a session wedged holding the mutex.
RELEASE_TIMEOUT_S = 60.0
# What both launchers allow a session to report in; matched, not second-guessed.
STARTUP_TIMEOUT_S = 45.0

_POLL_S = 0.25


@dataclass(frozen=True)
class HandoffTarget:
    """One side of the crossing: which session to start, and how it reports in."""

    key: str
    app_name: str
    module: str
    launcher_log: str
    ready_marker: str


# Log and marker names are each app's own ``.vbs``'s, pinned to the
# orchestrators' constants by tests/test_session_handoff.py.
DESKTOP = HandoffTarget(
    key="desktop",
    app_name="Fun Time",
    module="fun_time.orchestrator",
    launcher_log="launcher.log",
    ready_marker="launcher.ready",
)
VR = HandoffTarget(
    key="vr",
    app_name="FunTimeVR",
    module="fun_time_vr.orchestrator",
    launcher_log="vr_launcher.log",
    ready_marker="vr_launcher.ready",
)
TARGETS: dict[str, HandoffTarget] = {target.key: target for target in (DESKTOP, VR)}


def this_session(*, vr_main_player: bool) -> HandoffTarget:
    """Which of the two a session is, by where its main player lives."""
    return VR if vr_main_player else DESKTOP


def handoff_request_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / HANDOFF_REQUEST_NAME


def request_handoff(state_dir: str | Path, target: HandoffTarget) -> None:
    """Leave word that this session is crossing to *target*, not quitting."""
    path = handoff_request_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{target.key}\n", encoding="utf-8")


def clear_handoff_request(state_dir: str | Path) -> None:
    """Drop a request the session that wrote it never got to take."""
    handoff_request_path(state_dir).unlink(missing_ok=True)


def take_handoff_request(state_dir: str | Path) -> HandoffTarget | None:
    """The crossing this session was asked for, taken off the disk as it is read.

    Unreadable or unrecognized reads as no request, and a session with no
    request ends normally.
    """
    path = handoff_request_path(state_dir)
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    finally:
        path.unlink(missing_ok=True)
    target = TARGETS.get(key)
    if target is None and key:
        logger.warning("Ignoring unrecognized handoff request %r", key)
    return target


def hand_over_if_asked(config, session_logger: logging.Logger) -> HandoffTarget | None:
    """Spawn the relay when the session ended by crossing over; None otherwise.

    An orchestrator's last act, and detached, because the relay's first act is
    to wait for that orchestrator's mutex, which frees only once it is gone.
    """
    target = take_handoff_request(config.paths.state_dir)
    if target is None:
        return None
    session_logger.info("Session ended to cross over to %s", target.app_name)
    command = [
        str(config.paths.python_exe), "-m", "fun_time.session_handoff",
        "--target", target.key, "--config", str(config.config_path),
    ]
    # Its own log: dying on import is the one failure it cannot report itself.
    log = open_child_log(config.paths.state_dir / "session_handoff.log", command)
    subprocess.Popen(
        command, cwd=str(config.project_dir),
        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        **hidden_subprocess_kwargs(creationflags=subprocess.DETACHED_PROCESS),
    )
    return target


def wait_for_the_session_to_let_go(
    mutex: str, *, timeout_s: float = RELEASE_TIMEOUT_S, poll_s: float = _POLL_S,
) -> bool:
    """Block until nobody holds *mutex*; False when the wait ran out."""
    deadline = time.monotonic() + timeout_s
    while is_mutex_held(mutex):
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)
    return True


def start_the_session(
    target: HandoffTarget,
    *,
    python_exe: str | Path,
    project_dir: str | Path,
    state_dir: str | Path,
    config_path: str | Path,
) -> subprocess.Popen:
    """Launch *target*'s orchestrator the way its own ``.vbs`` launches it.

    The stale marker goes first: written once per launch and read as this
    launch's, the outgoing session's would vouch for one not started yet.
    """
    (Path(state_dir) / target.ready_marker).unlink(missing_ok=True)
    # Named as launch.vbs names it: a session entered by voice is as findable
    # in the task list as one entered by clicking.
    named = NAMER.named_exe(python_exe, "Orchestrator")
    command = [named, "-m", target.module, "--config", str(config_path)]
    log = open_child_log(Path(state_dir) / target.launcher_log, command)
    logger.info("Starting %s: %s", target.app_name, subprocess.list2cmdline(command))
    return subprocess.Popen(
        command, cwd=str(project_dir), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        **hidden_subprocess_kwargs(),
    )


def wait_for_the_session_to_come_up(
    target: HandoffTarget,
    session: subprocess.Popen,
    *,
    state_dir: str | Path,
    timeout_s: float = STARTUP_TIMEOUT_S,
    poll_s: float = _POLL_S,
) -> str:
    """Empty once *target* has published its startup marker, else why not.

    Watches the process too: one that died has a traceback to show now, where
    one merely wedged has nothing until the timeout.
    """
    marker = Path(state_dir) / target.ready_marker
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if marker.exists():
            return ""
        if session.poll() is not None:
            return f"{target.app_name} stopped during startup (exit code {session.returncode})."
        time.sleep(poll_s)
    return f"{target.app_name} did not finish starting within {timeout_s:.0f}s."


def last_lines_of(path: Path, count: int = 15) -> str:
    """The tail of a log, for a dialog that would otherwise send you to open it."""
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = body.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[-count:])


def report_a_failed_crossing(reason: str, log_file: Path) -> None:
    """Say the other session never came up: by now there is nothing on screen.

    Qt is imported in here so a relay that only waits and spawns never loads it.
    """
    from shared_ui.alert import Level, show_alert

    from fun_time.project_paths import PROJECT_ICON

    message = f"{reason}\n\nSee the full log at:\n{log_file}"
    tail = last_lines_of(log_file)
    if tail:
        message = f"{message}\n\nLast lines of the log:\n{tail}"
    logger.error("%s", reason)
    show_alert("Fun Time", message, level=Level.ERROR, icon=PROJECT_ICON)


def run(target: HandoffTarget, config) -> int:
    state_dir = config.paths.state_dir
    log_file = state_dir / target.launcher_log
    if not wait_for_the_session_to_let_go(
        mutex_name(MUTEX_ORCHESTRATOR, config.instance_id)
    ):
        report_a_failed_crossing(
            "The session that was running never finished shutting down, so "
            f"{target.app_name} could not take over.",
            log_file,
        )
        return 1
    session = start_the_session(
        target,
        python_exe=config.paths.python_exe,
        project_dir=config.project_dir,
        state_dir=state_dir,
        config_path=config.config_path,
    )
    reason = wait_for_the_session_to_come_up(target, session, state_dir=state_dir)
    if reason:
        report_a_failed_crossing(reason, log_file)
        return 1
    logger.info("%s is up (pid=%d)", target.app_name, session.pid)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open the other Fun Time session once this one has let go."
    )
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--config", help="Path to a JSON config file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    configure_logging(logger.name, config.log_file("session_handoff"))
    install_exception_logging(logger)
    return run(TARGETS[args.target], config)


if __name__ == "__main__":
    raise SystemExit(main())

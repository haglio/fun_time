"""Crossing between Fun Time and FunTimeVR: ``docs/entering-vr.md``."""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from app_support.logging_utils import configure_logging, install_exception_logging
from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.threading_utils import start_daemon_thread
from app_support.win32 import mutex_name

from fun_time.checkout_overrides import genau_project_kwargs
from fun_time.child_launch import no_child_log, no_console_window, open_child_log
from fun_time.config import load_config
from fun_time.overlay_progress import (
    CANCEL_ENTERING_VR,
    CANCEL_EXITING_VR,
    CANCEL_WORD,
    CANCELING,
    QUIT_WORD,
    cancel_file_for,
    parse_progress,
    what_the_flag_asks,
)
from fun_time.process_identity import NAMER
from fun_time.single_instance import (
    MUTEX_ORCHESTRATOR,
    claim_the_session,
    let_the_session_go,
)

# No genau_project_dirs override here: the dispatch loop imports THIS, and the
# session started below applies its own.  Named, not __name__: `-m` runs this.
logger = logging.getLogger("fun_time.session_handoff")

HANDOFF_REQUEST_NAME = "session_handoff.txt"

# What the monitors read while the room changes shape.
CROSSING_PROGRESS_NAME = "crossing_progress.txt"
COVER_HEARTBEAT_S = 1.0  # how often a session says the crossing is under way
COVER_STALE_S = 20.0  # and how long it may stop saying before the cover is gone
KEPT_ORIGENERATOR_NAME = "origenerator_kept.txt"

HEADSET_HOLD_NAME = "vr_headset_hold.flag"  # the headset's half, a handshake
HEADSET_HELD_NAME = "vr_headset_held.flag"
_STOP_RUNTIME = "stop_runtime"

# The first expires only on a session wedged holding the mutex; the second is
# what both launchers allow a session to report in.
RELEASE_TIMEOUT_S = 60.0
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
    crossing_message: str
    crossing_hint: str


DESKTOP = HandoffTarget(
    key="desktop",
    app_name="Fun Time",
    module="fun_time.orchestrator",
    launcher_log="launcher.log",
    ready_marker="launcher.ready",
    crossing_message="Returning to Fun Time...",
    crossing_hint=CANCEL_EXITING_VR,
)
VR = HandoffTarget(
    key="vr",
    app_name="FunTimeVR",
    module="fun_time_vr.orchestrator",
    launcher_log="vr_launcher.log",
    ready_marker="vr_launcher.ready",
    crossing_message="Entering VR...",
    crossing_hint=CANCEL_ENTERING_VR,
)
TARGETS: dict[str, HandoffTarget] = {target.key: target for target in (DESKTOP, VR)}


def this_session(*, vr_main_player: bool) -> HandoffTarget:
    """Which of the two a session is, by where its main player lives."""
    return VR if vr_main_player else DESKTOP


def handoff_request_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / HANDOFF_REQUEST_NAME


_NO_CANCEL = "no-cancel"


@dataclass(frozen=True)
class Handoff:
    target: HandoffTarget
    cancelable: bool


def request_handoff(
    state_dir: str | Path, target: HandoffTarget, *, cancelable: bool = True,
) -> None:
    """Leave word that this session is crossing, not quitting."""
    path = handoff_request_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    words = [target.key] if cancelable else [target.key, _NO_CANCEL]
    path.write_text(" ".join(words) + "\n", encoding="utf-8")


def clear_handoff_request(state_dir: str | Path) -> None:
    handoff_request_path(state_dir).unlink(missing_ok=True)


def pending_handoff(state_dir: str | Path) -> HandoffTarget | None:
    """The crossing this session is ending for, WITHOUT taking it."""
    handoff = _read_request(state_dir, take=False)
    return handoff.target if handoff is not None else None


def take_handoff_request(state_dir: str | Path) -> Handoff | None:
    """The crossing asked for, taken off the disk as it is read."""
    return _read_request(state_dir, take=True)


def _read_request(state_dir: str | Path, *, take: bool) -> Handoff | None:
    path = handoff_request_path(state_dir)
    try:
        key, *rest = path.read_text(encoding="utf-8").split() or [""]
    except OSError:
        return None
    finally:
        if take:
            path.unlink(missing_ok=True)
    target = TARGETS.get(key)
    if target is None:
        if key:
            logger.warning("Ignoring unrecognized handoff request %r", key)
        return None
    return Handoff(target, cancelable=_NO_CANCEL not in rest)


def hold_the_headset(state_dir: str | Path, *, stop_runtime: bool) -> None:
    """Cover the headset past this session; *stop_runtime* rides along."""
    (Path(state_dir) / HEADSET_HELD_NAME).unlink(missing_ok=True)
    (Path(state_dir) / HEADSET_HOLD_NAME).write_text(
        _STOP_RUNTIME if stop_runtime else "", encoding="utf-8",
    )


def headset_hold_asked(state_dir: str | Path) -> bool:
    return (Path(state_dir) / HEADSET_HOLD_NAME).exists()


def headset_hold_stops_the_runtime(state_dir: str | Path) -> bool:
    try:
        return (Path(state_dir) / HEADSET_HOLD_NAME).read_text(
            encoding="utf-8").strip() == _STOP_RUNTIME
    except OSError:
        return False


def report_the_headset_held(state_dir: str | Path) -> None:
    """The player's answer: every channel let go of, only the cover left."""
    (Path(state_dir) / HEADSET_HELD_NAME).write_text("", encoding="utf-8")


def headset_is_held(state_dir: str | Path) -> bool:
    return (Path(state_dir) / HEADSET_HELD_NAME).exists()


def release_the_headset(state_dir: str | Path) -> None:
    """Let a held player go: the room it covered for is on screen."""
    for name in (HEADSET_HOLD_NAME, HEADSET_HELD_NAME):
        (Path(state_dir) / name).unlink(missing_ok=True)


class KeptOrigenerator(NamedTuple):
    pid: int
    created_at: int
    taken_over: bool


def keep_the_origenerator(state_dir: str | Path, *, pid: int, created_at: int,
                          taken_over: bool = False) -> None:
    """Record the hosted app a crossing leaves running."""
    (Path(state_dir) / KEPT_ORIGENERATOR_NAME).write_text(
        f"{pid} {created_at} {int(taken_over)}\n", encoding="utf-8",
    )


def kept_origenerator(state_dir: str | Path) -> KeptOrigenerator | None:
    """The hosted app a crossing left running, or None."""
    try:
        pid, created_at, taken_over = (
            Path(state_dir) / KEPT_ORIGENERATOR_NAME
        ).read_text(encoding="utf-8").split()
        return KeptOrigenerator(int(pid), int(created_at), taken_over == "1")
    except (OSError, ValueError):
        return None


def forget_the_kept_origenerator(state_dir: str | Path) -> None:
    (Path(state_dir) / KEPT_ORIGENERATOR_NAME).unlink(missing_ok=True)


def crossing_progress_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / CROSSING_PROGRESS_NAME


def raise_crossing_cover(state_dir: str | Path, target: HandoffTarget) -> Path:
    """Say the room is changing over to *target*."""
    path = crossing_progress_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"1/2|{target.crossing_message}|{target.crossing_hint}\n", encoding="utf-8")
    return path


def returning_from_a_crossing(state_dir: str | Path) -> bool:
    path = crossing_progress_path(state_dir)
    try:  # a cover file left over made every LATER startup read as a return
        return time.time() - path.stat().st_mtime < COVER_STALE_S
    except OSError:
        return False


def keep_the_crossing_cover(state_dir: str | Path) -> None:
    """Say the crossing is still under way, once a second, for this process's
    life.  The cover takes ITSELF down when this stops -- its one way out that
    needs nobody else alive -- and a crossing outlasts that timeout."""
    path = crossing_progress_path(state_dir)

    def beat() -> None:
        while True:
            try:  # not past DONE: a file the cover never tidied is not a crossing
                if not parse_progress(path.read_text(encoding="utf-8")).done:
                    os.utime(path, None)
            except OSError:
                pass  # no crossing under way
            time.sleep(COVER_HEARTBEAT_S)

    start_daemon_thread(target=beat, name="crossing-cover")


_CANCELED_LINE = f"1/2|{CANCELING}\n"


def say_the_crossing_is_cancelled(state_dir: str | Path) -> None:
    path = crossing_progress_path(state_dir)
    if path.exists():
        path.write_text(_CANCELED_LINE, encoding="utf-8")


def launch_crossing_cover(
    state_dir: str | Path, target: HandoffTarget, *, project_dirs: str,
) -> subprocess.Popen:
    """Raise the monitors' crossing cover and leave it standing."""
    return _launch_transition_screen(
        raise_crossing_cover(state_dir, target), project_dirs=project_dirs,
    )


def launch_the_way_back_cover(state_dir: str | Path, *, project_dirs: str) -> subprocess.Popen:
    path = crossing_progress_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_CANCELED_LINE, encoding="utf-8")
    return _launch_transition_screen(path, project_dirs=project_dirs)


def _launch_transition_screen(progress_file: Path, *, project_dirs: str) -> subprocess.Popen:
    return subprocess.Popen([
        NAMER.named_exe(sys.executable, "TransitionScreen"),
        "-m", "fun_time.transition_screen", str(progress_file),
    ], **no_child_log(), **no_console_window(), **genau_project_kwargs(project_dirs))


def drop_crossing_cover(state_dir: str | Path) -> None:
    """Take the crossing cover down; a no-op where none is up."""
    path = crossing_progress_path(state_dir)
    if path.exists():
        path.write_text("DONE\n", encoding="utf-8")


def hand_over_if_asked(config, session_logger: logging.Logger) -> HandoffTarget | None:
    """Spawn the relay when the session ended by crossing over; None otherwise.
    An orchestrator's last act, the relay's first being to wait for its mutex."""
    handoff = take_handoff_request(config.paths.state_dir)
    if handoff is None:
        return None
    target = handoff.target
    session_logger.info("Session ended to cross over to %s", target.app_name)
    command = [
        str(config.paths.python_exe), "-m", "fun_time.session_handoff",
        "--target", target.key, "--config", str(config.config_path),
    ]
    if not handoff.cancelable:
        command.append("--no-cancel")
    # Its own log: dying on import is the one failure it cannot report itself.
    log = open_child_log(config.paths.state_dir / "session_handoff.log", command)
    subprocess.Popen(
        command, cwd=str(config.project_dir),
        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        **hidden_subprocess_kwargs(creationflags=subprocess.DETACHED_PROCESS),
    )
    return target


def a_session_is_playing(mutex: str) -> bool:
    claimed = claim_the_session(mutex)
    let_the_session_go(claimed)
    return claimed is None


def wait_for_the_session_to_let_go(
    mutex: str, *, timeout_s: float = RELEASE_TIMEOUT_S, poll_s: float = _POLL_S,
) -> bool:
    """Block until no session holds *mutex*; False when the wait ran out."""
    deadline = time.monotonic() + timeout_s
    while a_session_is_playing(mutex):
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
    cancelable: bool = True,
) -> subprocess.Popen:
    """Launch *target*'s orchestrator as its own ``.vbs`` does."""
    (Path(state_dir) / target.ready_marker).unlink(missing_ok=True)
    # Named as launch.vbs names it: a session entered by voice is as findable
    # in the task list as one entered by clicking.
    named = NAMER.named_exe(python_exe, "Orchestrator")
    command = [named, "-m", target.module, "--config", str(config_path)]
    if not cancelable:
        command.append("--no-cancel")
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
    """Empty once *target* has published its startup marker, else why not."""
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
    """Say the other session never came up; Qt loads only for this."""
    from shared_ui.alert import Level, show_alert

    from fun_time.project_paths import PROJECT_ICON

    message = f"{reason}\n\nSee the full log at:\n{log_file}"
    tail = last_lines_of(log_file)
    if tail:
        message = f"{message}\n\nLast lines of the log:\n{tail}"
    logger.error("%s", reason)
    show_alert("Fun Time", message, level=Level.ERROR, icon=PROJECT_ICON)


def _uncover_what_was_waiting(state_dir: Path, origenerator_cmd_file) -> None:
    drop_crossing_cover(state_dir)
    release_the_headset(state_dir)
    from fun_time.windows_bridge_orchestrator import let_go_of_a_kept_origenerator

    let_go_of_a_kept_origenerator(Path(state_dir), origenerator_cmd_file)


def _give_up(reason: str, log_file: Path, config) -> int:
    """Report a crossing that did not happen, and uncover what was waiting."""
    _uncover_what_was_waiting(config.paths.state_dir, config.origenerator_cmd_file)
    report_a_failed_crossing(reason, log_file)
    return 1


def run(target: HandoffTarget, config, *, cancelable: bool = True) -> int:
    state_dir = config.paths.state_dir
    keep_the_crossing_cover(state_dir)  # the middle of the three
    log_file = state_dir / target.launcher_log
    if not wait_for_the_session_to_let_go(
        mutex_name(MUTEX_ORCHESTRATOR, config.instance_id)
    ):
        return _give_up(
            "The session that was running never finished shutting down, so "
            f"{target.app_name} could not take over.",
            log_file, config,
        )
    flag = cancel_file_for(crossing_progress_path(state_dir))
    asked = what_the_flag_asks(flag) if cancelable else ""
    if asked:
        flag.unlink(missing_ok=True)
    if asked == QUIT_WORD:
        logger.info("The quit chord called the crossing off")
        _uncover_what_was_waiting(state_dir, config.origenerator_cmd_file)
        return 0
    if asked == CANCEL_WORD:
        target, cancelable = (DESKTOP if target is VR else VR), False
        log_file = state_dir / target.launcher_log
        logger.info("Esc called the crossing off; going back to %s", target.app_name)
        say_the_crossing_is_cancelled(state_dir)
    session = start_the_session(
        target,
        python_exe=config.paths.python_exe,
        project_dir=config.project_dir,
        state_dir=state_dir,
        config_path=config.config_path,
        cancelable=cancelable,
    )
    reason = wait_for_the_session_to_come_up(target, session, state_dir=state_dir)
    if reason:
        return _give_up(reason, log_file, config)
    logger.info("%s is up (pid=%d)", target.app_name, session.pid)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open the other Fun Time session once this one has let go."
    )
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--config", help="Path to a JSON config file.")
    parser.add_argument("--no-cancel", action="store_true",
                        help="The way back from a crossing Esc called off: offer no Esc.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    configure_logging(logger.name, config.log_file("session_handoff"))
    install_exception_logging(logger)
    return run(TARGETS[args.target], config, cancelable=not args.no_cancel)


if __name__ == "__main__":
    raise SystemExit(main())

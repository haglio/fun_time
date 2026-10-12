from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app_support.file_channel import write_whole
from app_support.subprocess_utils import hidden_subprocess_kwargs

from .commands import CommandFailed, run_command

FIX_NOTICES_NAME = "main_verifier_fix_notices.json"
FIX_RESTARTS_NAME = "main_verifier_fix_restarts.json"
CLAUDE_TRANSCRIPTS = Path.home() / ".claude" / "projects"
CARRY_ON = ("Your last turn ended on an error from the Claude service, not on anything you did. "
            "Carry on with the work you were given.")
RESTARTS = 3
_SETTLED = frozenset({"blocked", "done", "failed"})
_WAITS_FOR = {"input needed": "answer", "permission prompt": "permission"}
_log = logging.getLogger("main_verifier")


@dataclass(frozen=True)
class FixNotice:
    session: str
    resume: str
    cwd: str
    state: str
    title: str
    body: str


@dataclass(frozen=True)
class FixSessionSteps:
    start_again: list[dict]
    notices: list[FixNotice]


def shipped_again(incidents: Sequence[dict]) -> set[int]:
    return {incident.get("pull_request") for incident in incidents if incident.get("shipped_again_by")}


def _fix_sessions(incidents: Sequence[dict], sessions: Sequence[dict]) -> Iterator[tuple[dict, dict]]:
    by_id = {session.get("id"): session for session in sessions}
    changes_shipped_again = shipped_again(incidents)
    for incident in incidents:
        session = by_id.get(incident.get("fix_session"))
        if session and not (incident.get("taken_back_by")
                            and incident.get("pull_request") in changes_shipped_again):
            yield incident, session


def fix_session_steps(incidents: Sequence[dict], sessions: Sequence[dict], told: Mapping[str, str],
                      restarts: Mapping[str, int], died: Callable[[dict], bool]) -> FixSessionSteps:
    start_again, notices = [], []
    for incident, session in _fix_sessions(incidents, sessions):
        state, session_id = session.get("state"), session["id"]
        if state not in _SETTLED or told.get(session_id) == state:
            continue
        name = session.get("name") or f"The fix for #{incident.get('pull_request')}"
        if state == "done":
            title, body = f"{name} finished", "Click to open it in Claude Desktop and read what it did."
        elif state == "blocked" and (session.get("waitingFor") or not died(session)):
            title = f"{name} is waiting for you"
            body = (f"It needs your {_WAITS_FOR.get(session.get('waitingFor'), 'answer')}. "
                    "Click to open it in Claude Desktop.")
        elif session_id not in told and restarts.get(session_id, 0) < RESTARTS:
            start_again.append(session)
            continue
        else:
            title = f"{name} stopped on an error"
            body = ("It stopped on an error from the Claude service. Click to open it in Claude Desktop "
                    "and tell it to carry on.")
        notices.append(FixNotice(session_id, session.get("sessionId", session_id), session.get("cwd", ""), state,
                                 title, body))
    return FixSessionSteps(start_again, notices)


def background_sessions(claude: str, shell=run_command) -> list[dict]:
    answer = shell([claude, "agents", "--json", "--all"])
    try:
        return json.loads(answer)
    except ValueError:
        raise CommandFailed(f"claude agents did not answer with a list of sessions: {answer.strip()[:200]}") from None


def died_on_a_service_error(transcripts: Path, session: dict) -> bool:
    kept = next(transcripts.glob(f"*/{session.get('sessionId')}.jsonl"), None)
    if kept is None:
        return False
    last_said = {}
    for line in kept.read_text(encoding="utf-8", errors="replace").splitlines():
        with contextlib.suppress(ValueError):
            if (record := json.loads(line)).get("type") == "assistant":
                last_said = record
    return bool(last_said.get("isApiErrorMessage"))


def _start_notice(notice: FixNotice) -> None:
    subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")), "-m", "tests.integration.fix_session_notice",
                      notice.session, notice.resume, notice.cwd, notice.title, notice.body],
                     cwd=Path(__file__).resolve().parents[2], **hidden_subprocess_kwargs())


def _started_again(claude: str, session: dict, shell) -> bool:
    try:
        shell([claude, "auth", "status"])
        shell([claude, "--bg", "--resume", session["sessionId"], CARRY_ON], cwd=Path(session["cwd"]))
    except CommandFailed as why:
        _log.warning("could not start fix session %s again: %s", session["id"], why)
        return False
    return True


def _kept(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def look_after_fix_sessions(state: Path, incidents: Sequence[dict], claude: str, shell=run_command,
                            start: Callable[[FixNotice], None] = _start_notice,
                            transcripts: Path = CLAUDE_TRANSCRIPTS) -> None:
    told_path, restarts_path = state / FIX_NOTICES_NAME, state / FIX_RESTARTS_NAME
    told, restarts = _kept(told_path), _kept(restarts_path)
    steps = fix_session_steps(incidents, background_sessions(claude, shell), told, restarts,
                              lambda session: died_on_a_service_error(transcripts, session))
    started_again = [session["id"] for session in steps.start_again if _started_again(claude, session, shell)]
    for session_id in started_again:
        restarts[session_id] = restarts.get(session_id, 0) + 1
    if started_again:
        write_whole(restarts_path, json.dumps(restarts, indent=1))
    for notice in steps.notices:
        start(notice)
        told[notice.session] = notice.state
    if steps.notices:
        write_whole(told_path, json.dumps(told, indent=1))

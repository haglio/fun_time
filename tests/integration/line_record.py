from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from app_support.integration_runs import RunRecord, read_run, write_run
from app_support.win32 import process_started

KEPT_FOR = timedelta(days=14)
SEEN_EVERY_S = 15.0
RUN_RECORD_ENV = "FUN_TIME_INTEGRATION_RUN_RECORD"


def _now() -> datetime:
    return datetime.now().astimezone()


class Visit:
    def __init__(self, folder: Path, run: RunRecord, seen_every_s: float):
        self._folder = folder
        self._saving = threading.Lock()
        self._gone = threading.Event()
        self.run = run
        self.path = write_run(folder, run)
        self._being_seen = threading.Thread(target=self._keep_being_seen, args=(seen_every_s,), daemon=True,
                                            name="integration-run-seen")
        self._being_seen.start()

    @classmethod
    def join(cls, runs: Path, *, checkout: str, branch: str, session: str, what: list[str], short: bool,
             now: datetime | None = None, seen_every_s: float = SEEN_EVERY_S) -> Visit:
        joined = (now or _now()).replace(microsecond=0)
        runs.mkdir(parents=True, exist_ok=True)
        _forget_runs_before(runs, joined - KEPT_FOR)
        return cls(runs, RunRecord(checkout=checkout, branch=branch, what=what, short=short, pid=os.getpid(),
                                   joined=joined, session=session, process_started=process_started(os.getpid()),
                                   seen=joined), seen_every_s)

    def got_the_machine(self, now: datetime | None = None) -> None:
        moment = now or _now()
        self._save(got_the_machine=moment, seen=moment)

    def ended_with(self, exit_code: int, now: datetime | None = None) -> int:
        moment = now or _now()
        self._save(ended=moment, exit_code=exit_code, seen=moment)
        return exit_code

    def leave(self, now: datetime | None = None) -> None:
        self._gone.set()
        self._being_seen.join()
        if self.run.ended is None:
            moment = now or _now()
            self._save(ended=moment, seen=moment)

    def _keep_being_seen(self, every_s: float) -> None:
        while not self._gone.wait(every_s):
            with contextlib.suppress(OSError):
                self._save(seen=_now())

    def _save(self, **changes) -> None:
        with self._saving:
            self.run = replace(self.run, **changes)
            write_run(self._folder, self.run)


def require_a_run_that_holds_the_machine(environment: Mapping[str, str] = os.environ) -> None:
    named = environment.get(RUN_RECORD_ENV)
    try:
        run = read_run(Path(named)) if named else None
    except (OSError, ValueError, TypeError):
        run = None
    if run is None or run.got_the_machine is None or run.ended is not None:
        raise RuntimeError(
            "These tests run only inside a run that has waited its turn in Fun Time's integration line and "
            "holds the machine, written down where Line shows it.  Start them with:\n\n"
            "    .venv/Scripts/python.exe -m tests.integration.hidden_desktop\n")


def _forget_runs_before(runs: Path, cutoff: datetime) -> None:
    for record in runs.glob("*.json"):
        with contextlib.suppress(OSError):
            if record.stat().st_mtime < cutoff.timestamp():
                record.unlink()
